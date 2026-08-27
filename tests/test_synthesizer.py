"""Tests for the recipe-query synthesizer (no real network calls)."""

from typing import cast

import pytest
from anthropic import Anthropic

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.synthesizer import (
    RecipeSynthesisError,
    _format_pantry,
    synthesize_recipe_query,
)


def _chicken() -> Ingredient:
    return Ingredient(
        name="chicken",
        category=Category.PROTEIN,
        tracking_mode=TrackingMode.QUANTITY,
        base_unit=BaseUnit.GRAM,
        on_hand=800,
    )


def _spinach() -> Ingredient:
    return Ingredient(
        name="spinach",
        category=Category.GREEN,
        tracking_mode=TrackingMode.PRESENCE,
        status=StockStatus.OK,
    )


# --- A tiny fake Claude client so tests stay offline and deterministic -------


class _FakeResponse:
    def __init__(
        self, parsed_output: RecipeQuery | None, stop_reason: str = "end_turn"
    ) -> None:
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def parse(self, **kwargs: object) -> _FakeResponse:
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


def _client_returning(response: _FakeResponse) -> Anthropic:
    # The fake matches the slice of Anthropic we call; cast so mypy accepts it.
    return cast(Anthropic, _FakeClient(response))


def test_format_pantry_names_the_goal_and_every_item() -> None:
    text = _format_pantry([_chicken(), _spinach()], Category.PROTEIN)
    assert "protein" in text.lower()  # the macro goal appears
    assert "chicken" in text  # every pantry item is listed
    assert "spinach" in text


def test_synthesize_returns_the_validated_query() -> None:
    canned = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    client = _client_returning(_FakeResponse(canned))
    result = synthesize_recipe_query([_chicken()], Category.PROTEIN, client=client)
    assert result is canned  # the parsed_output is handed straight back


def test_synthesize_raises_on_refusal() -> None:
    client = _client_returning(_FakeResponse(None, stop_reason="refusal"))
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], Category.PROTEIN, client=client)
