from anthropic import Anthropic

from pantry_pilot.core.llm import get_client
from pantry_pilot.models.enums import Category
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient


def _format_pantry(ingredients: list[Ingredient], goal: Category) -> str:
    """Turn the pantry + goal into a plain-text block for the LLM to read."""
    lines = [f"Macro goal: {goal.value}", "", "Pantry items:"]
    for item in ingredients:
        line = f"- {item.name} ({item.category.value})"
        lines.append(line)
    return "\n".join(lines)


class RecipeSynthesisError(Exception):
    """Raised when the LLM fails to produce a valid RecipeQuery."""


def synthesize_recipe_query(
    ingredients: list[Ingredient],
    goal: Category,
    *,
    client: Anthropic | None = None,
) -> RecipeQuery:
    """Ask Claude to turn the pantry + goal into a schema-validated RecipeQuery."""
    client = client or get_client()
    system = (
        "You convert a kitchen pantry and a macro goal into a recipe search query. "
        "Use only ingredients present in the pantry for include_ingredients."
    )
    response = client.messages.parse(
        model="claude-opus-4-8",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": _format_pantry(ingredients, goal)}],
        output_format=RecipeQuery,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RecipeSynthesisError("the model did not return a usable recipe query")
    return response.parsed_output
