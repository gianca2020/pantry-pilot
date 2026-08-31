"""Tests for the Phase 3 recipe resolver (services/resolver.py).

Everything OFFLINE: inject a fake ClaudeRunner returning a canned envelope dict, and build a
small pantry from the saved fixture / plain Ingredient objects. Nothing shells out to claude.

RED FIRST: these fail until you hand-write src/pantry_pilot/services/resolver.py.
"""

import json
from pathlib import Path

import pytest

from pantry_pilot.core.claude_cli import ClaudeRunner
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import IngredientMatch, Recipe
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.resolver import (
    ResolutionError,
    _parse_resolution,
    _resolver_persona,
    _to_resolution_prompt,
    assess,
    resolve_recipe,
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


# --- Task 3: assess (deterministic gate: hallucination guard + stock check) ---


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
