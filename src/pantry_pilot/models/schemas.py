"""RecipeQuery — the validated shape the query synthesizer must produce.

WHAT  A Pydantic model: a data shape with built-in validation. NOT a database
      table like Ingredient — there are no rows and no db here, just structure.
WHY   It is the single contract for our one LLM step. We hand these fields to
      Claude as "return exactly this", and messages.parse() validates the reply
      against this model before we trust it — so a malformed LLM answer can never
      reach the rest of the app (the determinism rule). The fields map onto
      Spoonacular's complexSearch parameters, which Phase 2b will send to the API.
HOW   Each line is `name: type`. A bare type is REQUIRED; a `type | None = None`
      field is OPTIONAL and defaults to None when the model omits it.
"""

from pydantic import BaseModel, Field


class RecipeQuery(BaseModel):
    """A Spoonacular-shaped recipe search query, synthesized from the pantry."""

    include_ingredients: list[str]  # REQUIRED: must-use pantry items -> includeIngredients
    exclude_ingredients: list[str] | None = None  # items to avoid -> excludeIngredients
    keywords: str | None = None  # free-text search terms -> query
    cuisine: str | None = None  # e.g. "italian" -> cuisine
    dish_type: str | None = None  # e.g. "main course" -> type
    max_ready_minutes: int | None = None  # cap on total cook time -> maxReadyTime


class Recipe(BaseModel):
    """One candidate recipe — shared output of BOTH sources (#1 Spoonacular, #2 web).

    WHAT  The validated shape of a single result. A plain Pydantic model (no DB, just
          structure) — the deterministic gate for whatever a source hands back.
    WHY   Retrieval/trending must never hand un-validated JSON to the rest of the app
          (the determinism rule). Each source validates every result against this model;
          extra fields we don't model (imageType, healthScore, dishTypes, ...) are dropped.
          Identity is `source_url` (the web has no stable id), so `id` is optional and
          the cooking-detail fields (`ingredients`/`steps`) stay None for Spoonacular.
    HOW   Two fields come back under a camelCase key that differs from our snake_case name,
          so they carry a `validation_alias` — model_validate reads the value from the alias
          key while the attribute keeps its Python name.
    """

    id: int | None = None  # Spoonacular's stable id; None for web recipes (identity = source_url)
    title: str  # REQUIRED: display name <- "title"
    image: str | None = None  # thumbnail URL <- "image"
    ready_minutes: int | None = Field(default=None, validation_alias="readyInMinutes")
    servings: int | None = None  # how many the recipe serves <- "servings"
    source_url: str | None = Field(default=None, validation_alias="sourceUrl")
    ingredients: list[str] | None = None  # source #2 fills these verbatim; None for Spoonacular
    steps: list[str] | None = None  # numbered method, copied verbatim; None for Spoonacular


class TrendingQuery(BaseModel):
    """The input to source #2 ("what's hot right now") — its OWN shape, not RecipeQuery.

    WHAT  A small, all-optional search intent for the agentic web fetcher.
    WHY   Source #2 is a parallel entry point (find_trending), so it takes its own input
          rather than the pantry-shaped RecipeQuery. An empty query means "surprise me —
          what's hot overall"; each set field narrows the search.
    HOW   Every field is optional; _to_search_terms() turns the set ones into a web-search
          string. Domain steering (allow/block sites) lives in the persona, not here.
    """

    theme: str | None = None  # free-text focus, e.g. "chicken dinner"; None -> "recipes"
    cuisine: str | None = None  # e.g. "thai"
    meal_type: str | None = None  # e.g. "dinner"
    max_minutes: int | None = None  # cap on total time -> "under N minutes"


class TrendingResults(BaseModel):
    """The JSON contract the web model must return — a wrapper around a list of Recipes.

    WHAT  A one-field object: {"recipes": [Recipe, ...]}.
    WHY   --json-schema needs an object at the top level, so we wrap the list.
          model_json_schema() emits Recipe's aliases (readyInMinutes/sourceUrl), so the
          model fills those keys and model_validate reads them back.
    HOW   recipes is REQUIRED (no default); _parse_trending validates the envelope payload
          against this model before any recipe is trusted.
    """

    recipes: list[Recipe]


# --- Phase 3: recipe resolver ("what can I cook tonight?") I/O models ---


class IngredientMatch(BaseModel):
    """One recipe ingredient line matched (or not) to a canonical pantry item.

    WHAT  The atomic result of the LLM match step: a raw recipe line paired with the
          pantry item name it maps to (or None), plus an honesty flag.
    WHY   LLM boundary #3 only *matches* free text to a pantry name; deterministic code
          decides stock/rank/mutation from these. `confident=False` marks a judgment call
          the CLI highlights (⚠) but never blocks on (D5); `pantry_name=None` means the
          pantry has nothing suitable -> the item is missing.
    HOW   Defaults keep the schema forgiving so the model can emit a bare
          {recipe_ingredient, pantry_name} and still validate (confident -> True).
    """

    recipe_ingredient: str  # REQUIRED: the raw recipe line, verbatim
    pantry_name: str | None = None  # matched pantry item name, or None = not stocked
    confident: bool = True  # False = judgment call (highlight ⚠, do not block)
    note: str | None = None  # optional "why", e.g. "'green onions' ≈ 'onion'?"


class RecipeResolution(BaseModel):
    """The LLM's reply for one recipe — a wrapper around a list of matches.

    WHAT  A one-field object: {"matches": [IngredientMatch, ...]}.
    WHY   --json-schema needs an object at the top level, so we wrap the list;
          RecipeResolution.model_json_schema() is handed to Claude as the contract, and
          _parse_resolution validates the envelope payload against it before any match is
          trusted (the determinism rule).
    HOW   matches is REQUIRED (no default). No cardinality check — a reply with fewer/more
          matches than recipe lines still validates; unmatched lines just aren't assessed.
    """

    matches: list[IngredientMatch]  # REQUIRED: one entry per recipe ingredient line (ideally)


class RecipeFit(BaseModel):
    """The deterministic assessment of one recipe against the pantry (one per recipe).

    WHAT  A recipe split into what you have vs what you're missing.
    WHY   The output of `assess` and the unit `rank_recipes` orders; the CLI renders it as a
          row (Missing / ⚠ / Can make?) plus a per-recipe shopping list.
    HOW   `have` = matched to a stocked pantry item (may include ⚠ uncertain-but-stocked);
          `missing` = null match, hallucinated name, or matched-but-OUT/empty.
    """

    recipe: Recipe
    have: list[IngredientMatch]  # matched to a stocked pantry item (may include ⚠ uncertain)
    missing: list[IngredientMatch]  # null match, hallucinated name, or matched-but-OUT/empty


class CookResult(BaseModel):
    """What `cook` changed in the pantry — ledger-honest (D4).

    WHAT  Two lists of names: PRESENCE items stepped down, and QUANTITY items to adjust by hand.
    WHY   Cook flips matched PRESENCE items down one notch (OK->LOW->OUT) but never fabricates
          a ledger amount for QUANTITY items — those are *reported* for manual `pantry use`,
          keeping the event-sourced ledger truthful.
    HOW   `flipped` holds formatted strings ("name -> newstatus") the CLI prints as-is;
          `to_update` holds bare QUANTITY names.
    """

    flipped: list[str]  # formatted "name -> newstatus" per PRESENCE item stepped down
    to_update: list[str]  # QUANTITY names to adjust by hand (never auto-deducted)
