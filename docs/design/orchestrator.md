# Elephant Design — Phase 4: Orchestrator (the WAT "Agent")

**Status: DRAFT v2 (design session 2026-08-31; Goldfished ×3 → seams closed). Design-of-record for Phase 4 — `pipeline/orchestrator.py`, the WAT "Agent". Implementation is a SEPARATE gate (`writing-plans` → TDD) — no code from this doc alone. Follows the format of `docs/design/recipe-resolver.md`. Branch: `dev-feature-9-orchestrator`.**
Depends on Phases 2a–3 (the Tools this coordinator chains). Author reviews the committed doc before the build session.

> **Goldfish record (§0A.2):** two independent no-context passes (55% / 62%) both failed on the same seams — no render-layer data contract (§2 hid schema *fields* behind `…`) and an under-specified `_timed` contract; folded verbatim field lists + import paths (§2), a fully-specified `_timed` (§4.2), and an exact render contract (§4.8). A confirming pass (82%) surfaced two real defects — a `result.flipped` naming collision and missing `TrendingQuery` field types — folded into §4.8 / §2. Final verification pass: **96%, zero blocking gaps** (residual = behavior-neutral boilerplate: Typer help strings, a `from typing import Literal`, exact progress wording).

> Vision: `pantry plan` looks at what's actually in your pantry, figures out what you need, searches
> the trendy live web for it, ranks the results by **what you can cook tonight**, and (on confirm)
> adjusts the pantry. **The orchestration is deterministic; the LLM only reasons inside the tools it
> chains.** All modules under `src/pantry_pilot/`; imports absolute.

---

## 1. Context
Phases 2a–3 built the Tools: `synthesize_recipe_query` (pantry → query), `find_recipes` (source #1
Spoonacular), `find_trending` (source #2 trendy web, WITH ingredient lines), `rank_recipes`/`cook`
(pantry-fit + state mutation). Nothing yet **chains** them end-to-end. Phase 4 is that coordinator —
the WAT "Agent" (planning, routing, error handling) — realized as a **deterministic pipeline (DAG)**,
not an LLM control loop. This satisfies the WAT "Agent" role *and* Principle 10 (event-driven, hard
stop conditions, no uncontrolled loops) by construction: a DAG has no cycle to bound.

**Where the agency lives:** not in the wiring, but in the *tools* the orchestrator drives —
`find_trending` is a genuine bounded web agent (~14 turns of WebSearch/WebFetch across vetted trendy
sites); `synthesize_recipe_query` and the per-recipe ingredient `match` are real LLM reasoning. The
orchestrator's job is to *sequence* those agents deterministically. Choosing a deterministic
coordinator does not remove the agentic behavior — it decides *where* the agency is allowed to sit
(inside gated tools), keeping every project principle intact.

**Architectural decisions (locked in the 2026-08-31 design session):** see §8. In brief: deterministic
DAG (D1); trending-primary + Spoonacular fallback (D2); pantry-derived intent, flags override (D3); new
`pantry plan`, present-and-confirm (D4); in-memory `PlanResult` (D5); structured per-stage trace (D6);
content→degrade / transport→abort stop conditions with a `MAX_RANK` cap (D7); rubric + Tier-2 eval
harness (D8).

## 2. Ground truth — current code a fresh reader needs (verbatim signatures)

Tools the orchestrator chains (all inject their transport for offline tests):
```python
# services/synthesizer.py — LLM boundary #1 (tools OFF)
def synthesize_recipe_query(ingredients: list[Ingredient], *, runner: ClaudeRunner | None = None) -> RecipeQuery
class RecipeSynthesisError(Exception): ...          # content failure

# services/retrieval.py — source #1 Spoonacular (deterministic HTTP; Recipe.ingredients = None)
def find_recipes(query: RecipeQuery, *, fetcher: RecipeFetcher | None = None) -> list[Recipe]
# core/spoonacular.py: class SpoonacularError(Exception): kind  (transport, on HTTP status)

# services/trending.py — source #2 trendy web (LLM+web ON, ~120–180 s; Recipe.ingredients filled)
def find_trending(query: TrendingQuery, *, month=None, fetcher: ClaudeRunner | None = None) -> list[Recipe]
class TrendingRecipeError(Exception): kind ∈ {"llm_failed","bad_output"}   # content failure

# services/resolver.py — LLM boundary #3 (tools OFF, N serial calls) + deterministic assess/rank/cook
def rank_recipes(recipes: list[Recipe], pantry: list[Ingredient], *, runner=None) -> list[RecipeFit]
def cook(session: Session, fit: RecipeFit) -> CookResult
def _uncertain(fit: RecipeFit) -> list[IngredientMatch]      # the ⚠ subset of have
def _shopping_list(fit: RecipeFit) -> list[str]              # "restock <name>" / "buy: <line>"
class ResolutionError(Exception): kind ∈ {"llm_failed","bad_output"}
# rank_recipes ALREADY: skips ingredient-less recipes; swallows a per-recipe ResolutionError (content);
# PROPAGATES ClaudeCliError (transport). So it returns fits (maybe fewer) or raises transport-only.

# services/pantry.py
def list_ingredients(session, include_archived=False, category=None) -> list[Ingredient]

# cli.py (reuse verbatim)
def _ask_cook_choice(n: int) -> int | None    # 1-based prompt -> 0-based idx; EOF/empty/bad -> None
```

Schemas (`models/schemas.py`): `RecipeQuery(include_ingredients, exclude_ingredients, keywords,
cuisine, dish_type, max_ready_minutes)`; `TrendingQuery(theme, cuisine, meal_type, max_minutes)`;
`Recipe(id, title, …, ingredients, steps)`; `RecipeFit(recipe, have, missing)`; `CookResult`.

Transports (`core/claude_cli.py`, `core/claude_web.py`): `run_claude` (tools OFF, 120 s),
`run_claude_web` (web ON, 180 s), both `--model opus`. Transport failures → `ClaudeCliError(.kind ∈
{not_found,auth,quota,timeout,bad_output,failed})`. **Two-class error split (project-wide): transport
= `*CliError`/`SpoonacularError`; content/validation = `*Error(.kind)`.**

Session/DB (`core/database.py`): `get_session()` (context manager) and `init_db()` (the CLI callback
already calls `init_db()` before every command). Every pantry-service mutation (`cook` → `set_status`)
commits internally, so the CLI just wraps a `cook` call in `with get_session() as session:`.

**Render-relevant schema fields (verbatim — the CLI renders these; the doc must stand alone).**
```python
# models/schemas.py
class RecipeQuery(BaseModel):
    include_ingredients: list[str]
    exclude_ingredients: list[str] | None = None
    keywords: str | None = None          # SCALAR (not a list) -> maps to TrendingQuery.theme
    cuisine: str | None = None
    dish_type: str | None = None
    max_ready_minutes: int | None = None

class TrendingQuery(BaseModel):          # ALL fields optional w/ None defaults (so _to_trending_query
    theme: str | None = None             #   may pass None for any of them without a ValidationError)
    cuisine: str | None = None
    meal_type: str | None = None
    max_minutes: int | None = None

class Recipe(BaseModel):
    id: int | None = None
    title: str
    image: str | None = None
    ready_minutes: int | None = None     # (validation_alias "readyInMinutes")
    servings: int | None = None
    source_url: str | None = None        # (validation_alias "sourceUrl")
    ingredients: list[str] | None = None # source #2 fills these; None for Spoonacular (source #1)
    steps: list[str] | None = None

class IngredientMatch(BaseModel):
    recipe_ingredient: str
    pantry_name: str | None = None       # matched pantry item name (None = not stocked)
    confident: bool = True               # False -> ⚠ highlight (non-blocking)
    note: str | None = None              # optional "why", e.g. "'green onions' ≈ 'onion'?"

class RecipeFit(BaseModel):
    recipe: Recipe
    have: list[IngredientMatch]          # matched to a stocked pantry item (may include ⚠)
    missing: list[IngredientMatch]       # null match / hallucinated name / OUT

class CookResult(BaseModel):
    flipped: list[str]                   # formatted "name -> newstatus" (CLI prints as-is)
    to_update: list[str]                 # QUANTITY item names to adjust by hand

# models/tables.py — build plain objects in tests/eval fixtures (no DB needed for assess/rank)
class Ingredient(SQLModel, table=True):
    name: str                            # unique; the match target
    category: Category                   # enum
    tracking_mode: TrackingMode          # QUANTITY | PRESENCE
    base_unit: BaseUnit | None = None    # QUANTITY only (EACH | GRAM | MILLILITER)
    on_hand: int | None = None           # QUANTITY: cached ledger sum
    status: StockStatus | None = None    # PRESENCE: OUT | LOW | OK
    is_active: bool = True
```
Enums (`models/enums.py`): `Category`; `TrackingMode = QUANTITY | PRESENCE`;
`BaseUnit = EACH | GRAM | MILLILITER`; `StockStatus = OUT | LOW | OK`.

**Exact import paths (so a fresh reader never has to go hunting):**
```python
from pantry_pilot.pipeline.orchestrator import make_plan, MAX_RANK
from pantry_pilot.models.schemas import (RecipeQuery, TrendingQuery, Recipe,
    RecipeFit, IngredientMatch, CookResult, PlanResult, StageTrace)
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.synthesizer import synthesize_recipe_query, RecipeSynthesisError
from pantry_pilot.services.retrieval import find_recipes
from pantry_pilot.services.trending import find_trending, TrendingRecipeError
from pantry_pilot.services.resolver import rank_recipes, cook, _uncertain, _shopping_list
from pantry_pilot.services.pantry import list_ingredients
from pantry_pilot.core.claude_cli import ClaudeCliError
from pantry_pilot.core.spoonacular import SpoonacularError
from pantry_pilot.core.database import get_session
# CLI reuse: `_ask_cook_choice(n) -> int | None` already lives in cli.py (same module as `pantry plan`).
```

## 3. Scope & non-goals (v1)
- **In:** `pipeline/orchestrator.py` `make_plan(...)` — the deterministic DAG (synthesize → map →
  trending → rank, with Spoonacular degradation); the `RecipeQuery → TrendingQuery` map; `PlanResult`
  + `StageTrace` (§4.3); per-stage logging; a `pantry plan` CLI (present-and-confirm); offline unit
  tests; a Tier-2 eval harness + the eval rubric (§5).
- **Out (deferred):** an LLM control loop / tool-calling agent (agency stays in the tools);
  source-#1 ingredient parsing (LLM boundary #2, roadmap #3); agentic **substitution** reasoning;
  additional trendy platforms (2nd source behind the seam / TikTok — GH #12); persisting the meal
  plan; an LLM-as-judge eval tier; global pipeline timeout / retries.

## 4. Target design

### 4.1 Modules (all under `src/pantry_pilot/`)
- `pipeline/__init__.py`, `pipeline/orchestrator.py` — `make_plan`, `_to_trending_query`,
  `_timed` (stage-timing helper); the conceptual core the author hand-writes.
- `models/schemas.py` — add `StageTrace`, `PlanResult` (§4.3).
- `cli.py` — `pantry plan` command (thin; reuses `_ask_cook_choice`, `_uncertain`, `_shopping_list`).
- `tests/test_orchestrator.py`, `evals/plan_eval.py` (Tier-2 harness); extend `test_schemas.py`,
  `test_cli.py`.

### 4.2 The flow — a DAG (the orchestrator), reference pseudocode
```python
import time
MAX_RANK = 5   # bound the serial rank fan-out (Principle 10: hard cap, no unbounded work)

def _timed(name, thunk):
    """Run thunk, wall-clock it -> (value, StageTrace). Exceptions PROPAGATE (never swallowed).
    outcome/detail are INFERRED from the return value (fully specified, no caller guesswork):
      - value is a list  -> ("ok", f"{n} recipes") if non-empty else ("empty", "0 recipes")
      - value is not a list (the RecipeQuery from synthesize) -> ("ok", None)
    `seconds` is rounded to 0.1 s and is OBSERVED, never asserted in unit tests."""
    start = time.monotonic()
    value = thunk()
    seconds = round(time.monotonic() - start, 1)
    if isinstance(value, list):
        outcome, detail = ("ok", f"{len(value)} recipes") if value else ("empty", "0 recipes")
    else:
        outcome, detail = "ok", None
    return value, StageTrace(name=name, outcome=outcome, seconds=seconds, detail=detail)

def make_plan(
    pantry: list[Ingredient],
    *,
    theme: str | None = None, cuisine: str | None = None,
    meal: str | None = None, max_minutes: int | None = None,
    synth_runner=None, trending_fetcher=None, spoon_fetcher=None, rank_runner=None,
) -> PlanResult:
    stages: list[StageTrace] = []

    # Stage 1 — INTENT: pantry -> RecipeQuery (the existing synthesize boundary).
    #   RecipeSynthesisError (content) -> abort (foundational; propagates, caller -> exit 1).
    #   ClaudeCliError (transport) -> propagate.
    query, s = _timed("synthesize", lambda: synthesize_recipe_query(pantry, runner=synth_runner))
    stages.append(s)

    # Stage 2 — TRENDING (primary, rankable): map the intent, then agentic web search.
    #   Timed by hand so the trace records `seconds` EVEN on content-degrade.
    tq = _to_trending_query(query, theme, cuisine, meal, max_minutes)   # deterministic map + overrides
    start = time.monotonic()
    try:
        recipes = find_trending(tq, fetcher=trending_fetcher)          # ClaudeCliError -> propagate
        outcome, detail = ("ok", f"{len(recipes)} recipes") if recipes else ("empty", "0 recipes")
    except TrendingRecipeError:                 # content failure -> DEGRADE (not abort)
        recipes, outcome, detail = [], "degraded", "content error -> fallback"
    stages.append(StageTrace(name="trending", outcome=outcome,
                             seconds=round(time.monotonic() - start, 1), detail=detail))

    # Stage 3 — DEGRADE if nothing rankable (empty OR content-degraded): unranked Spoonacular ideas.
    if not recipes:
        ideas, s = _timed("fallback", lambda: find_recipes(query, fetcher=spoon_fetcher))
        stages.append(s)                        # SpoonacularError (transport) -> propagate
        return PlanResult(intent=query, source_used="spoonacular_fallback",
                          fits=[], ideas=ideas, stages=stages, degraded=True)

    # Stage 4 — RANK vs pantry (cap the fan-out). rank_recipes swallows per-recipe content errors
    #   and propagates ClaudeCliError (transport).
    fits, s = _timed("rank", lambda: rank_recipes(recipes[:MAX_RANK], pantry, runner=rank_runner))
    stages.append(s)
    return PlanResult(intent=query, source_used="trending",
                      fits=fits, ideas=[], stages=stages, degraded=False)
```
`make_plan` catches **only** `TrendingRecipeError` (Stage 2 → degrade). Every transport error
(`ClaudeCliError`, `SpoonacularError`) and the Stage-1 `RecipeSynthesisError` propagate uncaught to
the CLI (§4.8), which maps them to a clean one-line message + exit 1. `_timed`'s inferred
`outcome`/`detail` cover the three list-returning stages *and* the non-list `synthesize` stage, so no
stage's trace is left to guesswork.

### 4.3 New schemas
```python
class StageTrace(BaseModel):        # per-stage observability (D6)
    name: str                       # "synthesize" | "trending" | "fallback" | "rank"
    outcome: str = "ok"             # "ok" | "empty" | "degraded" | short summary
    seconds: float = 0.0            # wall-clock (observed, not asserted)
    detail: str | None = None       # set by _timed (§4.2): "4 recipes" / "0 recipes" / "content error -> fallback"

class PlanResult(BaseModel):        # the in-memory plan (D5) — returned by make_plan
    intent: RecipeQuery             # what it decided you need (the synthesized query)
    source_used: Literal["trending", "spoonacular_fallback"]
    fits: list[RecipeFit] = []      # ranked plan (empty when degraded)
    ideas: list[Recipe] = []        # unranked Spoonacular fallback ideas (empty when ranked)
    stages: list[StageTrace] = []   # per-stage trace
    degraded: bool = False          # True = fell back to unranked ideas
```

### 4.4 Intent synthesis + the deterministic `RecipeQuery → TrendingQuery` map
`synthesize_recipe_query(pantry)` returns a `RecipeQuery`; `_to_trending_query` maps it (flags win):
```python
def _to_trending_query(q, theme, cuisine, meal, max_minutes) -> TrendingQuery:
    return TrendingQuery(
        theme      = theme      or q.keywords or (q.include_ingredients[0] if q.include_ingredients else None),
        cuisine    = cuisine    or q.cuisine,
        meal_type  = meal       or q.dish_type,
        max_minutes= max_minutes if max_minutes is not None else q.max_ready_minutes,
    )
```
- `include_/exclude_ingredients` do **not** map to trending's search-term shape — they're used only by
  the Spoonacular fallback's *native* `RecipeQuery`. One synthesis serves both sources.
- **Flags override** the synthesized/mapped fields (an explicit user flag wins over the LLM's guess).
  `max_minutes` uses an `is not None` guard (0 is a legitimate override); the string fields use `or`
  (an empty string is not a meaningful override).

### 4.5 Source strategy + graceful degradation (D2)
- **Primary:** trending (has ingredient lines → rankable). **Fallback:** Spoonacular (fast, no lines →
  **unranked ideas**, no cook prompt). Fallback fires when trending returns `[]` **or** raises a
  content `TrendingRecipeError`.
- The fallback is honest about the tradeoff: the CLI says *"couldn't rank by your pantry (no trendy
  recipes with ingredients) — here are some ideas."*
- **Flag scope (intentional):** CLI flags steer the **primary** trendy search only (via
  `_to_trending_query`); the fallback calls `find_recipes(query)` on the *pantry-synthesized*
  `RecipeQuery` **without re-applying flags** — the degraded path is a best-effort safety net, not a
  precise re-query. Re-applying flags to the fallback query is a noted future refinement, not v1.

### 4.6 Stop conditions + error taxonomy + latency (D7 — Principle 10)
- **No loop → no iteration to bound.** The one fan-out (rank) is hard-capped at `MAX_RANK`.
- **Content → degrade; transport → abort** (the project's two-class split, applied to the chain):

  | Failure | Handling |
  |---|---|
  | `RecipeSynthesisError` (content, Stage 1) | abort — foundational; caller → exit 1 |
  | `TrendingRecipeError` (content) **or** empty trending | **degrade** → Spoonacular fallback |
  | `ClaudeCliError` (transport, any stage — synth/trending/rank) | **propagate** → CLI exit 1 |
  | `SpoonacularError` (transport, fallback) | propagate → CLI exit 1 |
  | per-recipe `ResolutionError` during rank | already swallowed inside `rank_recipes` |
  | empty pantry | NOT an error — plan still renders (all missing) |
  | both trending & fallback empty | NOT an error — friendly "nothing found" note |

  `make_plan` itself does **not** catch transport errors — it lets `ClaudeCliError`/`SpoonacularError`
  propagate to the CLI, which maps them to a clean one-line message + exit 1 (mirrors `cook-ideas`).
  The only `try/except` inside `make_plan` is the Stage-2 `TrendingRecipeError` → degrade.
- **No auto-retry** anywhere (undo/re-run by hand — avoids stacked corrective loops).
- **Latency budget:** worst case ≈ synth (≤120 s) + trending (≤180 s) + N≤5 × rank (≤120 s each);
  each stage is individually bounded, the pipeline is finite. No *global* timeout in v1; the
  per-stage trace + progress lines keep a multi-minute run legible.

### 4.7 Observability (D6)
`make_plan` is a single blocking call with no progress callback in v1, so the CLI prints **one**
"this can take a few minutes" status line *before* calling it (mirrors `cook-ideas`' dim status line),
and the **full per-stage trace** (`PlanResult.stages`) is available *after* the call via `-v/--verbose`
(each row `name · outcome · seconds · detail`). Live per-stage streaming (a progress callback threaded
into `make_plan`) is a deferred refinement — the trace is post-hoc in v1. Optional stdlib `logging` at
DEBUG is sugar; no new dependency.

### 4.8 CLI — `pantry plan`
```
pantry plan [-t/--theme TEXT] [-c/--cuisine TEXT] [-m/--meal TEXT] [--max-minutes N] [-v/--verbose]
```
Thin front door (mirrors `cook-ideas`):
1. `pantry = list_ingredients(session)`; close the session **before** the slow LLM calls.
2. Print a progress line; call `make_plan(pantry, theme=…, …)`. Catch `(RecipeSynthesisError,
   SpoonacularError, ClaudeCliError)` → clean one-line message + exit 1.
3. **Degraded** (`source_used="spoonacular_fallback"`): print the "couldn't rank" note + a simple
   ideas table (Title | Ready | Source); **no cook prompt** (no ingredient lines to cook against).
4. **Ranked:** the table (Title | Missing | ⚠ | Can make?) + per-recipe ⚠ notes (`_uncertain`) +
   shopping list (`_shopping_list`); then the non-blocking `_ask_cook_choice(len(fits))` → open a fresh
   session → `cook_result = cook(session, fits[i])` → print the `CookResult`'s `flipped` lines + the
   `to_update` nudge (see the render contract). EOF/empty/out-of-range → skip.
5. `-v` → also print `PlanResult.stages`. Empty everything → friendly note, no prompt.

**Render contract (exact — stated here so the doc stands alone; matches the existing `trending`/
`cook-ideas` commands).** Stack: a Typer command on the existing `app`; `rich.Console` + `rich.Table`;
errors → `raise typer.Exit(1)`. `result.fits` arrive **already sorted** by `rank_recipes` (§5.4) — the
CLI does not re-sort.
- **Degraded ideas table** — columns `Title | Ready | Source`, one row per `r in result.ideas`:
  `r.title` · `f"{r.ready_minutes} min" if r.ready_minutes else "-"` · `r.source_url or "-"`. **No cook prompt.**
- **Ranked table** — columns `Title | Missing | ⚠ | Can make?`, one row per `f in result.fits`:
  `f.recipe.title` · `str(len(f.missing))` · `str(len(_uncertain(f)))` · `"✓" if not f.missing else "✗"`.
  (**"Can make?" is derived** as `not f.missing` — it is NOT a field on `RecipeFit`.)
- **Per-recipe detail** (printed under the table, one block per `f`): for each `m in _uncertain(f)` →
  `⚠ {m.pantry_name}` + (` — {m.note}` when `m.note`); for each `line in _shopping_list(f)` → `• {line}`.
- **Post-cook:** bind `cook_result = cook(session, result.fits[i])` — a **`CookResult`**, NOT the
  `PlanResult`. Then for each `flip in cook_result.flipped` print `flip` (already `"name -> newstatus"`);
  if `cook_result.to_update`, print `Used (update by hand): ` +
  `", ".join(f"\`pantry use {n} <amt>\`" for n in cook_result.to_update)`.
- **Empty everything** (`not result.fits and not result.ideas`, e.g. both trending & fallback empty) →
  one friendly line, no prompt.
- **`-v/--verbose`** → print each `s in result.stages` as `{s.name} · {s.outcome} · {s.seconds}s · {s.detail or ''}`.

## 5. Eval criteria — good vs bad END-TO-END run (write BEFORE code, §0A.3)
Seeded pantry: `chicken`, `rice`, `soy sauce`, `garlic`, `spinach` (**OUT**), `onion`. No flags.

**GOOD (ranked path):**
1. **Intent** — `synthesize` yields a sensible `RecipeQuery` (`include_ingredients ⊆ pantry`, coherent
   `keywords`/`cuisine`).
2. **Map** — `_to_trending_query` faithfully carries `keywords→theme`, `cuisine`, `dish_type→meal_type`,
   `max_ready_minutes→max_minutes`.
3. **Trending** — ≥1 recipe, all **allow-listed**, each WITH ingredients + steps.
4. **Rank** — `fits` sorted fewest-missing (then ⚠, then title); the most-cookable recipe is #1;
   `spinach` (OUT) → *missing* → "restock spinach"; no invented `pantry_name` survives in `have`.
5. **No degradation** — `source_used="trending"`, `degraded=False`.
6. **Trace** — `stages` = synthesize/trending/rank, each `ok`, plausible timings.
7. **Cook** — one confirm → PRESENCE flip + QUANTITY nudge; **ledger untouched** (no fabricated txn).

**GOOD (degraded path):** trending returns `[]` → `source_used="spoonacular_fallback"`,
`degraded=True`, `ideas` non-empty (unranked), **no** cook prompt, clear "couldn't rank" note.

**BAD (+ where caught):**
1. Orchestrator loops / re-queries unbounded — **impossible: DAG, no loop** (design guarantee).
2. Auto-cooks without confirmation — **design forbids** (present-and-confirm).
3. A paywalled/non-allow-listed recipe reaches `fits` — **caught by `find_trending._filter`**.
4. A hallucinated `pantry_name` shows as `have` — **caught by `assess` hallucination guard** → missing.
5. `fits` not sorted fewest-missing — **deterministic → auto-graded by the Tier-2 harness**.
6. Transport failure leaves partial/committed bad state — **impossible: no mutation until explicit
   `cook`**; transport error → clean exit 1.
7. Synthesized intent contains a non-pantry ingredient — persona-steered, *not* code-caught (grading).
8. Empty pantry treated as an error — **NOT an error**; plan still renders (grading).

→ #7, #8 are "schema-valid ≠ good end-to-end" (build spike / live smoke grade them); #1–#6 are
design- or code-enforced (auto-gradable).

## 6. Failure modes / edge cases
| Situation | Handling |
|---|---|
| Trending empty **or** `TrendingRecipeError` (content) | degrade → unranked Spoonacular ideas (`degraded=True`) |
| `RecipeSynthesisError` (content, Stage 1) | abort → CLI exit 1 (can't tell what you need) |
| `ClaudeCliError` (transport, any stage) | propagate → CLI exit 1 |
| `SpoonacularError` (transport, fallback) | propagate → CLI exit 1 |
| per-recipe `ResolutionError` mid-rank | swallowed inside `rank_recipes` — rest still rank |
| >5 trending recipes | ranked set capped at `MAX_RANK=5` (bounded fan-out) |
| empty pantry | not an error — everything ranks *missing* |
| both trending & fallback empty | not an error — friendly note, no cook prompt |
| `pantry plan` run non-interactively / EOF | cook skipped (no hang); plan still printed |
| cook item archived/renamed since resolve | `get_ingredient` → None → skipped (existing `cook` behavior) |

## 7. Verification / testing (verification-left)
- **`tests/test_orchestrator.py` (offline, the backbone):** inject fake `synth_runner`/`rank_runner`
  (canned envelopes) + fake `trending_fetcher`/`spoon_fetcher` (canned recipes). Assert: the flow
  (synthesize → map → trending → rank); the `RecipeQuery→TrendingQuery` map + **flag overrides**; the
  **degradation** branch (empty trending → Spoonacular fallback, `degraded=True`, `fits==[]`,
  `ideas!=[]`, no cook prompt); a `TrendingRecipeError` also degrades; a `ClaudeCliError` **propagates**;
  the `MAX_RANK` cap; `stages` populated with the right names/outcomes (NOT timings); `RecipeSynthesisError`
  aborts. Build the pantry as plain `Ingredient` objects; `session` fixture only for the cook path.
- **`tests/test_schemas.py`:** `StageTrace`/`PlanResult` defaults + shapes.
- **`tests/test_cli.py`:** monkeypatch `make_plan` + `init_db`; `pantry plan` renders the ranked table
  vs the degraded ideas table; `-v` prints the trace; cook via `CliRunner(input="1\n")`; no-input →
  skip; error → exit 1.
- **Tier-2 eval harness — `evals/plan_eval.py` (real LLM, NOT in the default pytest run):** ~3 fixed
  scenarios against a throwaway pantry (temp `PANTRY_DB_PATH`, like the Phase-3 live smoke). For each,
  run `make_plan` for real, capture the `PlanResult`, and **auto-grade the deterministic criteria**:
  `fits` sorted by `(missing, uncertain, title)`; every `fit.recipe.source_url` allow-listed; every
  `have.pantry_name ∈` real pantry (guard held); shopping-list lines well-formed; degraded path yields
  `ideas` + no `fits`; `stages` present & labeled. **Print the subjective criteria** (the synthesized
  intent; chosen recipes + why "trendy"; match correctness) for human spot-check. Emit a per-scenario
  scorecard (criteria met / total). **No LLM-judge in v1.**
- **Build spike (read-only) — do this FIRST (R2 go/no-go, §10):** grade a real
  `synthesize_recipe_query → _to_trending_query → find_trending` chain on the §5 pantry — is the
  pantry-derived theme a sensible, genuinely-*trendy* search, not a weak Spoonacular-flavored one? Then a
  **live smoke** (`pantry plan` against a real seeded pantry) closes the loop, as every prior phase did.

## 8. Decisions (resolved 2026-08-31 design session)
- **D1** Agency = **deterministic pipeline (DAG)**; agency lives in the tools; no loop → Principle-10
  safe by construction.
- **D2** Sources = **trending-primary (rankable) + Spoonacular fallback (unranked ideas)** on
  empty/content-fail; source-#1 ingredient parsing deferred.
- **D3** Intent = **pantry-derived** via `synthesize_recipe_query` → one `RecipeQuery`; deterministic
  `RecipeQuery→TrendingQuery` map; **flags override**.
- **D4** UX = new **`pantry plan`**, **present-and-confirm** (never auto-cook); existing commands
  untouched; reuse `_ask_cook_choice`/`_uncertain`/`_shopping_list`.
- **D5** State = **in-memory `PlanResult`**; persistence deferred.
- **D6** Observability = **structured `PlanResult` + `StageTrace`** per-stage log; `-v` prints the trace.
- **D7** Stop conditions = per-stage bounded timeouts (existing 120/180 s); **`MAX_RANK` cap**;
  **content → degrade, transport → abort**; empty pantry not an error; **no auto-retry loop**.
- **D8** Evals = mandatory rubric (§5) + **Tier-2 minimal automated harness** (auto-grade deterministic
  criteria; human spot-check subjective; LLM-judge deferred).

## 9. What approval authorizes
(1) A **Goldfish pass** on this doc (fresh no-context agent implements from the doc alone; fix the DOC
on any stumble — §0A.2), iterated to ~clean. (2) Then a SEPARATE **execute** session (`writing-plans`
→ TDD) on `dev-feature-9-orchestrator`: `pipeline/orchestrator.py` (+`__init__.py`), the `PlanResult`/
`StageTrace` schemas, the `pantry plan` CLI, offline tests, the Tier-2 eval harness, **plus ADR 0011 +
SOP 05 + playbook update**. Author hand-writes the conceptual core (`make_plan` flow, the map, the
stop conditions, the eval rubric/harness) via the TODO→pseudocode→code ladder; Claude does plumbing;
fallback if crunched (Claude builds core, author reviews PR-style). **No implementation code from
design-approval alone.**

## 10. Build-time risks to resolve (execute session)
Two honest reservations surfaced in design review — neither blocks approval, but the build must treat
them consciously, not gloss them.

- **R1 — `pantry plan` vs `cook-ideas` overlap (Readability / Maintainability).** `plan` is nearly a
  *superset* of the existing `cook-ideas`: same trending → rank → cook spine, plus pantry-derived intent
  and the Spoonacular fallback. Shipping both **untouched** risks two near-identical commands and
  **duplicated table / ⚠-notes / cook-prompt rendering**. *Build-time action:* **extract the shared
  rendering into a helper both commands call**, rather than copy-pasting `cook-ideas`' body. *Open
  question (not decided in v1):* should `plan` eventually **supersede** `cook-ideas` (with `cook-ideas`
  reframed as the flags-only entry, or retired)? Flagged so the duplication is a conscious choice.

- **R2 — the intent→trending mapping is the load-bearing, UNVALIDATED assumption (Correctness).** The
  whole value-add of `plan` over `cook-ideas` is that `synthesize_recipe_query` "knows what you need" and
  drives the trendy search. But that persona was tuned for **Spoonacular `complexSearch`**, not a
  web-trend search — its `keywords` / `dish_type`, once mapped by `_to_trending_query`, may make a *weak*
  trending theme. *Build-time action:* **validate this FIRST in the build spike (§7) as a go/no-go**,
  before the full CLI is built. If the derived theme searches poorly, the pantry-derived-intent value
  collapses back toward flags-only — which reopens R1 — so this is the check that most determines whether
  the phase delivers its headline benefit.
