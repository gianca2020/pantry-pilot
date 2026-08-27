"""Tests for RecipeQuery — the LLM's validated output shape.

RED FIRST: these fail until you write src/pantry_pilot/models/schemas.py.
"""

import pytest
from pydantic import ValidationError

from pantry_pilot.models.schemas import RecipeQuery


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
