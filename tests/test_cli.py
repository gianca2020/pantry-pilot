"""Smoke test for the `suggest` CLI command (runs offline: no real DB, no network)."""

import pytest
from typer.testing import CliRunner

from pantry_pilot.cli import app
from pantry_pilot.models.schemas import RecipeQuery


def test_suggest_prints_the_synthesized_query(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = RecipeQuery(include_ingredients=["chicken", "rice"], exclude_ingredients=[])
    # Replace the DB init, the pantry read, and the LLM call so the command runs
    # deterministically without touching the database or the network.
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.synthesize_recipe_query", lambda *a, **k: canned)

    result = CliRunner().invoke(app, ["suggest"])

    assert result.exit_code == 0
    assert "chicken" in result.output  # the synthesized query is printed
