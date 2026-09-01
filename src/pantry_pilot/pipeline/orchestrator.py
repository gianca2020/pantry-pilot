"""Phase 4 orchestrator — the WAT "Agent" (a deterministic DAG, NOT an LLM control loop).

WHAT  `make_plan` chains the Phase 2a-3 Tools end-to-end: synthesize intent from the pantry,
      map it to a trending-web search, fetch + rank by pantry-fit — degrading to unranked
      Spoonacular ideas when nothing trendy is rankable.
WHY   The orchestration is deterministic; the LLM only reasons *inside* the tools it drives
      (`find_trending` is a bounded web agent; `synthesize`/the per-recipe match are LLM steps).
      Sequencing them with a DAG (no cycle to bound) satisfies the WAT "Agent" role AND
      Principle 10 by construction: hard `MAX_RANK` cap on the one fan-out, content->degrade /
      transport->abort, and NO auto-retry.
HOW   Every chained tool injects its transport, so the whole chain is offline-testable: tests
      pass fake runners/fetchers and plain `Ingredient` objects; nothing shells out.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pantry_pilot.core.claude_cli import ClaudeRunner
from pantry_pilot.core.spoonacular import RecipeFetcher
from pantry_pilot.models.schemas import PlanResult, RecipeQuery, StageTrace, TrendingQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.resolver import rank_recipes
from pantry_pilot.services.retrieval import find_recipes
from pantry_pilot.services.synthesizer import synthesize_recipe_query
from pantry_pilot.services.trending import TrendingRecipeError, find_trending

# Hard cap on the one fan-out (the serial rank). Principle 10: bounded work, no unbounded loop.
MAX_RANK = 5


# The value a timed stage returns varies (a RecipeQuery, or a list of recipes/fits), so _timed is
# generic over it (PEP 695): the type parameter T carries the thunk's return type to the caller.
def _timed[T](name: str, thunk: Callable[[], T]) -> tuple[T, StageTrace]:
    """Run `thunk`, wall-clock it, and label a StageTrace from what it returned.

    Exceptions PROPAGATE (never swallowed here) — the caller decides degrade-vs-abort. The
    outcome/detail are inferred from the return value so no stage is left to caller guesswork:
      - a non-empty list  -> ("ok",    "<n> recipes")
      - an empty list      -> ("empty", "0 recipes")
      - a non-list value   -> ("ok",    None)   # the synthesize stage returns a RecipeQuery
    `seconds` is rounded to 0.1 s and is OBSERVED — unit tests never assert on it.
    """
    start = time.monotonic()
    value = thunk()
    seconds = round(time.monotonic() - start, 1)
    if isinstance(value, list):
        outcome, detail = ("ok", f"{len(value)} recipes") if value else ("empty", "0 recipes")
    else:
        outcome, detail = "ok", None
    return value, StageTrace(name=name, outcome=outcome, seconds=seconds, detail=detail)


def _to_trending_query(
    q: RecipeQuery,
    theme: str | None,
    cuisine: str | None,
    meal: str | None,
    max_minutes: int | None,
) -> TrendingQuery:
    """Map the pantry-synthesized RecipeQuery onto a TrendingQuery; explicit CLI flags win.

    One synthesis serves BOTH sources: `include_/exclude_ingredients` are deliberately NOT
    mapped here (they shape only the Spoonacular fallback's native RecipeQuery) — trending
    searches on a free-text theme instead. `theme` falls back keywords -> first include so an
    empty-keywords query still searches for *something*. `max_minutes` guards on `is not None`
    (0 is a legitimate override); the string fields use `or` (an empty string is not meaningful).
    """
    return TrendingQuery(
        theme=theme or q.keywords or (q.include_ingredients[0] if q.include_ingredients else None),
        cuisine=cuisine or q.cuisine,
        meal_type=meal or q.dish_type,
        max_minutes=max_minutes if max_minutes is not None else q.max_ready_minutes,
    )


def make_plan(
    pantry: list[Ingredient],
    *,
    theme: str | None = None,
    cuisine: str | None = None,
    meal: str | None = None,
    max_minutes: int | None = None,
    synth_runner: ClaudeRunner | None = None,
    trending_fetcher: ClaudeRunner | None = None,
    spoon_fetcher: RecipeFetcher | None = None,
    rank_runner: ClaudeRunner | None = None,
) -> PlanResult:
    """The deterministic pipeline: synthesize -> map -> trending -> rank, Spoonacular on degrade.

    Error policy (the project's two-class split, applied to the chain):
      - Stage 1 RecipeSynthesisError (content) -> propagate (foundational; CLI -> exit 1).
      - Stage 2 TrendingRecipeError (content) OR empty trending -> DEGRADE to unranked ideas.
      - any transport error (ClaudeCliError / SpoonacularError, any stage) -> propagate.
    The ONLY try/except here is the Stage-2 TrendingRecipeError -> degrade; everything else
    surfaces to the CLI, which maps it to a clean one-line message + exit 1.
    """
    stages: list[StageTrace] = []

    # Stage 1 - INTENT: pantry -> RecipeQuery (the existing synthesize boundary).
    query, s = _timed("synthesize", lambda: synthesize_recipe_query(pantry, runner=synth_runner))
    stages.append(s)

    # Stage 2 - TRENDING (primary, rankable): map the intent, then the agentic web search.
    # Timed by hand (not via _timed) so the trace records `seconds` EVEN on a content-degrade.
    tq = _to_trending_query(query, theme, cuisine, meal, max_minutes)
    start = time.monotonic()
    try:
        recipes = find_trending(tq, fetcher=trending_fetcher)  # ClaudeCliError -> propagate
        outcome, detail = ("ok", f"{len(recipes)} recipes") if recipes else ("empty", "0 recipes")
    except TrendingRecipeError:  # content failure -> DEGRADE (not abort)
        recipes, outcome, detail = [], "degraded", "content error -> fallback"
    stages.append(StageTrace(name="trending", outcome=outcome,
                             seconds=round(time.monotonic() - start, 1), detail=detail))

    # Stage 3 - DEGRADE if nothing rankable (empty OR content-degraded): unranked Spoonacular ideas.
    if not recipes:
        ideas, s = _timed("fallback", lambda: find_recipes(query, fetcher=spoon_fetcher))
        stages.append(s)  # SpoonacularError (transport) -> propagate
        return PlanResult(intent=query, source_used="spoonacular_fallback",
                          fits=[], ideas=ideas, stages=stages, degraded=True)

    # Stage 4 - RANK vs the pantry (cap the serial fan-out at MAX_RANK). rank_recipes swallows a
    # per-recipe ResolutionError (content) and propagates ClaudeCliError (transport).
    fits, s = _timed("rank", lambda: rank_recipes(recipes[:MAX_RANK], pantry, runner=rank_runner))
    stages.append(s)
    return PlanResult(intent=query, source_used="trending",
                      fits=fits, ideas=[], stages=stages, degraded=False)
