"""Tests for RecipeQuery — the LLM's validated output shape.

RED FIRST: these fail until you write src/pantry_pilot/models/schemas.py.
"""

import pytest
from pydantic import ValidationError

from pantry_pilot.models.schemas import Recipe, RecipeQuery, TrendingQuery, TrendingResults


def test_minimal_query_defaults_optionals_to_none() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    assert q.include_ingredients == ["chicken"]
    assert q.exclude_ingredients == []
    # Everything else is optional and starts as None.
    assert q.keywords is None
    assert q.cuisine is None
    assert q.dish_type is None
    assert q.max_ready_minutes is None


def test_full_query_keeps_all_fields() -> None:
    q = RecipeQuery(
        include_ingredients=["chicken", "rice"],
        exclude_ingredients=["peanuts"],
        keywords="high protein dinner",
        cuisine="mediterranean",
        dish_type="main course",
        max_ready_minutes=30,
    )
    assert q.max_ready_minutes == 30
    assert q.dish_type == "main course"


def test_wrong_type_is_rejected() -> None:
    # include_ingredients must be a list, not a bare string.
    with pytest.raises(ValidationError):
        RecipeQuery.model_validate(
            {"include_ingredients": "chicken", "exclude_ingredients": []}
        )


def test_include_ingredients_is_required() -> None:
    # The synthesizer's whole job is to surface pantry items, so this field
    # must always be present (an empty list is fine; a missing field is not).
    with pytest.raises(ValidationError):
        RecipeQuery.model_validate({"exclude_ingredients": []})


# --- Source #2 "what's hot right now": Recipe evolution + trending I/O models ---


def test_recipe_id_is_now_optional() -> None:
    # Web recipes have no Spoonacular id; identity comes from source_url instead.
    r = Recipe(title="Web recipe", source_url="https://seriouseats.com/x")
    assert r.id is None


def test_recipe_accepts_ingredients_and_steps() -> None:
    r = Recipe(title="X", ingredients=["a", "b"], steps=["1", "2", "3"])
    assert r.ingredients == ["a", "b"]
    assert r.steps == ["1", "2", "3"]


def test_recipe_reads_camelcase_aliases() -> None:
    # The web model emits Recipe's aliases; model_validate reads them.
    r = Recipe.model_validate(
        {"title": "X", "readyInMinutes": 30, "sourceUrl": "https://x.com"}
    )
    assert r.ready_minutes == 30
    assert r.source_url == "https://x.com"


def test_trending_query_defaults_all_optional() -> None:
    # Empty query == "what's hot overall"; every field is optional.
    q = TrendingQuery()
    assert (q.theme, q.cuisine, q.meal_type, q.max_minutes) == (None, None, None, None)


def test_trending_results_validates_nested_recipes() -> None:
    tr = TrendingResults.model_validate(
        {"recipes": [{"title": "X", "sourceUrl": "https://x.com"}]}
    )
    assert len(tr.recipes) == 1
    assert tr.recipes[0].id is None  # no id from the web


def test_trending_results_requires_recipes_key() -> None:
    with pytest.raises(ValidationError):
        TrendingResults.model_validate({})
