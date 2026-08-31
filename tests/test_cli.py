"""Smoke tests for the CLI commands (run offline: no real DB, no network)."""

import pytest
from typer.testing import CliRunner

from pantry_pilot.cli import app
from pantry_pilot.core.claude_cli import ClaudeCliError
from pantry_pilot.models.schemas import Recipe, RecipeQuery, TrendingQuery


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


# --- `trending` command (source #2, offline: find_trending is monkeypatched) ---


def _rec(title: str) -> Recipe:
    return Recipe.model_validate(
        {"title": title, "sourceUrl": f"https://seriouseats.com/{title}",
         "steps": ["a", "b"], "ingredients": ["x"]}
    )


def test_trending_prints_results(monkeypatch: pytest.MonkeyPatch) -> None:
    recipes = [_rec("Alpha"), _rec("Bravo")]
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.find_trending", lambda *a, **k: recipes)

    result = CliRunner().invoke(app, ["trending", "--theme", "chicken"])

    assert result.exit_code == 0
    assert "Alpha" in result.output
    assert "Bravo" in result.output


def test_trending_builds_query_from_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake(query: TrendingQuery, **k: object) -> list[Recipe]:
        captured["query"] = query
        return []

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.find_trending", _fake)

    result = CliRunner().invoke(
        app, ["trending", "-t", "ramen", "-c", "japanese", "-m", "dinner", "--max-minutes", "30"]
    )

    assert result.exit_code == 0
    q = captured["query"]
    assert isinstance(q, TrendingQuery)
    assert (q.theme, q.cuisine, q.meal_type, q.max_minutes) == ("ramen", "japanese", "dinner", 30)


def test_trending_empty_is_friendly_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.find_trending", lambda *a, **k: [])

    result = CliRunner().invoke(app, ["trending"])

    assert result.exit_code == 0
    assert "Nothing trending" in result.output


def test_trending_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> list[Recipe]:
        raise ClaudeCliError("not logged in", kind="auth")

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.find_trending", _boom)

    result = CliRunner().invoke(app, ["trending"])

    assert result.exit_code == 1
    assert "Error:" in result.output
