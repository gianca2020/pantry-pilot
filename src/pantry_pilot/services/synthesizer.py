"""Recipe-query synthesis — the one LLM step, deterministically gated.

Deterministic Python owns pantry-read, prompt-building, and the final
`RecipeQuery.model_validate()`; the LLM call goes through an injected `ClaudeRunner`
(default: the real `run_claude`). Tests pass a fake runner, so nothing shells out.
"""

# `json` parses the JSON string the CLI mirrors into the envelope's "result" field.
import json

# Pydantic raises ValidationError when a dict doesn't fit the RecipeQuery schema.
from pydantic import ValidationError

# ClaudeRunner is the *interface* (a Protocol) our LLM call depends on;
# run_claude is the real implementation that shells out to `claude -p`.
from pantry_pilot.core.claude_cli import ClaudeRunner, run_claude
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient


class RecipeSynthesisError(Exception):
    """Raised when Claude fails to produce a usable, valid RecipeQuery from the pantry."""


def _format_pantry(ingredients: list[Ingredient]) -> str:
    """Turn the pantry into a plain-text block for the LLM to read."""
    # Start the block with a header line, then add one bullet per pantry item.
    lines = ["Pantry items:"]
    for item in ingredients:
        # e.g. "- chicken (protein)" — the item's name plus its category label.
        lines.append(f"- {item.name} ({item.category.value})")
    # Glue the list into a single string, one item per line.
    return "\n".join(lines)


def synthesize_recipe_query(
    ingredients: list[Ingredient],
    *,
    runner: ClaudeRunner | None = None,
) -> RecipeQuery:
    """Ask Claude to synthesize a Spoonacular-shaped query from the pantry."""
    # Dependency injection: use the runner passed in (a fake, in tests), otherwise
    # the real one. This is what lets the tests run without ever calling the CLI.
    runner = runner or run_claude

    # The instructions Claude gets. The second sentence is the load-bearing rule
    # that keeps include_ingredients honest (no hallucinated, non-pantry items).
    system = (
        "You convert a kitchen pantry into a recipe search query for meals that taste good. "
        "Use only ingredients present in the pantry for include_ingredients."
    )

    # Turn our Pydantic model into a JSON Schema so the CLI constrains the output shape.
    schema = RecipeQuery.model_json_schema()
    # Build the plain-text pantry block the model reads.
    prompt = _format_pantry(ingredients)
    # Make the call. `envelope` is the CLI's JSON response, already parsed into a dict.
    envelope = runner(prompt, schema, system=system)

    # --- The determinism gate: never trust the LLM's output as-is. ---

    # 1. The CLI flagged a content-level error -> nothing usable came back.
    if envelope.get("is_error"):
        raise RecipeSynthesisError("Claude did not return a usable recipe query")

    # 2. Find the payload. Primary spot is "structured_output" (already a dict).
    payload = envelope.get("structured_output")
    if payload is None:
        # Fallback: the same object is mirrored as a JSON *string* in "result".
        # `.get` gives None if the key is missing; the isinstance check also rules
        # out a non-string value — either way there's nothing usable to parse.
        raw = envelope.get("result")
        if not isinstance(raw, str):
            raise RecipeSynthesisError("Claude did not return a usable recipe query")
        # json.loads turns the JSON string back into a dict (raises on bad JSON).
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RecipeSynthesisError("Claude did not return a usable recipe query") from exc

    # 3. The gate itself: validate the dict against RecipeQuery. It either returns a
    # guaranteed-valid RecipeQuery or raises ValidationError, which we wrap in our
    # own error. `from exc` keeps the original cause attached for debugging.
    try:
        return RecipeQuery.model_validate(payload)
    except ValidationError as exc:
        raise RecipeSynthesisError("Claude returned a query that failed validation") from exc
