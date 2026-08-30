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
    """One candidate recipe from Spoonacular complexSearch (Phase-2b retrieval).

    WHAT  The validated shape of a single search result. Like RecipeQuery, a plain
          Pydantic model (no DB, just structure) — the deterministic gate for API output.
    WHY   Retrieval must never hand un-validated API JSON to the rest of the app (the
          determinism rule). _parse_recipes() runs Recipe.model_validate() on each result;
          extra API fields we don't model (imageType, healthScore, dishTypes, ...) are
          dropped automatically.
    HOW   Two fields come back under a camelCase API key that differs from our snake_case
          name, so they carry a `validation_alias` — model_validate reads the value from the
          alias key while the attribute keeps its Python name.
    """

    id: int  # REQUIRED: Spoonacular's stable recipe id <- "id"
    title: str  # REQUIRED: display name <- "title"
    image: str | None = None  # thumbnail URL <- "image"
    ready_minutes: int | None = Field(default=None, validation_alias="readyInMinutes")
    servings: int | None = None  # how many the recipe serves <- "servings"
    source_url: str | None = Field(default=None, validation_alias="sourceUrl")
