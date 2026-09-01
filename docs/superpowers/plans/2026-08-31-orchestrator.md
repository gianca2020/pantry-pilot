# Phase 4 Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, with review checkpoints) to implement this plan task-by-task — learning-first cadence (CLAUDE.md §0) keeps the author in the loop. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `pipeline/orchestrator.py` — the WAT "Agent" that deterministically chains the Phase 2a–3 tools (synthesize → map → trending → rank, degrading to Spoonacular ideas) — plus the `PlanResult`/`StageTrace` schemas, a `pantry plan` CLI, offline tests, a Tier-2 eval harness, and docs.

**Architecture:** A deterministic pipeline (DAG), NOT an LLM control loop — the agency lives in the *tools* it drives (`find_trending` is a bounded web agent; `synthesize`/per-recipe `match` are LLM reasoning). The orchestrator only *sequences* them, so Principle 10 (no loop to bound, `MAX_RANK` cap, content→degrade / transport→abort, no retry) holds by construction. It adds **no new LLM boundary**; every LLM output stays Pydantic-validated inside its tool.

**Tech Stack:** Python 3.12, Pydantic/SQLModel, Typer + Rich CLI, pytest (TDD, fully offline via injected fakes), `uv` for run/test, `mypy --strict` + `ruff`.

**Spec:** `docs/design/orchestrator.md` (design-of-record, Goldfished ×3 → 96% zero blocking gaps). This plan implements that doc **verbatim** — it does NOT re-open decisions D1–D8. Load-bearing sections: §2 (signatures + import paths), §4.2 (DAG + `_timed`), §4.3 (schemas), §4.4 (map), §4.6 (stop conditions), §4.8 (render contract), §5 (eval rubric), §7 (tests), §10 (R1/R2).

## Global Constraints

- **Determinism rule:** no new LLM boundary; state mutation only via the existing `cook`; DAG + `MAX_RANK=5` + content→degrade / transport→propagate + **no auto-retry**.
- **Two-class error split:** transport = `ClaudeCliError` / `SpoonacularError` (propagate → CLI exit 1); content/validation = `RecipeSynthesisError` (abort) / `TrendingRecipeError` (degrade).
- **Fully offline tests:** inject fake `synth_runner`/`rank_runner` (canned envelopes) + `trending_fetcher`/`spoon_fetcher` (canned recipes); pantry = plain `Ingredient` objects; `session` fixture only for the cook path. **Never assert `StageTrace.seconds`.**
- **All imports absolute**, under `src/pantry_pilot/`. Keep `uv run pytest`, `uv run mypy`, `uv run ruff check` green at every commit.
- **Learning split (CLAUDE.md §0):** author hand-writes the conceptual core (Task 2 `make_plan`/map/stop-conditions, Task 4 eval rubric) via the TODO→pseudocode→code ladder; Claude does plumbing (Task 1 schemas, Task 3 CLI wiring, test scaffolding, Task 5 docs). Fallback if crunched: Claude builds the core at the "code + explanation" rung for PR-style review.
- **Git:** branch `dev-feature-9-orchestrator` (already checked out; design committed there — build on top). One commit per task. Claude opens ONE PR bundling design + build; **does NOT push `main` or merge — the author merges.**

---

## File structure

| File | Responsibility |
|---|---|
| `src/pantry_pilot/pipeline/__init__.py` | New package marker (WAT "Agent" layer). |
| `src/pantry_pilot/pipeline/orchestrator.py` | **The core:** `MAX_RANK`, `_timed`, `_to_trending_query`, `make_plan`. |
| `src/pantry_pilot/models/schemas.py` | Add `StageTrace`, `PlanResult`. |
| `src/pantry_pilot/cli.py` | New `pantry plan` command; extract shared `_present_ranked` helper (R1); refactor `cook-ideas` to call it. |
| `tests/test_orchestrator.py` | New — offline backbone (the DAG, map+overrides, degrade, propagate, `MAX_RANK`, stages). |
| `tests/test_schemas.py` | Add `StageTrace`/`PlanResult` shape tests. |
| `tests/test_cli.py` | Add `pantry plan` tests (ranked table / degraded ideas / `-v` / cook / error). |
| `evals/plan_eval.py` | Tier-2 real-LLM harness (NOT in the default pytest run; `testpaths=["tests"]` excludes it). |
| `docs/adr/0011-orchestrator.md` | ADR (mirror 0010). |
| `workflows/05-orchestrator.md` | SOP (mirror 04). |
| `docs/elephant-goldfish-playbook.md` | §5 build entry + §2 Principle-10 "verified in code" + §6 roadmap flip. |

---

## Pre-flight (before Task 1)

- [ ] **Confirm baseline green** (already verified this session): `uv run pytest` → 129 passed; `uv run mypy`; `uv run ruff check`. Re-run if the tree changed.

---

### Task 1: Schemas — `StageTrace` + `PlanResult`

**Files:**
- Modify: `src/pantry_pilot/models/schemas.py` (add two models + `from typing import Literal`)
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces: `StageTrace(name: str, outcome: str = "ok", seconds: float = 0.0, detail: str | None = None)`; `PlanResult(intent: RecipeQuery, source_used: Literal["trending","spoonacular_fallback"], fits: list[RecipeFit] = [], ideas: list[Recipe] = [], stages: list[StageTrace] = [], degraded: bool = False)`. Consumed by Task 2 (`make_plan` returns `PlanResult`) and Task 3 (CLI renders it).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_schemas.py`)

```python
# add to the existing import block:
from pantry_pilot.models.schemas import PlanResult, StageTrace


def test_stage_trace_defaults() -> None:
    s = StageTrace(name="synthesize")
    assert (s.outcome, s.seconds, s.detail) == ("ok", 0.0, None)


def test_plan_result_defaults_and_shape() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    p = PlanResult(intent=q, source_used="trending")
    assert p.fits == [] and p.ideas == [] and p.stages == [] and p.degraded is False


def test_plan_result_rejects_unknown_source() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    with pytest.raises(ValidationError):
        PlanResult(intent=q, source_used="nope")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/test_schemas.py -k "stage_trace or plan_result" -v` → FAIL (ImportError: cannot import PlanResult/StageTrace).

- [ ] **Step 3: Implement** (append to `src/pantry_pilot/models/schemas.py`; add `from typing import Literal` at the top)

```python
class StageTrace(BaseModel):
    """Per-stage observability for one orchestrator run (design §4.3, D6).

    `_timed` (orchestrator) sets outcome/detail from the stage's return value; the CLI's
    `-v` prints each row. `seconds` is OBSERVED, never asserted in tests.
    """

    name: str  # "synthesize" | "trending" | "fallback" | "rank"
    outcome: str = "ok"  # "ok" | "empty" | "degraded" | short summary
    seconds: float = 0.0  # wall-clock (observed, not asserted)
    detail: str | None = None  # "4 recipes" / "0 recipes" / "content error -> fallback"


class PlanResult(BaseModel):
    """The in-memory plan returned by make_plan (design §4.3, D5) — persistence deferred."""

    intent: RecipeQuery  # the synthesized query (what it decided you need)
    source_used: Literal["trending", "spoonacular_fallback"]
    fits: list[RecipeFit] = []  # ranked plan (empty when degraded)
    ideas: list[Recipe] = []  # unranked Spoonacular fallback ideas (empty when ranked)
    stages: list[StageTrace] = []  # per-stage trace
    degraded: bool = False  # True = fell back to unranked ideas
```

- [ ] **Step 4: Run to verify green** — `uv run pytest tests/test_schemas.py -v` → PASS; then `uv run mypy` + `uv run ruff check` clean.

- [ ] **Step 5: Commit** — `git add src/pantry_pilot/models/schemas.py tests/test_schemas.py && git commit` → `feat(schemas): add StageTrace + PlanResult for the orchestrator`

---

### Task 2: Orchestrator core — `make_plan`, `_to_trending_query`, `_timed`, `MAX_RANK` (**CORE — learning ladder**)

**Files:**
- Create: `src/pantry_pilot/pipeline/__init__.py`, `src/pantry_pilot/pipeline/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes (Task 1): `PlanResult`, `StageTrace`. Existing tools (design §2, verified): `synthesize_recipe_query(pantry, *, runner)`, `find_trending(tq, *, fetcher)`, `find_recipes(query, *, fetcher)`, `rank_recipes(recipes, pantry, *, runner)`; errors `RecipeSynthesisError`, `TrendingRecipeError`, `ClaudeCliError`, `SpoonacularError`.
- Produces: `make_plan(pantry, *, theme, cuisine, meal, max_minutes, synth_runner, trending_fetcher, spoon_fetcher, rank_runner) -> PlanResult`; `MAX_RANK = 5`; `_to_trending_query(q, theme, cuisine, meal, max_minutes) -> TrendingQuery`; `_timed(name, thunk) -> tuple[T, StageTrace]`. Consumed by Task 3 (CLI) and the R2 spike / Task 4 eval.

> **Author-core note:** climb the ladder — first drop TODO comments for the DAG stages (§4.2), then pseudocode, then code. The reference below is the target. If crunched, Claude fills it at "code + explanation".

- [ ] **Step 1: Write the failing tests** — `tests/test_orchestrator.py` (offline; fakes below). The canned trending recipe uses an **allow-listed** domain (`seriouseats.com`) + `steps` + `ingredients` so it survives `find_trending._filter`.

```python
"""Offline backbone for the Phase-4 orchestrator (services injected as fakes).

RED FIRST: fails until src/pantry_pilot/pipeline/orchestrator.py exists.
"""

from typing import Any

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, ClaudeRunner
from pantry_pilot.core.spoonacular import RecipeFetcher
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import RecipeQuery, TrendingQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.pipeline.orchestrator import (
    MAX_RANK,
    _timed,
    _to_trending_query,
    make_plan,
)

# --- fakes: transports the tools inject (no claude, no network) ---

_QUERY = {
    "include_ingredients": ["chicken", "rice"],
    "keywords": "chicken dinner",
    "cuisine": "asian",
    "dish_type": "main course",
    "max_ready_minutes": 40,
}


def _synth_runner(query: dict[str, Any] = _QUERY, *, error: bool = False) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return {"is_error": True} if error else {"is_error": False, "structured_output": query}

    return _run


def _recipe(title: str) -> dict[str, object]:
    return {
        "title": title,
        "sourceUrl": f"https://seriouseats.com/{title}",
        "steps": ["step one", "step two"],
        "ingredients": ["2 lb chicken breasts", "1 cup rice"],
    }


def _trending_fetcher(recipes: list[dict[str, object]], *, error: bool = False) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return {"is_error": True} if error else {"is_error": False,
                                                 "structured_output": {"recipes": recipes}}

    return _run


def _rank_runner(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
    # one match per "- <line>" in the prompt; chicken/rice map, everything else -> null
    lines = [ln[2:] for ln in prompt.split("\n") if ln.startswith("- ")]
    matches = [
        {"recipe_ingredient": ln,
         "pantry_name": next((n for n in ("chicken", "rice") if n in ln), None)}
        for ln in lines
    ]
    return {"is_error": False, "structured_output": {"matches": matches}}


def _spoon_fetcher(results: list[dict[str, object]]) -> RecipeFetcher:
    def _run(params: dict[str, str]) -> dict[str, object]:
        return {"results": results}

    return _run


def _pantry() -> list[Ingredient]:
    return [
        Ingredient(name="chicken", category=Category.PROTEIN, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.GRAM, on_hand=800),
        Ingredient(name="rice", category=Category.STAPLE, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.GRAM, on_hand=1000),
        Ingredient(name="spinach", category=Category.GREEN, tracking_mode=TrackingMode.PRESENCE,
                   status=StockStatus.OUT),
    ]


# --- the deterministic map (pure) ---

def test_to_trending_query_maps_fields() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[],
                    keywords="ramen", cuisine="japanese", dish_type="soup", max_ready_minutes=30)
    tq = _to_trending_query(q, None, None, None, None)
    assert (tq.theme, tq.cuisine, tq.meal_type, tq.max_minutes) == ("ramen", "japanese", "soup", 30)


def test_to_trending_query_theme_falls_back_to_first_include() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])  # no keywords
    assert _to_trending_query(q, None, None, None, None).theme == "chicken"


def test_to_trending_query_flags_override() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[],
                    keywords="ramen", cuisine="japanese", dish_type="soup", max_ready_minutes=30)
    tq = _to_trending_query(q, "tacos", "mexican", "dinner", 0)  # 0 is a legit override
    assert (tq.theme, tq.cuisine, tq.meal_type, tq.max_minutes) == ("tacos", "mexican", "dinner", 0)


# --- the stage-timing helper ---

def test_timed_infers_outcome_and_detail() -> None:
    val, tr = _timed("trending", lambda: [1, 2])
    assert val == [1, 2] and tr.name == "trending" and tr.outcome == "ok" and tr.detail == "2 recipes"
    _, empty = _timed("trending", lambda: [])
    assert empty.outcome == "empty" and empty.detail == "0 recipes"
    q = RecipeQuery(include_ingredients=["x"], exclude_ingredients=[])
    _, nonlist = _timed("synthesize", lambda: q)
    assert nonlist.outcome == "ok" and nonlist.detail is None


# --- make_plan: the DAG ---

def test_make_plan_ranked_happy_path() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([_recipe("Alpha")]),
        rank_runner=_rank_runner,
    )
    assert plan.source_used == "trending" and plan.degraded is False
    assert plan.ideas == [] and len(plan.fits) == 1
    assert [s.name for s in plan.stages] == ["synthesize", "trending", "rank"]
    assert plan.stages[1].outcome == "ok"


def test_make_plan_degrades_on_empty_trending() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([]),  # nothing rankable
        spoon_fetcher=_spoon_fetcher([{"id": 1, "title": "Fallback Stew"}]),
    )
    assert plan.source_used == "spoonacular_fallback" and plan.degraded is True
    assert plan.fits == [] and len(plan.ideas) == 1
    assert [s.name for s in plan.stages] == ["synthesize", "trending", "fallback"]


def test_make_plan_degrades_on_trending_content_error() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([], error=True),  # -> TrendingRecipeError
        spoon_fetcher=_spoon_fetcher([{"id": 1, "title": "Fallback Stew"}]),
    )
    assert plan.degraded is True and plan.stages[1].outcome == "degraded"


def test_make_plan_propagates_transport_error() -> None:
    def _boom(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise ClaudeCliError("timed out", kind="timeout")

    with pytest.raises(ClaudeCliError):
        make_plan(_pantry(), synth_runner=_synth_runner(), trending_fetcher=_boom)


def test_make_plan_aborts_on_synthesis_error() -> None:
    from pantry_pilot.services.synthesizer import RecipeSynthesisError

    with pytest.raises(RecipeSynthesisError):
        make_plan(_pantry(), synth_runner=_synth_runner(error=True))


def test_make_plan_caps_rank_at_max_rank() -> None:
    calls = {"n": 0}

    def _counting_rank(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        calls["n"] += 1
        return {"is_error": False,
                "structured_output": {"matches": [{"recipe_ingredient": "2 lb chicken breasts",
                                                    "pantry_name": "chicken"}]}}

    recipes = [_recipe(f"R{i}") for i in range(MAX_RANK + 2)]  # 7 > cap
    plan = make_plan(_pantry(), synth_runner=_synth_runner(),
                     trending_fetcher=_trending_fetcher(recipes), rank_runner=_counting_rank)
    assert calls["n"] == MAX_RANK and len(plan.fits) == MAX_RANK
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/test_orchestrator.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** — `src/pantry_pilot/pipeline/__init__.py`:

```python
"""WAT 'Agent' layer — the deterministic orchestrator chaining the Phase 2a-3 tools."""
```

`src/pantry_pilot/pipeline/orchestrator.py` (reference target — mirrors design §4.2/§4.4):

```python
"""Phase 4 orchestrator — the WAT 'Agent' (deterministic DAG, no LLM control loop).

Chains the Phase 2a-3 Tools end-to-end: synthesize intent from the pantry, map it to a
trending-web search, fetch + rank by pantry-fit, degrading to unranked Spoonacular ideas when
nothing trendy is rankable. The orchestration is deterministic; the LLM only reasons inside the
tools it drives. Every tool injects its transport, so the whole chain is offline-testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from pantry_pilot.core.claude_cli import ClaudeRunner
from pantry_pilot.core.spoonacular import RecipeFetcher
from pantry_pilot.models.schemas import PlanResult, RecipeQuery, StageTrace, TrendingQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.resolver import rank_recipes
from pantry_pilot.services.retrieval import find_recipes
from pantry_pilot.services.synthesizer import synthesize_recipe_query
from pantry_pilot.services.trending import TrendingRecipeError, find_trending

MAX_RANK = 5  # bound the serial rank fan-out (Principle 10: hard cap, no unbounded work)

_T = TypeVar("_T")


def _timed(name: str, thunk: Callable[[], _T]) -> tuple[_T, StageTrace]:
    """Run thunk, wall-clock it -> (value, StageTrace). Exceptions PROPAGATE (never swallowed).

    outcome/detail are inferred from the return value: a non-empty list -> ("ok", "N recipes");
    an empty list -> ("empty", "0 recipes"); a non-list (the synthesize RecipeQuery) -> ("ok", None).
    `seconds` is rounded to 0.1 s and OBSERVED, never asserted in unit tests.
    """
    start = time.monotonic()
    value = thunk()
    seconds = round(time.monotonic() - start, 1)
    if isinstance(value, list):
        outcome, detail = ("ok", f"{len(value)} recipes") if value else ("empty", "0 recipes")
    else:
        outcome, detail = "ok", None
    return value, StageTrace(name=name, outcome=outcome, seconds=seconds, detail=detail)


def _to_trending_query(
    q: RecipeQuery,
    theme: str | None,
    cuisine: str | None,
    meal: str | None,
    max_minutes: int | None,
) -> TrendingQuery:
    """Map the synthesized RecipeQuery -> TrendingQuery; explicit CLI flags win (design §4.4).

    include_/exclude_ingredients do NOT map to trending's search shape (they serve the
    Spoonacular fallback's native RecipeQuery). max_minutes guards on `is not None` (0 is a legit
    override); string fields use `or` (an empty string is not a meaningful override).
    """
    return TrendingQuery(
        theme=theme or q.keywords or (q.include_ingredients[0] if q.include_ingredients else None),
        cuisine=cuisine or q.cuisine,
        meal_type=meal or q.dish_type,
        max_minutes=max_minutes if max_minutes is not None else q.max_ready_minutes,
    )


def make_plan(
    pantry: list[Ingredient],
    *,
    theme: str | None = None,
    cuisine: str | None = None,
    meal: str | None = None,
    max_minutes: int | None = None,
    synth_runner: ClaudeRunner | None = None,
    trending_fetcher: ClaudeRunner | None = None,
    spoon_fetcher: RecipeFetcher | None = None,
    rank_runner: ClaudeRunner | None = None,
) -> PlanResult:
    """The deterministic DAG: synthesize -> map -> trending -> rank, with Spoonacular degrade.

    Catches ONLY TrendingRecipeError (Stage 2 -> degrade). Every transport error
    (ClaudeCliError / SpoonacularError) and the Stage-1 RecipeSynthesisError PROPAGATE to the CLI.
    """
    stages: list[StageTrace] = []

    # Stage 1 - INTENT: pantry -> RecipeQuery (RecipeSynthesisError/ClaudeCliError propagate).
    query, s = _timed("synthesize", lambda: synthesize_recipe_query(pantry, runner=synth_runner))
    stages.append(s)

    # Stage 2 - TRENDING (primary, rankable). Hand-timed so `seconds` is recorded even on degrade.
    tq = _to_trending_query(query, theme, cuisine, meal, max_minutes)
    start = time.monotonic()
    try:
        recipes = find_trending(tq, fetcher=trending_fetcher)  # ClaudeCliError -> propagate
        outcome, detail = ("ok", f"{len(recipes)} recipes") if recipes else ("empty", "0 recipes")
    except TrendingRecipeError:  # content failure -> DEGRADE (not abort)
        recipes, outcome, detail = [], "degraded", "content error -> fallback"
    stages.append(StageTrace(name="trending", outcome=outcome,
                             seconds=round(time.monotonic() - start, 1), detail=detail))

    # Stage 3 - DEGRADE if nothing rankable: unranked Spoonacular ideas.
    if not recipes:
        ideas, s = _timed("fallback", lambda: find_recipes(query, fetcher=spoon_fetcher))
        stages.append(s)  # SpoonacularError (transport) -> propagate
        return PlanResult(intent=query, source_used="spoonacular_fallback",
                          fits=[], ideas=ideas, stages=stages, degraded=True)

    # Stage 4 - RANK vs pantry (cap the fan-out). rank_recipes swallows per-recipe content errors
    # and propagates ClaudeCliError (transport).
    fits, s = _timed("rank", lambda: rank_recipes(recipes[:MAX_RANK], pantry, runner=rank_runner))
    stages.append(s)
    return PlanResult(intent=query, source_used="trending",
                      fits=fits, ideas=[], stages=stages, degraded=False)
```

- [ ] **Step 4: Run to verify green** — `uv run pytest tests/test_orchestrator.py -v` → PASS; `uv run pytest` (full suite still green); `uv run mypy` + `uv run ruff check` clean.

- [ ] **Step 5: Commit** — `git add src/pantry_pilot/pipeline tests/test_orchestrator.py && git commit` → `feat(pipeline): make_plan DAG + RecipeQuery->TrendingQuery map (WAT Agent)`

---

### GATE — R2 build spike (go/no-go, real LLM, read-only) — do BEFORE Task 3

**Why (design §10 R2):** the whole value-add of `plan` over `cook-ideas` is that `synthesize_recipe_query` "knows what you need" and drives the trendy search. But that persona was tuned for Spoonacular `complexSearch`, not a web-trend search — its mapped `theme` may make a *weak* trending query. Validate this FIRST; if it searches poorly, **STOP and reconsider** (the pantry-derived-intent value collapses toward flags-only, reopening R1) before investing in the CLI/eval/docs.

- [ ] **Step 1: Write a throwaway read-only spike** — `scratchpad/r2_spike.py` (NOT committed; imports the REAL tools + the just-built `_to_trending_query`). Seeds the §5 pantry as plain `Ingredient` objects (no DB — `synthesize` takes `list[Ingredient]`). Real `claude` + web; `ANTHROPIC_API_KEY` stays scrubbed by the transport ($0 subscription).

```python
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.pipeline.orchestrator import _to_trending_query
from pantry_pilot.services.synthesizer import synthesize_recipe_query
from pantry_pilot.services.trending import find_trending

pantry = [
    Ingredient(name="chicken", category=Category.PROTEIN, tracking_mode=TrackingMode.QUANTITY,
               base_unit=BaseUnit.GRAM, on_hand=800),
    Ingredient(name="rice", category=Category.STAPLE, tracking_mode=TrackingMode.QUANTITY,
               base_unit=BaseUnit.GRAM, on_hand=1000),
    Ingredient(name="soy sauce", category=Category.STAPLE, tracking_mode=TrackingMode.PRESENCE,
               status=StockStatus.OK),
    Ingredient(name="garlic", category=Category.STAPLE, tracking_mode=TrackingMode.PRESENCE,
               status=StockStatus.OK),
    Ingredient(name="spinach", category=Category.GREEN, tracking_mode=TrackingMode.PRESENCE,
               status=StockStatus.OUT),
    Ingredient(name="onion", category=Category.GREEN, tracking_mode=TrackingMode.QUANTITY,
               base_unit=BaseUnit.EACH, on_hand=3),
]

query = synthesize_recipe_query(pantry)
tq = _to_trending_query(query, None, None, None, None)
print("INTENT:", query.model_dump())
print("MAPPED TRENDING QUERY:", tq.model_dump())
recipes = find_trending(tq)
print(f"TRENDING RESULTS ({len(recipes)}):")
for r in recipes:
    print(" -", r.title, "|", r.source_url)
```

- [ ] **Step 2: Run it** — `uv run python scratchpad/r2_spike.py` (~2–4 min; needs `claude` authenticated).
- [ ] **Step 3: Grade against §5 GOOD** — is the mapped `theme` a *genuinely-trendy* search (not a weak Spoonacular-flavored one)? Are ≥1 results allow-listed with ingredients + steps? **GO** → proceed to Task 3. **NO-GO** → STOP; surface the finding to the author and reconsider (record in the playbook regardless). Delete `scratchpad/r2_spike.py` after.

---

### Task 3: CLI — `pantry plan` + shared render helper (R1)

**Files:**
- Modify: `src/pantry_pilot/cli.py` (add imports; add `_present_ranked`; refactor `cook-ideas`; add `plan`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `make_plan` (Task 2), `PlanResult` (Task 1), existing `_ask_cook_choice`, `_uncertain`, `_shopping_list`, `cook`, `get_session`, `list_ingredients`; errors `RecipeSynthesisError`, `SpoonacularError`, `ClaudeCliError`.
- Produces: `_present_ranked(fits: list[RecipeFit]) -> None` (shared by `cook-ideas` + `plan`); the `plan` Typer command.

**R1 (design §10):** extract the shared table / ⚠-notes / shopping-list / cook-prompt rendering into `_present_ranked` that BOTH commands call — do NOT copy-paste `cook-ideas`' body. Naming `cook_result` inside the helper sidesteps the `result.flipped` collision the Goldfish flagged (§4.8).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
# extend the schemas import with PlanResult, StageTrace, CookResult; add:
from contextlib import contextmanager
from collections.abc import Iterator
from pantry_pilot.core.spoonacular import SpoonacularError


def _ranked_plan(*, cookable: bool = False) -> PlanResult:
    fit = RecipeFit(
        recipe=Recipe(title="Garlic Chicken"),
        have=[IngredientMatch(recipe_ingredient="2 lb chicken", pantry_name="chicken")],
        missing=[] if cookable else [IngredientMatch(recipe_ingredient="1 cup honey")],
    )
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    return PlanResult(intent=q, source_used="trending", fits=[fit],
                      stages=[StageTrace(name="synthesize"), StageTrace(name="trending"),
                              StageTrace(name="rank")])


def _degraded_plan() -> PlanResult:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    return PlanResult(intent=q, source_used="spoonacular_fallback", degraded=True,
                      ideas=[Recipe(title="Idea Soup", sourceUrl="https://x.com")],  # type: ignore[call-arg]
                      stages=[StageTrace(name="synthesize"), StageTrace(name="trending"),
                              StageTrace(name="fallback")])


def test_plan_prints_ranked_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan())
    result = CliRunner().invoke(app, ["plan", "-t", "cozy"])  # no stdin -> cook skipped
    assert result.exit_code == 0
    assert "Garlic Chicken" in result.output


def test_plan_degraded_prints_ideas_and_no_cook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _degraded_plan())
    result = CliRunner().invoke(app, ["plan"])
    assert result.exit_code == 0
    assert "Idea Soup" in result.output and "Cook one now" not in result.output


def test_plan_verbose_prints_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan())
    result = CliRunner().invoke(app, ["plan", "-v"])
    assert result.exit_code == 0 and "synthesize" in result.output


def test_plan_cook_path_flips_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan(cookable=True))

    @contextmanager
    def _dummy() -> Iterator[None]:
        yield None

    monkeypatch.setattr("pantry_pilot.cli.get_session", _dummy)
    monkeypatch.setattr("pantry_pilot.cli.cook",
                        lambda s, f: CookResult(flipped=["garlic -> low"], to_update=["chicken"]))
    result = CliRunner().invoke(app, ["plan"], input="1\n")
    assert result.exit_code == 0
    assert "Cooked" in result.output and "garlic -> low" in result.output


def test_plan_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> PlanResult:
        raise ClaudeCliError("not logged in", kind="auth")

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", _boom)
    result = CliRunner().invoke(app, ["plan"])
    assert result.exit_code == 1 and "Error:" in result.output
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/test_cli.py -k plan -v` → FAIL (no `plan` command / import errors).

- [ ] **Step 3: Implement** — in `src/pantry_pilot/cli.py`:
  1. Add imports: `from pantry_pilot.core.spoonacular import SpoonacularError`; `from pantry_pilot.models.schemas import PlanResult` (add to existing schemas import); `from pantry_pilot.pipeline.orchestrator import make_plan`.
  2. Extract the shared helper and refactor `cook-ideas` (replace its table→cook block, current lines ~269–298) with a call to it:

```python
def _present_ranked(fits: list[RecipeFit]) -> None:
    """Render the ranked table + per-recipe ⚠ notes + shopping list, then the non-blocking
    cook prompt (present-and-confirm). Shared by `cook-ideas` and `plan` (R1 — no copy-paste)."""
    table = Table(title="What can I cook tonight?")
    for column in ("Title", "Missing", "⚠", "Can make?"):
        table.add_column(column)
    for f in fits:
        table.add_row(f.recipe.title, str(len(f.missing)),
                      str(len(_uncertain(f))), "✓" if not f.missing else "✗")
    console.print(table)

    for f in fits:  # per-recipe detail: uncertain ⚠ matches (with why) + shopping list
        console.print(f"\n[bold]{f.recipe.title}[/bold]")
        for m in _uncertain(f):
            why = f" — {m.note}" if m.note else ""
            console.print(f"  [yellow]⚠[/yellow] {m.pantry_name}{why}")
        for line in _shopping_list(f):
            console.print(f"  [yellow]•[/yellow] {line}")

    choice = _ask_cook_choice(len(fits))  # EOF / empty / out-of-range -> None (skip)
    if choice is not None:
        with get_session() as session:
            cook_result = cook(session, fits[choice])
        console.print(f"\n[green]Cooked[/green] {fits[choice].recipe.title}.")
        for flip in cook_result.flipped:
            console.print(f"  {flip}")
        if cook_result.to_update:
            used = ", ".join(f"`pantry use {n} <amt>`" for n in cook_result.to_update)
            console.print(f"Used (update by hand): {used}")
```

`cook-ideas`' body becomes (unchanged head, then):

```python
    if not fits:  # nothing trending, or nothing with ingredients to resolve — not an error
        console.print("[yellow]No cookable ideas found[/yellow] — try a broader theme.")
        return
    _present_ranked(fits)
```

  3. Add the `plan` command:

```python
@app.command()
def plan(
    theme: Annotated[
        str | None, typer.Option("--theme", "-t", help="override the pantry-derived focus")
    ] = None,
    cuisine: Annotated[str | None, typer.Option("--cuisine", "-c", help="e.g. 'thai'")] = None,
    meal: Annotated[str | None, typer.Option("--meal", "-m", help="e.g. 'dinner'")] = None,
    max_minutes: Annotated[
        int | None, typer.Option("--max-minutes", help="cap on total cook time")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="show the per-stage trace")
    ] = False,
) -> None:
    """Plan tonight's cooking from your pantry (Phase-4 orchestrator, the WAT 'Agent').

    WHAT: reads your pantry, synthesizes what you need, searches the trendy live web for it,
          ranks the results by what you can cook tonight, and (on confirm) adjusts the pantry.
    WHY:  the orchestration is deterministic; the LLM only reasons inside the tools it chains.
          This can take a few minutes. Flags override the pantry-derived trendy search.
    """
    with get_session() as session:  # read the pantry, then close BEFORE the slow LLM calls
        pantry = list_ingredients(session)

    console.print("[dim]Planning from your pantry… this can take a few minutes.[/dim]")
    try:
        result = make_plan(pantry, theme=theme, cuisine=cuisine, meal=meal, max_minutes=max_minutes)
    except (RecipeSynthesisError, SpoonacularError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if verbose:
        for s in result.stages:
            console.print(f"[dim]{s.name} · {s.outcome} · {s.seconds}s · {s.detail or ''}[/dim]")

    if result.degraded:  # source_used == "spoonacular_fallback": unranked ideas, NO cook prompt
        console.print("[yellow]Couldn't rank by your pantry[/yellow] "
                      "(no trendy recipes with ingredients) — here are some ideas.")
        if not result.ideas:
            console.print("[yellow]Nothing found[/yellow] — try a broader theme.")
            return
        table = Table(title="Recipe ideas")
        for column in ("Title", "Ready", "Source"):
            table.add_column(column)
        for r in result.ideas:
            ready = f"{r.ready_minutes} min" if r.ready_minutes else "-"
            table.add_row(r.title, ready, r.source_url or "-")
        console.print(table)
        return

    if not result.fits:  # ranked path but nothing rankable — friendly, no prompt
        console.print("[yellow]No cookable ideas found[/yellow] — try a broader theme.")
        return

    _present_ranked(result.fits)
```

- [ ] **Step 4: Run to verify green** — `uv run pytest tests/test_cli.py -v` (new `plan` tests PASS **and** the existing `cook-ideas` tests still pass — the refactor is regression-safe); then `uv run pytest` full suite; `uv run mypy` + `uv run ruff check` clean.

- [ ] **Step 5: Commit** — `git add src/pantry_pilot/cli.py tests/test_cli.py && git commit` → `feat(cli): pantry plan + shared _present_ranked render helper (R1)`

---

### Task 4: Tier-2 eval harness — `evals/plan_eval.py` (**CORE — rubric; real LLM, not in pytest**)

**Files:**
- Create: `evals/plan_eval.py` (a script; `testpaths=["tests"]` keeps it out of `uv run pytest`)

**Interfaces:**
- Consumes: `make_plan` (real transports), `_uncertain`/`_shopping_list`, `ALLOW_DOMAINS`.
- Produces: a manual `uv run python evals/plan_eval.py` that runs ~2–3 fixed scenarios, auto-grades the deterministic §5/§7 criteria, prints the subjective ones for spot-check, and emits a per-scenario scorecard.

> **Author-core note:** this encodes the eval rubric (§5) — climb the ladder (list the deterministic checks as TODOs first). Fallback: Claude fills at "code + explanation". Keep it to 2–3 scenarios with deterministic checks; **no LLM-judge in v1.**

- [ ] **Step 1: Write the harness** — deterministic auto-grades (design §7): `fits` sorted by `(len(missing), len(_uncertain), title)`; every `fit.recipe.source_url` domain ∈ `ALLOW_DOMAINS`; every `have.pantry_name` ∈ the real pantry names; shopping-list lines start with `restock ` or `buy: `; degraded path yields `ideas` and no `fits`; `stages` present & labeled. Scenarios: (a) the §5 pantry, no flags (expect ranked); (b) the §5 pantry with a narrow flag (e.g. `theme="obscure ingredient"`) to try to force the degraded path; (c) an empty pantry (not an error — everything ranks missing). Print the synthesized intent, chosen recipes + why "trendy", and match correctness for human spot-check. Emit `criteria met / total` per scenario.

```python
"""Tier-2 eval harness for `make_plan` (real LLM + web; NOT part of `uv run pytest`).

Run manually:  uv run python evals/plan_eval.py
Auto-grades the deterministic §5/§7 criteria; prints subjective ones for human spot-check.
No LLM-judge in v1.
"""
# ... (scenarios + graders per Step 1; author writes the rubric core)
```

- [ ] **Step 2: Run it** — `uv run python evals/plan_eval.py` (real `claude`; ~minutes). Confirm each scenario's deterministic criteria pass and the printed subjective output looks GOOD vs §5. (This doubles as extra live evidence for the loop.)

- [ ] **Step 3: Commit** — `git add evals/plan_eval.py && git commit` → `test(evals): Tier-2 plan_eval harness (deterministic auto-grade + spot-check)`

---

### Task 5: Docs — ADR 0011 + workflow 05 + playbook

**Files:**
- Create: `docs/adr/0011-orchestrator.md` (mirror `0010-recipe-resolver.md`: Context / Decision D1–D8 / Error taxonomy / determinism boundary / Consequences / Goldfish outcome / Alternatives). Point at `docs/design/orchestrator.md` + `workflows/05-orchestrator.md`.
- Create: `workflows/05-orchestrator.md` (mirror `04-recipe-resolver.md`: Intent / Trigger `pantry plan […]` / Inputs / Steps (the DAG stages) / Output (`PlanResult`) / Determinism boundary / Edge-cases table (design §6) / Tests / **Build spikes + live smoke** — filled in after the R2 spike & live smoke).
- Modify: `docs/elephant-goldfish-playbook.md`:
  - §5 session log: add a **2026-08-31 — Phase 4 BUILD** entry (execute session, TDD, tasks 1–5, R2 spike result, live-smoke result, R1 render-helper decision, any TDD-caught bug — honest record).
  - §2 Principle 10 row: change "Verified in code at build" → note it IS now verified in code (DAG + `MAX_RANK` + degrade/abort + no-retry shipped).
  - §6 roadmap: flip step 5 (Phase-4 orchestrator) to done; note step 6 (end-to-end smoke) is substantially covered by `pantry plan`.

- [ ] **Step 1: Write ADR 0011 + workflow 05** (mirroring the 0010/04 structure and tone).
- [ ] **Step 2: Update the playbook** (§5 entry + §2 + §6).
- [ ] **Step 3: Commit** — `git add docs/adr/0011-orchestrator.md workflows/05-orchestrator.md docs/elephant-goldfish-playbook.md && git commit` → `docs: ADR 0011 + SOP 05 + playbook for the Phase-4 orchestrator`

---

### Close the loop — live smoke (design §7; like every prior phase)

- [ ] Seed a **throwaway** pantry (real dev DB untouched) via a temp DB path, then run the real `pantry plan`:

```bash
export PANTRY_DB_PATH="$(mktemp -d)/smoke.db"   # engine reads settings.db_path at import; dev DB safe
uv run pantry add chicken -c protein -m quantity -u gram -a 800
uv run pantry add rice -c staple -m quantity -u gram -a 1000
uv run pantry add "soy sauce" -c staple -m presence -s ok
uv run pantry add garlic -c staple -m presence -s ok
uv run pantry add spinach -c green -m presence -s out
uv run pantry add onion -c green -m quantity -u each -a 3
uv run pantry plan --verbose        # grade the ranked path vs §5 GOOD
# force-degrade check (obscure theme -> Spoonacular ideas, no cook prompt):
uv run pantry plan --theme "asdfqwer nonexistent dish"
```

- [ ] Grade both paths against §5 GOOD (ranked: sensible intent, allow-listed recipes with ingredients, `fits` fewest-missing, spinach→restock, trace `ok`; a confirmed cook flips a PRESENCE item + reports the QUANTITY nudge, **ledger untouched**). Record the build-spike + live-smoke outcomes in the playbook §5 and workflow 05 (loop closed). Category/unit/status enum spellings: verify against `models/enums.py` before seeding.

---

### PR (author merges)

- [ ] `git push` the branch; open ONE PR (`gh pr create --base main`) bundling the committed design + this build. Body summarizes: the DAG, R1 (shared render helper), the R2 go/no-go outcome, the offline test backbone, and the live-smoke result. **Do NOT push `main` or merge — the author merges.**

---

## Self-Review (against the spec)

- **§3 scope:** orchestrator (Task 2), map (Task 2), `PlanResult`/`StageTrace` (Task 1), per-stage trace (Task 1/2), `pantry plan` present-and-confirm (Task 3), offline tests (Tasks 1–3), Tier-2 harness + rubric (Task 4) — all covered. Non-goals (LLM loop, source-#1 parsing, substitution, persistence, LLM-judge, global timeout) — not built. ✓
- **§4.2/§4.4/§4.6:** DAG, `_timed` (list vs non-list inference), `_to_trending_query` (flags-win, `is not None` for max_minutes), degrade-only-on-`TrendingRecipeError`, transport propagation, `MAX_RANK` cap — all in Task 2 code + tests. ✓
- **§4.8 render contract:** ranked table (Missing / ⚠ / derived "Can make?" = `not f.missing`), per-recipe ⚠+shopping, degraded ideas table (Title/Ready/Source) with no cook prompt, post-cook `cook_result.flipped`/`to_update`, empty-everything friendly line, `-v` trace — Task 3. The `cook_result` naming avoids the flagged `result.flipped` collision. ✓
- **§10 R1/R2:** R2 is an explicit go/no-go GATE before Task 3; R1 is the extracted `_present_ranked` helper both commands call. ✓
- **Type consistency:** `make_plan` kwargs and the four fake injections match the tool signatures (`synth_runner`/`rank_runner`/`trending_fetcher` = `ClaudeRunner`; `spoon_fetcher` = `RecipeFetcher`). `_present_ranked(fits)`, `_to_trending_query(q, theme, cuisine, meal, max_minutes)`, `_timed(name, thunk)` used identically across tasks. ✓
- **Placeholder scan:** every code/test step carries real code; the only intentionally-sketched body is `evals/plan_eval.py` Step 1 (author-core rubric, filled at execution). ✓
