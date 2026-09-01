"""Offline backbone for the Phase-4 orchestrator (services injected as fakes).

Everything runs OFFLINE: the four transports the chained tools inject (synth/rank runners,
trending/spoon fetchers) are canned fakes, and the pantry is plain `Ingredient` objects — no
claude, no network, no DB. The `session` fixture is NOT needed here (cook lives in the CLI).

RED FIRST: fails until src/pantry_pilot/pipeline/orchestrator.py exists.
"""

from typing import Any

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, ClaudeRunner
from pantry_pilot.core.spoonacular import RecipeFetcher
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.pipeline.orchestrator import (
    MAX_RANK,
    _timed,
    _to_trending_query,
    make_plan,
)

# --- fakes: the transports the tools inject (no claude, no network) ---

_QUERY: dict[str, Any] = {
    "include_ingredients": ["chicken", "rice"],
    "keywords": "chicken dinner",
    "cuisine": "asian",
    "dish_type": "main course",
    "max_ready_minutes": 40,
}


def _synth_runner(query: dict[str, Any] = _QUERY, *, error: bool = False) -> ClaudeRunner:
    """A fake synthesize transport: returns the canned RecipeQuery envelope (or a content error)."""

    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return {"is_error": True} if error else {"is_error": False, "structured_output": query}

    return _run


def _recipe(title: str) -> dict[str, object]:
    """A canned trending recipe that SURVIVES find_trending._filter (allow-listed + steps)."""
    return {
        "title": title,
        "sourceUrl": f"https://seriouseats.com/{title}",  # allow-listed domain
        "steps": ["step one", "step two"],
        "ingredients": ["2 lb chicken breasts", "1 cup rice"],
    }


def _trending_fetcher(recipes: list[dict[str, object]], *, error: bool = False) -> ClaudeRunner:
    """A fake trending web transport: returns a canned envelope (or a content error)."""

    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        if error:
            return {"is_error": True}  # -> TrendingRecipeError inside find_trending
        return {"is_error": False, "structured_output": {"recipes": recipes}}

    return _run


def _rank_runner(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
    """A fake resolve transport: one match per '- <line>' in the prompt; chicken/rice -> pantry."""
    lines = [ln[2:] for ln in prompt.split("\n") if ln.startswith("- ")]
    matches = [
        {
            "recipe_ingredient": ln,
            "pantry_name": next((n for n in ("chicken", "rice") if n in ln), None),
        }
        for ln in lines
    ]
    return {"is_error": False, "structured_output": {"matches": matches}}


def _spoon_fetcher(results: list[dict[str, object]]) -> RecipeFetcher:
    """A fake Spoonacular transport: returns a complexSearch-shaped body."""

    def _run(params: dict[str, str]) -> dict[str, object]:
        return {"results": results}

    return _run


def _pantry() -> list[Ingredient]:
    return [
        Ingredient(name="chicken", category=Category.PROTEIN, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.GRAM, on_hand=800),
        Ingredient(name="rice", category=Category.STAPLE, tracking_mode=TrackingMode.QUANTITY,
                   base_unit=BaseUnit.GRAM, on_hand=1000),
        Ingredient(name="spinach", category=Category.GREEN, tracking_mode=TrackingMode.PRESENCE,
                   status=StockStatus.OUT),
    ]


# --- the deterministic RecipeQuery -> TrendingQuery map (pure) ---


def test_to_trending_query_maps_fields() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[],
                    keywords="ramen", cuisine="japanese", dish_type="soup", max_ready_minutes=30)
    tq = _to_trending_query(q, None, None, None, None)
    assert (tq.theme, tq.cuisine, tq.meal_type, tq.max_minutes) == ("ramen", "japanese", "soup", 30)


def test_to_trending_query_theme_falls_back_to_first_include() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[])  # no keywords
    assert _to_trending_query(q, None, None, None, None).theme == "chicken"


def test_to_trending_query_flags_override() -> None:
    q = RecipeQuery(include_ingredients=["chicken"], exclude_ingredients=[],
                    keywords="ramen", cuisine="japanese", dish_type="soup", max_ready_minutes=30)
    tq = _to_trending_query(q, "tacos", "mexican", "dinner", 0)  # 0 is a legit max_minutes override
    assert (tq.theme, tq.cuisine, tq.meal_type, tq.max_minutes) == ("tacos", "mexican", "dinner", 0)


# --- the stage-timing helper ---


def test_timed_infers_outcome_and_detail() -> None:
    val, tr = _timed("trending", lambda: [1, 2])
    assert val == [1, 2]
    assert (tr.name, tr.outcome, tr.detail) == ("trending", "ok", "2 recipes")
    _, empty = _timed("trending", lambda: [])
    assert (empty.outcome, empty.detail) == ("empty", "0 recipes")
    q = RecipeQuery(include_ingredients=["x"], exclude_ingredients=[])
    _, nonlist = _timed("synthesize", lambda: q)  # a non-list return (the synthesized query)
    assert (nonlist.outcome, nonlist.detail) == ("ok", None)


# --- make_plan: the DAG ---


def test_make_plan_ranked_happy_path() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([_recipe("Alpha")]),
        rank_runner=_rank_runner,
    )
    assert plan.source_used == "trending" and plan.degraded is False
    assert plan.ideas == [] and len(plan.fits) == 1
    assert [s.name for s in plan.stages] == ["synthesize", "trending", "rank"]
    assert plan.stages[1].outcome == "ok"


def test_make_plan_degrades_on_empty_trending() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([]),  # nothing rankable
        spoon_fetcher=_spoon_fetcher([{"id": 1, "title": "Fallback Stew"}]),
    )
    assert plan.source_used == "spoonacular_fallback" and plan.degraded is True
    assert plan.fits == [] and len(plan.ideas) == 1
    assert [s.name for s in plan.stages] == ["synthesize", "trending", "fallback"]


def test_make_plan_degrades_on_trending_content_error() -> None:
    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_trending_fetcher([], error=True),  # -> TrendingRecipeError
        spoon_fetcher=_spoon_fetcher([{"id": 1, "title": "Fallback Stew"}]),
    )
    assert plan.degraded is True and plan.stages[1].outcome == "degraded"


def test_make_plan_degrades_on_trending_timeout() -> None:
    def _boom(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise ClaudeCliError("timed out after 180s", kind="timeout")

    plan = make_plan(
        _pantry(),
        synth_runner=_synth_runner(),
        trending_fetcher=_boom,
        spoon_fetcher=_spoon_fetcher([{"id": 1, "title": "Fallback Stew"}]),
    )
    assert plan.degraded is True
    assert plan.source_used == "spoonacular_fallback"
    assert plan.fits == [] and plan.ideas != []
    trending_stage = next(s for s in plan.stages if s.name == "trending")
    assert trending_stage.outcome == "degraded"
    assert trending_stage.detail is not None and "timeout" in trending_stage.detail


def test_make_plan_propagates_non_timeout_trending_error() -> None:
    def _boom(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise ClaudeCliError("unauthorized", kind="auth")

    with pytest.raises(ClaudeCliError):
        make_plan(_pantry(), synth_runner=_synth_runner(), trending_fetcher=_boom)


def test_make_plan_aborts_on_synthesis_error() -> None:
    from pantry_pilot.services.synthesizer import RecipeSynthesisError

    with pytest.raises(RecipeSynthesisError):
        make_plan(_pantry(), synth_runner=_synth_runner(error=True))


def test_make_plan_caps_rank_at_max_rank() -> None:
    calls = {"n": 0}

    def _counting_rank(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        calls["n"] += 1
        return {
            "is_error": False,
            "structured_output": {
                "matches": [{"recipe_ingredient": "2 lb chicken breasts", "pantry_name": "chicken"}]
            },
        }

    recipes = [_recipe(f"R{i}") for i in range(MAX_RANK + 2)]  # 7 > cap
    plan = make_plan(_pantry(), synth_runner=_synth_runner(),
                     trending_fetcher=_trending_fetcher(recipes), rank_runner=_counting_rank)
    assert calls["n"] == MAX_RANK and len(plan.fits) == MAX_RANK
