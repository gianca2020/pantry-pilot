"""Recipe retrieval — the deterministic Phase-2b Tool (no LLM).

Takes a validated RecipeQuery, asks the injected fetcher (default: the real Spoonacular
transport) for candidates, and validates each into a Recipe. Pure, testable Python: tests
inject a fake fetcher returning fixture JSON, so nothing here shells out to the network.
"""

# ValidationError is what Recipe.model_validate() raises on a malformed result item.
from pydantic import ValidationError

# The transport seam + typed error live in core/spoonacular.py (the plumbing).
from pantry_pilot.core.spoonacular import RecipeFetcher, SpoonacularError, fetch_recipes
from pantry_pilot.models.schemas import Recipe, RecipeQuery

# Deterministic constants — NOT the LLM's job (see CLAUDE.md determinism rule / SOP 02).
_SORT = "popularity"       # the "highly-rated" signal
_NUMBER = "5"              # how many candidates to request
_ADD_RECIPE_INFO = "true"  # ask for readyInMinutes / servings / sourceUrl (Rich Recipe)


def _query_to_params(query: RecipeQuery) -> dict[str, str]:
    """Map a RecipeQuery onto Spoonacular complexSearch query params (+ our constants)."""
    # include_ingredients is REQUIRED on RecipeQuery; complexSearch wants a comma-joined
    # string, so we always emit it. This is the one field guaranteed to be present.
    params: dict[str, str] = {"includeIngredients": ",".join(query.include_ingredients)}

    # Optional facets: emit a param ONLY when the model set the field. The `if ...` guard
    # keeps absent fields out entirely (an absent key, never an empty "" string) — a truthy
    # check also skips an empty list/string, which we wouldn't want to send anyway.
    if query.exclude_ingredients:
        params["excludeIngredients"] = ",".join(query.exclude_ingredients)
    if query.keywords:
        params["query"] = query.keywords
    if query.cuisine:
        params["cuisine"] = query.cuisine
    if query.dish_type:
        params["type"] = query.dish_type
    # A numeric 0 is falsy, so guard on `is not None` (not truthiness) and stringify.
    if query.max_ready_minutes is not None:
        params["maxReadyTime"] = str(query.max_ready_minutes)

    # The deterministic constants ride on every request — this is where "highly-rated"
    # (sort=popularity) is applied, not the LLM's job.
    params["sort"] = _SORT
    params["number"] = _NUMBER
    params["addRecipeInformation"] = _ADD_RECIPE_INFO
    return params


def _parse_recipes(payload: dict[str, object]) -> list[Recipe]:
    """Validate the complexSearch body's `results` into list[Recipe] (the determinism gate)."""
    # The body must carry a `results` list. Anything else is a broken contract, not data.
    results = payload.get("results")
    if not isinstance(results, list):
        raise SpoonacularError("Spoonacular response missing a 'results' list", kind="bad_output")

    # The sacred boundary: never trust API JSON as-is. model_validate either returns a
    # guaranteed-valid Recipe or raises ValidationError, which we wrap in our typed error.
    # An empty `results` list yields [] here — a valid, deterministic "no matches" (Decision 2).
    try:
        return [Recipe.model_validate(item) for item in results]
    except ValidationError as exc:
        raise SpoonacularError(
            "Spoonacular returned a malformed recipe", kind="bad_output"
        ) from exc


def find_recipes(
    query: RecipeQuery,
    *,
    fetcher: RecipeFetcher | None = None,
) -> list[Recipe]:
    """Fetch + validate candidate recipes for a RecipeQuery (the public tool entry point)."""
    # Dependency injection, mirroring synthesize_recipe_query: the real transport by default,
    # a fake fetcher in tests. This is what keeps retrieval fully offline-testable.
    fetcher = fetcher or fetch_recipes
    params = _query_to_params(query)
    payload = fetcher(params)  # transport (may raise SpoonacularError; we let it propagate)
    return _parse_recipes(payload)  # contract + validation gate
