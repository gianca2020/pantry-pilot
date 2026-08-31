"""Smoke tests for the CLI commands (run offline: no real DB, no network)."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from pantry_pilot.cli import app
from pantry_pilot.core.claude_cli import ClaudeCliError
from pantry_pilot.models.schemas import (
    CookResult,
    IngredientMatch,
    PlanResult,
    Recipe,
    RecipeFit,
    RecipeQuery,
    StageTrace,
    TrendingQuery,
)
from pantry_pilot.services.resolver import ResolutionError


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


# --- `cook-ideas` command (Phase-3 resolver; offline: find_trending + rank_recipes patched) ---


def _fit(title: str, missing: int) -> RecipeFit:
    miss = [IngredientMatch(recipe_ingredient=f"item {i}") for i in range(missing)]
    return RecipeFit(recipe=Recipe(title=title), have=[], missing=miss)


def test_cook_ideas_prints_ranked_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.find_trending", lambda *a, **k: [])
    monkeypatch.setattr(
        "pantry_pilot.cli.rank_recipes", lambda *a, **k: [_fit("Soup", 0), _fit("Stew", 2)]
    )
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
    monkeypatch.setattr(
        "pantry_pilot.cli.find_trending", lambda *a, **k: [Recipe(title="X", ingredients=["a"])]
    )
    monkeypatch.setattr("pantry_pilot.cli.rank_recipes", _boom)
    result = CliRunner().invoke(app, ["cook-ideas"])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_ask_cook_choice_parses_and_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-blocking prompt: 1-based -> 0-based; empty / non-numeric / out-of-range / EOF -> None."""
    from pantry_pilot.cli import _ask_cook_choice

    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    assert _ask_cook_choice(3) == 0  # 1-based -> 0-based
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert _ask_cook_choice(3) is None  # empty -> skip
    monkeypatch.setattr("builtins.input", lambda _prompt: "nope")
    assert _ask_cook_choice(3) is None  # non-numeric -> skip
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")
    assert _ask_cook_choice(3) is None  # out of range -> skip

    def _eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert _ask_cook_choice(3) is None  # EOF / no TTY -> skip


# --- `plan` command (Phase-4 orchestrator; offline: make_plan is monkeypatched) ---


def _ranked_plan(*, cookable: bool = False) -> PlanResult:
    fit = RecipeFit(
        recipe=Recipe(title="Garlic Chicken"),
        have=[IngredientMatch(recipe_ingredient="2 lb chicken", pantry_name="chicken")],
        missing=[] if cookable else [IngredientMatch(recipe_ingredient="1 cup honey")],
    )
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    return PlanResult(
        intent=q, source_used="trending", fits=[fit],
        stages=[StageTrace(name="synthesize"), StageTrace(name="trending"),
                StageTrace(name="rank")],
    )


def _degraded_plan() -> PlanResult:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])
    return PlanResult(
        intent=q, source_used="spoonacular_fallback", degraded=True,
        ideas=[Recipe.model_validate({"title": "Idea Soup", "sourceUrl": "https://x.com"})],
        stages=[StageTrace(name="synthesize"), StageTrace(name="trending"),
                StageTrace(name="fallback")],
    )


def test_plan_prints_ranked_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan())
    result = CliRunner().invoke(app, ["plan", "-t", "cozy"])  # no stdin -> cook skipped
    assert result.exit_code == 0
    assert "Garlic Chicken" in result.output


def test_plan_degraded_prints_ideas_and_no_cook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _degraded_plan())
    result = CliRunner().invoke(app, ["plan"])
    assert result.exit_code == 0
    assert "Idea Soup" in result.output
    assert "Cook one now" not in result.output  # degraded path has no cook prompt


def test_plan_verbose_prints_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan())
    result = CliRunner().invoke(app, ["plan", "-v"])
    assert result.exit_code == 0
    assert "synthesize" in result.output  # the per-stage trace


def test_plan_cook_path_flips_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", lambda *a, **k: _ranked_plan(cookable=True))

    @contextmanager
    def _dummy() -> Iterator[None]:
        yield None

    monkeypatch.setattr("pantry_pilot.cli.get_session", _dummy)
    monkeypatch.setattr(
        "pantry_pilot.cli.cook",
        lambda s, f: CookResult(flipped=["garlic -> low"], to_update=["chicken"]),
    )
    result = CliRunner().invoke(app, ["plan"], input="1\n")
    assert result.exit_code == 0
    assert "Cooked" in result.output and "garlic -> low" in result.output


def test_plan_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> PlanResult:
        raise ClaudeCliError("not logged in", kind="auth")

    monkeypatch.setattr("pantry_pilot.cli.init_db", lambda: None)
    monkeypatch.setattr("pantry_pilot.cli.list_ingredients", lambda *a, **k: [])
    monkeypatch.setattr("pantry_pilot.cli.make_plan", _boom)
    result = CliRunner().invoke(app, ["plan"])
    assert result.exit_code == 1
    assert "Error:" in result.output
