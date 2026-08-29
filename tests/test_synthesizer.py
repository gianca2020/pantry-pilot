"""Tests for the recipe-query synthesizer (offline: inject a fake ClaudeRunner).

RED FIRST: these fail until you rewrite src/pantry_pilot/services/synthesizer.py
onto the injected runner (drop the macro goal). Your job is to make them green.
"""

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, ClaudeRunner
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
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


def _runner_returning(envelope: dict[str, object]) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return envelope

    return _run


def _runner_raising(exc: Exception) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise exc

    return _run


def test_format_pantry_lists_every_item_without_a_goal() -> None:
    text = _format_pantry([_chicken(), _spinach()])
    assert "Macro goal" not in text          # goal is gone
    assert "chicken" in text and "spinach" in text


def test_returns_validated_query_from_structured_output() -> None:
    envelope = {"is_error": False, "structured_output": {"include_ingredients": ["chicken"]}}
    result = synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))
    assert result.include_ingredients == ["chicken"]


def test_falls_back_to_result_json_string() -> None:
    envelope = {"result": '{"include_ingredients": ["chicken"]}'}  # no structured_output
    result = synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))
    assert result.include_ingredients == ["chicken"]


def test_is_error_envelope_raises() -> None:
    envelope = {"is_error": True, "structured_output": {"include_ingredients": ["chicken"]}}
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))


def test_no_payload_raises() -> None:
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning({"is_error": False}))


def test_schema_invalid_payload_raises() -> None:
    # missing the required include_ingredients -> Pydantic (suspenders) catches it
    envelope = {"structured_output": {"cuisine": "italian"}}
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))


def test_transport_error_propagates() -> None:
    runner = _runner_raising(ClaudeCliError("down", kind="timeout"))
    with pytest.raises(ClaudeCliError):
        synthesize_recipe_query([_chicken()], runner=runner)
