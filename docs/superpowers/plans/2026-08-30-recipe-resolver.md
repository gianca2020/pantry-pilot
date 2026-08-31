# Phase 3 Recipe Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Learning split (CLAUDE.md §0):** the conceptual-core tasks (2–5) are the author's to hand-write via the TODO→pseudocode→code ladder; reference code below is for review/grading, not pasting. Plumbing tasks (1, 6, 7) Claude writes; author reviews PR-style.

**Goal:** Rank fetched recipes by what you can cook tonight — an LLM matches each recipe ingredient line to a pantry item, deterministic code decides in-stock + ranks by fewest-missing, and `cook` adjusts the pantry (presence-flip + nudge).

**Architecture:** One non-deterministic step — `resolve_recipe` (single-shot `run_claude`, tools OFF, schema-validated). Everything else deterministic: `assess` (hallucination guard + stock check), `rank_recipes`, `cook`. A thin `pantry cook-ideas` CLI wires `find_trending → rank_recipes → print → optional cook`. Every test is offline (injected fake `ClaudeRunner` + saved fixture / plain `Ingredient` objects).

**Tech Stack:** Python 3.12, Pydantic v2, SQLModel, Typer + rich, `subprocess`→`claude` CLI, pytest (`monkeypatch`, `CliRunner`), ruff, mypy `--strict`, uv.

**Spec:** `docs/design/recipe-resolver.md` (Goldfished ×3 → 95%, v2 — read it alongside this plan).

## Global Constraints
- All modules under `src/pantry_pilot/`; **absolute imports**. Python `>=3.12`.
- **Determinism rule:** the LLM only *matches*; all state/stock/ranking/mutation is deterministic and Pydantic-validated at the boundary.
- **Verification after every task:** `uv run pytest` · `uv run ruff check src tests` · `uv run mypy` — all green. ruff line-length 100; mypy `--strict` over src+tests.
- **Errors — two-class split:** transport → reuse `ClaudeCliError` (`.kind`); content/validation → new `ResolutionError` (`.kind ∈ {"llm_failed","bad_output"}`). Empty pantry / all-missing / no-ingredients → NOT errors.
- **Ledger-honest cook:** PRESENCE steps down (OK→LOW→OUT), deduped by `pantry_name`; QUANTITY items reported in `to_update`, **never** auto-deducted (no `record_transaction` in v1).
- Branch: `dev-feature-8-recipe-resolver` (already cut; design committed). Small commits, PR at end.

---

## File Structure
**Create:** `src/pantry_pilot/services/resolver.py` (the WAT tool); `tests/test_resolver.py`; `tests/fixtures/recipe_resolution.json`.
**Modify:** `src/pantry_pilot/models/schemas.py` (add 4 models); `src/pantry_pilot/cli.py` (add `cook-ideas`); `tests/test_schemas.py`, `tests/test_cli.py`.
**Docs (Task 7):** `docs/adr/0010-recipe-resolver.md`, `workflows/04-recipe-resolver.md`, `docs/elephant-goldfish-playbook.md`.

---

## Task 1: Schemas (plumbing)

**Files:** Modify `src/pantry_pilot/models/schemas.py`; Test `tests/test_schemas.py`.

**Interfaces — Produces:**
```python
class IngredientMatch(BaseModel):
    recipe_ingredient: str
    pantry_name: str | None = None
    confident: bool = True
    note: str | None = None

class RecipeResolution(BaseModel):
    matches: list[IngredientMatch]

class RecipeFit(BaseModel):
    recipe: Recipe
    have: list[IngredientMatch]
    missing: list[IngredientMatch]

class CookResult(BaseModel):
    flipped: list[str]
    to_update: list[str]
```

- [ ] **Step 1: Failing tests** (append to `tests/test_schemas.py`; add the names to its import):
```python
def test_ingredient_match_defaults() -> None:
    m = IngredientMatch(recipe_ingredient="2 lb chicken")
    assert m.pantry_name is None and m.confident is True and m.note is None

def test_recipe_resolution_validates_list() -> None:
    r = RecipeResolution.model_validate(
        {"matches": [{"recipe_ingredient": "salt", "pantry_name": "salt"}]}
    )
    assert r.matches[0].pantry_name == "salt" and r.matches[0].confident is True

def test_recipe_fit_and_cook_result_shapes() -> None:
    fit = RecipeFit(recipe=Recipe(title="X"), have=[], missing=[])
    assert fit.have == [] and fit.missing == []
    assert CookResult(flipped=["spinach -> low"], to_update=["chicken"]).to_update == ["chicken"]
```
- [ ] **Step 2: Run — expect FAIL** (`ImportError`): `uv run pytest tests/test_schemas.py -q`
- [ ] **Step 3: Implement** the four models in `schemas.py` (reference in Interfaces block).
- [ ] **Step 4: Run — expect PASS**; then full gate.
- [ ] **Step 5: Commit** `feat(schemas): add resolver I/O models (IngredientMatch/RecipeResolution/RecipeFit/CookResult)`.

---

## Task 2: The LLM boundary — persona, prompt, parse gate, `resolve_recipe` (AUTHOR core)

**Files:** Create `src/pantry_pilot/services/resolver.py`, `tests/fixtures/recipe_resolution.json`; Test `tests/test_resolver.py`.

**Interfaces:**
- Consumes: `run_claude`, `ClaudeRunner`, `ClaudeCliError` (`pantry_pilot.core.claude_cli`); `Recipe`, `RecipeResolution`, `IngredientMatch` (Task 1).
- Produces: `ResolutionError`, `ResolutionErrorKind`, `_resolver_persona()`, `_to_resolution_prompt(recipe, pantry_names)`, `_parse_resolution(envelope) -> list[IngredientMatch]`, `resolve_recipe(recipe, pantry_names, *, runner=None) -> list[IngredientMatch]`.

- [ ] **Step 1: Create fixture** `tests/fixtures/recipe_resolution.json` — the inner `structured_output` (a `RecipeResolution`); covers have / null-missing / OUT-missing / hallucinated-missing / uncertain-have:
```json
{
  "matches": [
    {"recipe_ingredient": "2 lb chicken breasts, cubed", "pantry_name": "chicken", "confident": true},
    {"recipe_ingredient": "1/4 cup low sodium soy sauce", "pantry_name": "soy sauce", "confident": true},
    {"recipe_ingredient": "1/3 cup honey", "pantry_name": null, "confident": true},
    {"recipe_ingredient": "3 cloves garlic, minced", "pantry_name": "garlic", "confident": true},
    {"recipe_ingredient": "1 bunch spinach", "pantry_name": "spinach", "confident": true},
    {"recipe_ingredient": "2 green onions, sliced", "pantry_name": "onion", "confident": false, "note": "green onions ~ onion?"},
    {"recipe_ingredient": "honey glaze", "pantry_name": "honey", "confident": false, "note": "not sure honey is stocked"}
  ]
}
```

- [ ] **Step 2: Failing tests** (`tests/test_resolver.py` — shared header + this task's tests):
```python
import json
from pathlib import Path

import pytest

from pantry_pilot.core.claude_cli import ClaudeRunner
from pantry_pilot.models.schemas import Recipe
from pantry_pilot.services.resolver import (
    ResolutionError, _parse_resolution, _resolver_persona, _to_resolution_prompt, resolve_recipe,
)

_FIX = Path(__file__).parent / "fixtures" / "recipe_resolution.json"


def _inner() -> dict[str, object]:
    body = json.loads(_FIX.read_text())
    assert isinstance(body, dict)
    return body


def _runner_returning(env: dict[str, object]) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return env
    return _run


def test_prompt_lists_pantry_names_and_ingredient_lines() -> None:
    recipe = Recipe(title="Honey Garlic Chicken", ingredients=["2 lb chicken", "1/3 cup honey"])
    prompt = _to_resolution_prompt(recipe, ["chicken", "soy sauce"])
    assert "chicken" in prompt and "soy sauce" in prompt
    assert "2 lb chicken" in prompt and "1/3 cup honey" in prompt


def test_persona_states_the_match_rules() -> None:
    p = _resolver_persona().lower()
    assert "verbatim" in p or "never invent" in p
    assert "null" in p
    assert "confident" in p


def test_parse_reads_structured_output() -> None:
    matches = _parse_resolution({"is_error": False, "structured_output": _inner()})
    assert len(matches) == 7
    assert matches[0].pantry_name == "chicken"
    assert matches[5].confident is False


def test_parse_falls_back_to_result_string() -> None:
    matches = _parse_resolution({"is_error": False, "result": json.dumps(_inner())})
    assert len(matches) == 7


def test_parse_is_error_raises_llm_failed() -> None:
    with pytest.raises(ResolutionError) as exc:
        _parse_resolution({"is_error": True, "structured_output": _inner()})
    assert exc.value.kind == "llm_failed"


def test_parse_non_json_result_raises_bad_output() -> None:
    with pytest.raises(ResolutionError) as exc:
        _parse_resolution({"is_error": False, "result": "not json"})
    assert exc.value.kind == "bad_output"


def test_parse_unschematic_payload_raises_bad_output() -> None:
    with pytest.raises(ResolutionError) as exc:
        _parse_resolution({"is_error": False, "structured_output": {}})  # missing 'matches'
    assert exc.value.kind == "bad_output"


def test_resolve_recipe_wires_runner_and_returns_matches() -> None:
    recipe = Recipe(title="X", ingredients=["2 lb chicken"])
    env = {"is_error": False, "structured_output": _inner()}
    matches = resolve_recipe(recipe, ["chicken"], runner=_runner_returning(env))
    assert len(matches) == 7
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError`).
- [ ] **Step 4: Ladder → author implements** `resolver.py` (reference, doc §4.2/§4.6):
```python
from __future__ import annotations

import json
from typing import Literal

from pydantic import ValidationError

from pantry_pilot.core.claude_cli import ClaudeRunner, run_claude
from pantry_pilot.models.schemas import IngredientMatch, Recipe, RecipeResolution

ResolutionErrorKind = Literal["llm_failed", "bad_output"]


class ResolutionError(Exception):
    def __init__(self, message: str, *, kind: ResolutionErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def _to_resolution_prompt(recipe: Recipe, pantry_names: list[str]) -> str:
    pantry = ", ".join(pantry_names) or "(pantry is empty)"
    lines = "\n".join(f"- {line}" for line in (recipe.ingredients or []))
    return (
        f"PANTRY ITEMS (match ONLY against these exact names):\n{pantry}\n\n"
        f"RECIPE: {recipe.title}\n"
        f"RECIPE INGREDIENTS (return one match per line, in order):\n{lines}"
    )


def _resolver_persona() -> str:
    return (
        "Match each recipe ingredient line to exactly one pantry item name from the provided list, "
        "or null if the pantry has nothing suitable. Use the pantry names verbatim — NEVER invent a "
        "name that isn't in the list. When the two are similar but not clearly the same, set confident "
        "to false and say why in note. Return exactly one entry per recipe ingredient line."
    )


def _parse_resolution(envelope: dict[str, object]) -> list[IngredientMatch]:
    if envelope.get("is_error"):
        raise ResolutionError("resolver reported an error", kind="llm_failed")
    payload = envelope.get("structured_output")
    if payload is None:
        raw = envelope.get("result")
        if not isinstance(raw, str):
            raise ResolutionError("no usable resolution payload", kind="bad_output")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResolutionError("resolution result was not JSON", kind="bad_output") from exc
    try:
        resolution = RecipeResolution.model_validate(payload)
    except ValidationError as exc:
        raise ResolutionError("resolution payload failed validation", kind="bad_output") from exc
    return resolution.matches


def resolve_recipe(
    recipe: Recipe, pantry_names: list[str], *, runner: ClaudeRunner | None = None
) -> list[IngredientMatch]:
    runner = runner or run_claude
    prompt = _to_resolution_prompt(recipe, pantry_names)
    envelope = runner(prompt, RecipeResolution.model_json_schema(), system=_resolver_persona())
    return _parse_resolution(envelope)
```
- [ ] **Step 5: Run — expect PASS**; full gate.
- [ ] **Step 6: Commit** `feat(resolver): LLM match boundary — persona, prompt, parse gate, resolve_recipe`.

---

## Task 3: `assess` + `_in_stock` — the deterministic gate (AUTHOR core)

**Files:** Modify `src/pantry_pilot/services/resolver.py`; Test `tests/test_resolver.py`.

**Interfaces:** Consumes `Ingredient` (`models.tables`), `TrackingMode`/`StockStatus` (`models.enums`), `IngredientMatch`/`RecipeFit` (Task 1). Produces `_in_stock(item) -> bool`, `assess(recipe, matches, pantry) -> RecipeFit`.

- [ ] **Step 1: Failing tests** (append to `tests/test_resolver.py`; add imports):
```python
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.resolver import assess


def _pantry() -> list[Ingredient]:
    return [
        Ingredient(name="chicken", category=Category.PROTEIN, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.GRAM, on_hand=800),
        Ingredient(name="soy sauce", category=Category.STAPLE, tracking_mode=TrackingMode.PRESENCE,
                   status=StockStatus.OK),
        Ingredient(name="garlic", category=Category.STAPLE, tracking_mode=TrackingMode.PRESENCE,
                   status=StockStatus.LOW),
        Ingredient(name="spinach", category=Category.GREEN, tracking_mode=TrackingMode.PRESENCE,
                   status=StockStatus.OUT),
        Ingredient(name="onion", category=Category.GREEN, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.EACH, on_hand=3),
    ]


def test_assess_splits_have_and_missing() -> None:
    matches = _parse_resolution({"is_error": False, "structured_output": _inner()})
    fit = assess(Recipe(title="X"), matches, _pantry())
    have = {m.pantry_name for m in fit.have}
    assert have == {"chicken", "soy sauce", "garlic", "onion"}   # stocked
    # missing: honey(null), spinach(OUT), honey-glaze(hallucinated name not in pantry)
    assert len(fit.missing) == 3


def test_assess_hallucinated_name_goes_missing() -> None:
    m = IngredientMatch(recipe_ingredient="honey glaze", pantry_name="honey")  # not in pantry
    fit = assess(Recipe(title="X"), [m], _pantry())
    assert fit.have == [] and fit.missing == [m]


def test_assess_out_of_stock_goes_missing() -> None:
    m = IngredientMatch(recipe_ingredient="1 bunch spinach", pantry_name="spinach")  # row is OUT
    fit = assess(Recipe(title="X"), [m], _pantry())
    assert fit.missing == [m]


def test_assess_keeps_uncertain_match_in_have() -> None:
    m = IngredientMatch(recipe_ingredient="2 green onions", pantry_name="onion", confident=False)
    fit = assess(Recipe(title="X"), [m], _pantry())
    assert fit.have == [m]
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Ladder → author implements** (reference, doc §4.4):
```python
from pantry_pilot.models.enums import StockStatus, TrackingMode
from pantry_pilot.models.schemas import RecipeFit
from pantry_pilot.models.tables import Ingredient


def _in_stock(item: Ingredient) -> bool:
    if item.tracking_mode == TrackingMode.QUANTITY:
        return (item.on_hand or 0) > 0
    return item.status != StockStatus.OUT


def assess(recipe: Recipe, matches: list[IngredientMatch], pantry: list[Ingredient]) -> RecipeFit:
    by_name = {i.name: i for i in pantry}
    have: list[IngredientMatch] = []
    missing: list[IngredientMatch] = []
    for m in matches:
        item = by_name.get(m.pantry_name) if m.pantry_name else None
        if item is not None and _in_stock(item):
            have.append(m)
        else:
            missing.append(m)
    return RecipeFit(recipe=recipe, have=have, missing=missing)
```
- [ ] **Step 4: Run — expect PASS**; full gate.
- [ ] **Step 5: Commit** `feat(resolver): assess gate — hallucination guard + stock check`.

---

## Task 4: `rank_recipes` + `_uncertain` + `_shopping_list` + `_step_down` (AUTHOR core)

**Files:** Modify `src/pantry_pilot/services/resolver.py`; Test `tests/test_resolver.py`.

**Interfaces:** Produces `_uncertain(fit)`, `_shopping_list(fit) -> list[str]`, `_step_down(status) -> StockStatus`, `rank_recipes(recipes, pantry, *, runner=None) -> list[RecipeFit]`.

- [ ] **Step 1: Failing tests** (append):
```python
from pantry_pilot.services.resolver import _shopping_list, _step_down, rank_recipes


def test_shopping_list_restock_vs_buy() -> None:
    fit = assess(Recipe(title="X"),
                 _parse_resolution({"is_error": False, "structured_output": _inner()}), _pantry())
    lines = _shopping_list(fit)
    assert "restock spinach" in lines            # stocked but OUT
    assert "buy: 1/3 cup honey" in lines         # null match -> not stocked
    assert "buy: honey glaze" in lines           # hallucinated name -> not stocked


def test_step_down() -> None:
    assert _step_down(StockStatus.OK) == StockStatus.LOW
    assert _step_down(StockStatus.LOW) == StockStatus.OUT
    assert _step_down(StockStatus.OUT) == StockStatus.OUT


def test_rank_orders_by_fewest_missing_and_skips_ingredient_less() -> None:
    full = Recipe(title="All I have", ingredients=["2 lb chicken breasts, cubed"])
    partial = Recipe(title="Missing one", ingredients=["2 lb chicken breasts, cubed", "1/3 cup honey"])
    no_ingredients = Recipe(title="No lines")  # ingredients is None -> skipped

    def _runner(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        # Return one match per line, matched by simple substring against the prompt's pantry list.
        lines = [ln[2:] for ln in prompt.split("\n") if ln.startswith("- ")]
        matches = []
        for ln in lines:
            name = "chicken" if "chicken" in ln else None
            matches.append({"recipe_ingredient": ln, "pantry_name": name})
        return {"is_error": False, "structured_output": {"matches": matches}}

    ranked = rank_recipes([partial, full, no_ingredients], _pantry(), runner=_runner)
    assert [f.recipe.title for f in ranked] == ["All I have", "Missing one"]  # no_ingredients skipped


def test_rank_skips_a_recipe_that_fails_resolution() -> None:
    good = Recipe(title="Good", ingredients=["2 lb chicken breasts, cubed"])
    bad = Recipe(title="Bad", ingredients=["x"])
    calls = {"n": 0}

    def _runner(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"is_error": True}                     # first recipe -> ResolutionError -> skipped
        return {"is_error": False, "structured_output":
                {"matches": [{"recipe_ingredient": "2 lb chicken breasts, cubed", "pantry_name": "chicken"}]}}

    ranked = rank_recipes([bad, good], _pantry(), runner=_runner)
    assert [f.recipe.title for f in ranked] == ["Good"]
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Ladder → author implements** (reference, doc §4.5):
```python
def _uncertain(fit: RecipeFit) -> list[IngredientMatch]:
    return [m for m in fit.have if not m.confident]


def _shopping_list(fit: RecipeFit) -> list[str]:
    return [
        f"restock {m.pantry_name}" if m.pantry_name else f"buy: {m.recipe_ingredient}"
        for m in fit.missing
    ]


def rank_recipes(
    recipes: list[Recipe], pantry: list[Ingredient], *, runner: ClaudeRunner | None = None
) -> list[RecipeFit]:
    fits: list[RecipeFit] = []
    for r in recipes:
        if not r.ingredients:
            continue
        try:
            matches = resolve_recipe(r, [i.name for i in pantry], runner=runner)
        except ResolutionError:
            continue
        fits.append(assess(r, matches, pantry))
    fits.sort(key=lambda f: (len(f.missing), len(_uncertain(f)), f.recipe.title))
    return fits


def _step_down(status: StockStatus) -> StockStatus:
    return {StockStatus.OK: StockStatus.LOW, StockStatus.LOW: StockStatus.OUT}.get(status, StockStatus.OUT)
```
- [ ] **Step 4: Run — expect PASS**; full gate.
- [ ] **Step 5: Commit** `feat(resolver): rank_recipes (fewest-missing, skip-on-error) + shopping list`.

---

## Task 5: `cook` — state mutation (AUTHOR core)

**Files:** Modify `src/pantry_pilot/services/resolver.py`; Test `tests/test_resolver.py`.

**Interfaces:** Consumes `get_ingredient`, `set_status` (`services.pantry`), `Session` (`sqlmodel`), `PantryTransaction` (for the "no txn" assertion). Produces `cook(session, fit) -> CookResult`.

- [ ] **Step 1: Failing test** (append; uses the shared `session` fixture from `tests/conftest.py`):
```python
from sqlmodel import Session, select

from pantry_pilot.models.schemas import CookResult
from pantry_pilot.models.tables import PantryTransaction
from pantry_pilot.services.pantry import add_ingredient
from pantry_pilot.services.resolver import cook


def test_cook_flips_presence_and_nudges_quantity_without_touching_ledger(session: Session) -> None:
    spinach = add_ingredient(session, name="spinach", category=Category.GREEN,
                             tracking_mode=TrackingMode.PRESENCE, status=StockStatus.OK)
    add_ingredient(session, name="chicken", category=Category.PROTEIN,
                   tracking_mode=TrackingMode.QUANTITY, base_unit=BaseUnit.GRAM)
    fit = RecipeFit(
        recipe=Recipe(title="X"),
        have=[
            IngredientMatch(recipe_ingredient="spinach", pantry_name="spinach"),
            IngredientMatch(recipe_ingredient="more spinach", pantry_name="spinach"),  # dup -> once
            IngredientMatch(recipe_ingredient="2 lb chicken", pantry_name="chicken"),
        ],
        missing=[],
    )
    result: CookResult = cook(session, fit)
    assert result.flipped == ["spinach -> low"]        # PRESENCE stepped down once (deduped)
    assert result.to_update == ["chicken"]             # QUANTITY reported, not deducted
    session.refresh(spinach)
    assert spinach.status == StockStatus.LOW
    assert session.exec(select(PantryTransaction)).all() == []   # ledger untouched (D4)
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Ladder → author implements** (reference, doc §4.5):
```python
from sqlmodel import Session

from pantry_pilot.models.schemas import CookResult
from pantry_pilot.services.pantry import get_ingredient, set_status


def cook(session: Session, fit: RecipeFit) -> CookResult:
    flipped: list[str] = []
    to_update: list[str] = []
    seen: set[str] = set()
    for m in fit.have:
        if not m.pantry_name or m.pantry_name in seen:
            continue
        seen.add(m.pantry_name)
        item = get_ingredient(session, m.pantry_name)
        if item is None:
            continue
        if item.tracking_mode == TrackingMode.PRESENCE:
            new = _step_down(item.status or StockStatus.OK)
            set_status(session, item, new)
            flipped.append(f"{item.name} -> {new.value}")
        else:
            to_update.append(item.name)
    return CookResult(flipped=flipped, to_update=to_update)
```
- [ ] **Step 4: Run — expect PASS**; full gate.
- [ ] **Step 5: Commit** `feat(resolver): ledger-honest cook (presence-flip + nudge, deduped)`.

---

## Task 6: CLI — `pantry cook-ideas` (plumbing)

**Files:** Modify `src/pantry_pilot/cli.py`; Test `tests/test_cli.py`.

**Interfaces:** Consumes `find_trending`/`TrendingQuery` (`services.trending`/`models.schemas`), `rank_recipes`/`cook`/`_uncertain`/`_shopping_list`/`ResolutionError` (resolver), `list_ingredients`/`get_session` (existing).

- [ ] **Step 1: Failing tests** (append to `tests/test_cli.py`; extend imports):
```python
from pantry_pilot.models.schemas import RecipeFit
from pantry_pilot.services.resolver import ResolutionError


def _fit(title: str, missing: int) -> RecipeFit:
    miss = [IngredientMatch(recipe_ingredient=f"item {i}") for i in range(missing)]
    return RecipeFit(recipe=Recipe(title=title), have=[], missing=miss)


def test_cook_ideas_prints_ranked_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.find_trending", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.rank_recipes", lambda *a, **k: [_fit("Soup", 0), _fit("Stew", 2)])
    result = CliRunner().invoke(app, ["cook-ideas", "--theme", "cozy"])  # no stdin -> cook skipped
    assert result.exit_code == 0
    assert "Soup" in result.output and "Stew" in result.output


def test_cook_ideas_builds_query_from_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_find(query: TrendingQuery, **k: object) -> list[Recipe]:
        captured["query"] = query
        return []

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.find_trending", _fake_find)
    monkeypatch.setattr("pantry_pilot.cli.rank_recipes", lambda *a, **k: [])
    result = CliRunner().invoke(app, ["cook-ideas", "-t", "ramen", "--max-minutes", "30"])
    assert result.exit_code == 0
    q = captured["query"]
    assert isinstance(q, TrendingQuery) and q.theme == "ramen" and q.max_minutes == 30


def test_cook_ideas_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> list[RecipeFit]:
        raise ResolutionError("bad", kind="bad_output")

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.find_trending", lambda *a, **k: [Recipe(title="X", ingredients=["a"])])
    monkeypatch.setattr("pantry_pilot.cli.rank_recipes", _boom)
    result = CliRunner().invoke(app, ["cook-ideas"])
    assert result.exit_code == 1
    assert "Error:" in result.output
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** in `cli.py` (add imports; read pantry + close session BEFORE the slow calls, mirror `suggest`; reopen for cook):
```python
from pantry_pilot.services.resolver import (
    ResolutionError, _shopping_list, _uncertain, cook, rank_recipes,
)
from pantry_pilot.services.trending import find_trending
from pantry_pilot.models.schemas import TrendingQuery
# ... plus IngredientMatch/RecipeFit already available via schemas if needed


@app.command(name="cook-ideas")
def cook_ideas(
    theme: Annotated[str | None, typer.Option("--theme", "-t")] = None,
    cuisine: Annotated[str | None, typer.Option("--cuisine", "-c")] = None,
    meal: Annotated[str | None, typer.Option("--meal", "-m")] = None,
    max_minutes: Annotated[int | None, typer.Option("--max-minutes")] = None,
) -> None:
    """Rank what's trending by what you can cook from your pantry (Phase-3 resolver)."""
    query = TrendingQuery(theme=theme, cuisine=cuisine, meal_type=meal, max_minutes=max_minutes)
    with get_session() as session:
        pantry = list_ingredients(session)                      # read, then close before slow calls

    console.print("[dim]Finding what's trending and matching it to your pantry… (a minute or two)[/dim]")
    try:
        recipes = find_trending(query)
        fits = rank_recipes(recipes, pantry)
    except (ResolutionError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not fits:
        console.print("[yellow]No cookable ideas found[/yellow] — try a broader theme.")
        return

    table = Table(title="What can I cook tonight?")
    for col in ("Title", "Missing", "⚠", "Can make?"):
        table.add_column(col)
    for f in fits:
        table.add_row(f.recipe.title, str(len(f.missing)), str(len(_uncertain(f))),
                      "✓" if not f.missing else "✗")
    console.print(table)
    for f in fits:                                              # per-recipe detail + shopping list
        console.print(f"\n[bold]{f.recipe.title}[/bold]")
        for line in _shopping_list(f):
            console.print(f"  [yellow]•[/yellow] {line}")

    choice = _ask_cook_choice(len(fits))                        # EOF/empty/out-of-range -> None
    if choice is not None:
        with get_session() as session:
            result = cook(session, fits[choice])
        console.print(f"[green]Cooked[/green] {fits[choice].recipe.title}.")
        for f in result.flipped:
            console.print(f"  {f}")
        if result.to_update:
            console.print("Used (update by hand): "
                          + ", ".join(f"`pantry use {n} <amt>`" for n in result.to_update))


def _ask_cook_choice(n: int) -> int | None:
    """1-based prompt -> 0-based index, or None to skip. EOF / empty / bad input -> None."""
    try:
        raw = input(f"Cook one now? [1-{n}, or Enter to skip]: ").strip()
    except EOFError:
        return None
    if not raw.isdigit():
        return None
    i = int(raw)
    return i - 1 if 1 <= i <= n else None
```
- [ ] **Step 4: Run — expect PASS**; full gate; `uv run pantry cook-ideas --help` shows the command.
- [ ] **Step 5: Commit** `feat(cli): pantry cook-ideas (rank pantry-fit + optional cook)`.

---

## Task 7: Docs (plumbing)

**Files:** Create `docs/adr/0010-recipe-resolver.md`, `workflows/04-recipe-resolver.md`; Modify `docs/elephant-goldfish-playbook.md`.

- [ ] **Step 1: ADR 0010** — decisions D1–D8 + the two-class error split + the Goldfish outcome (72/88/72→95%).
- [ ] **Step 2: SOP `workflows/04-recipe-resolver.md`** — WAT SOP (intent, inputs pantry+recipes, output ranked `RecipeFit`s, the single LLM step, the deterministic gates, cook, edges).
- [ ] **Step 3: Playbook** — session-log entry (Elephant → Goldfish ×3+1 → TDD; note the design-of-record + build).
- [ ] **Step 4: Commit** `docs: ADR 0010 + SOP 04 + playbook for the recipe resolver`.

---

## Verification (end-to-end)
1. **Offline suite (authoritative):** `uv run pytest && uv run ruff check src tests && uv run mypy` — all green; confirm Phase 2a/2b/2c suites stayed green.
2. **Build spike (read-only):** grade a real `resolve_recipe` on one real `find_trending` recipe + a seeded sample pantry against the design §5 rubric (correct matches, honest `confident`, `null` when not stocked, no hallucinated names).
3. **Live smoke (final):** seed a small pantry (`pantry add …`), then `pantry cook-ideas --theme "chicken dinner"` → a ranked table + shopping list; optionally cook #1 and confirm a PRESENCE item flipped + the QUANTITY nudge, with the ledger untouched.
4. **PR** `dev-feature-8-recipe-resolver` → `main` — title *"Phase 3: recipe resolver (what can I cook tonight)"*, body what/why/how-tested citing the design doc + Goldfish. Author merges.

## Self-review notes (done)
- **Spec coverage:** §4.1 modules → Tasks 1–6; §4.2 boundary → 2; §4.3 schemas → 1; §4.4 assess → 3; §4.5 rank/shopping/cook → 4/5; §4.6 errors → 2 (+ used throughout); §4.7 CLI → 6; §5 eval → Verification/spike; §6 edges → tests in 3/4/5; §7 tests → all test files; §8 decisions → ADR (7). No gaps.
- **Type consistency:** `IngredientMatch`/`RecipeFit`/`CookResult` fields + `resolve_recipe`/`assess`/`rank_recipes`/`cook` signatures match across tasks and the design doc.
- **No placeholders:** every step carries real test/impl code; author-core steps show reference code marked review-only.
