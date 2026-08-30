"""Tests for the deterministic recipe-retrieval tool (offline: inject a fake RecipeFetcher).

Mirrors tests/test_synthesizer.py: the tool owns contract + validation (RecipeQuery ->
params, results -> Recipe); the injected fetcher owns transport. We pass a fake fetcher that
returns saved fixture JSON, so nothing hits the network.

RED FIRST: these fail until you write src/pantry_pilot/services/retrieval.py and the Recipe
schema in models/schemas.py. Your job is to make them green.
"""

import json
from pathlib import Path

import pytest

from pantry_pilot.core.spoonacular import RecipeFetcher, SpoonacularError
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.services.retrieval import (
    _query_to_params,
    find_recipes,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "spoonacular_complexsearch.json"


def _load_body() -> dict[str, object]:
    body = json.loads(_FIXTURE.read_text())
    assert isinstance(body, dict)  # narrow Any -> dict for mypy --strict
    return body


def _fetcher_returning(body: dict[str, object]) -> RecipeFetcher:
    def _fetch(params: dict[str, str]) -> dict[str, object]:
        return body

    return _fetch


def _fetcher_raising(exc: Exception) -> RecipeFetcher:
    def _fetch(params: dict[str, str]) -> dict[str, object]:
        raise exc

    return _fetch


# --- find_recipes: results -> list[Recipe] ---


def test_find_recipes_parses_every_result_in_order() -> None:
    q = RecipeQuery(include_ingredients=["chicken", "garlic"])
    recipes = find_recipes(q, fetcher=_fetcher_returning(_load_body()))
    assert [r.id for r in recipes] == [715538, 640941, 511728]
    assert recipes[0].title == "Garlic Butter Chicken with Rice"


def test_camelcase_api_fields_map_onto_snake_case_recipe_fields() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    first = find_recipes(q, fetcher=_fetcher_returning(_load_body()))[0]
    assert first.ready_minutes == 35  # from readyInMinutes
    assert first.servings == 4
    assert first.source_url == "https://example.com/garlic-butter-chicken-rice"  # from sourceUrl


def test_missing_optional_fields_default_to_none() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    third = find_recipes(q, fetcher=_fetcher_returning(_load_body()))[2]
    assert third.id == 511728
    assert third.ready_minutes is None  # readyInMinutes absent in the fixture
    assert third.source_url is None  # sourceUrl absent
    assert third.servings == 6


def test_empty_results_returns_empty_list() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    assert find_recipes(q, fetcher=_fetcher_returning({"results": []})) == []


def test_missing_results_key_raises_bad_output() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    with pytest.raises(SpoonacularError) as exc:
        find_recipes(q, fetcher=_fetcher_returning({"totalResults": 0}))
    assert exc.value.kind == "bad_output"


def test_malformed_result_item_raises_bad_output() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    bad: dict[str, object] = {"results": [{"title": "no id here"}]}  # missing the required id
    with pytest.raises(SpoonacularError) as exc:
        find_recipes(q, fetcher=_fetcher_returning(bad))
    assert exc.value.kind == "bad_output"


def test_transport_error_propagates() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    fetcher = _fetcher_raising(SpoonacularError("quota gone", kind="quota"))
    with pytest.raises(SpoonacularError) as exc:
        find_recipes(q, fetcher=fetcher)
    assert exc.value.kind == "quota"


# --- _query_to_params: RecipeQuery -> complexSearch params ---


def test_query_to_params_maps_every_field_and_adds_constants() -> None:
    q = RecipeQuery(
        include_ingredients=["chicken", "garlic"],
        exclude_ingredients=["peanuts"],
        keywords="garlic butter chicken",
        cuisine="italian",
        dish_type="main course",
        max_ready_minutes=30,
    )
    params = _query_to_params(q)
    assert params["includeIngredients"] == "chicken,garlic"  # list -> comma-joined
    assert params["excludeIngredients"] == "peanuts"
    assert params["query"] == "garlic butter chicken"  # keywords -> query
    assert params["cuisine"] == "italian"
    assert params["type"] == "main course"  # dish_type -> type
    assert params["maxReadyTime"] == "30"  # int -> str
    # deterministic constants (not the model's job)
    assert params["sort"] == "popularity"
    assert params["number"] == "5"
    assert params["addRecipeInformation"] == "true"


def test_query_to_params_omits_absent_optionals() -> None:
    q = RecipeQuery(include_ingredients=["chicken"])
    params = _query_to_params(q)
    assert params["includeIngredients"] == "chicken"
    for absent in ("excludeIngredients", "query", "cuisine", "type", "maxReadyTime"):
        assert absent not in params  # absent, not an empty string
    assert params["sort"] == "popularity"  # constants still present
