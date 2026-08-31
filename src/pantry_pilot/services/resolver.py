"""Phase 3 recipe resolver — "what can I cook tonight?" (WAT "tool").

The ONE non-deterministic step of Phase 3 is `resolve_recipe`: a single-shot `run_claude`
call (tools OFF) that *matches* each free-text recipe ingredient line to a canonical pantry
item name. Everything downstream — stock check, ranking, mutation — is deterministic and lives
in this module too. Transport is injected (a ClaudeRunner) so every test runs offline against
a canned envelope; nothing shells out.

Mirrors `services/trending.py`: same two-class error split (transport ClaudeCliError vs. this
module's content-level ResolutionError) and the same parse+validate gate pattern.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import ValidationError

from pantry_pilot.core.claude_cli import ClaudeRunner, run_claude
from pantry_pilot.models.enums import StockStatus, TrackingMode
from pantry_pilot.models.schemas import IngredientMatch, Recipe, RecipeFit, RecipeResolution
from pantry_pilot.models.tables import Ingredient

ResolutionErrorKind = Literal["llm_failed", "bad_output"]


class ResolutionError(Exception):
    """Content/validation failure turning the model's reply into matches (not transport).

    Transport failures (claude missing / auth / timeout) stay `ClaudeCliError`; this is the
    content-level twin — an `is_error` envelope (`llm_failed`) or a payload that isn't the
    shape we asked for (`bad_output`).
    """

    def __init__(self, message: str, *, kind: ResolutionErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def _to_resolution_prompt(recipe: Recipe, pantry_names: list[str]) -> str:
    """Build the user prompt: the pantry to match against + the recipe's ingredient lines.

    Deterministic and testable — a test asserts every pantry name and every ingredient line
    appears in the output. Empty pantry -> a clear placeholder; no ingredients -> an empty list.
    """
    pantry = ", ".join(pantry_names) or "(pantry is empty)"
    lines = "\n".join(f"- {line}" for line in (recipe.ingredients or []))
    return (
        f"PANTRY ITEMS (match ONLY against these exact names):\n{pantry}\n\n"
        f"RECIPE: {recipe.title}\n"
        f"RECIPE INGREDIENTS (return one match per line, in order):\n{lines}"
    )


def _resolver_persona() -> str:
    """System prompt: match verbatim to a pantry name or null, and be honest about confidence."""
    return (
        "Match each recipe ingredient line to exactly one pantry item name from the provided "
        "list, or null if the pantry has nothing suitable. Use the pantry names verbatim — NEVER "
        "invent a name that isn't in the list. When the two are similar but not clearly the same, "
        "set confident to false and say why in note. Return exactly one entry per recipe "
        "ingredient line."
    )


def _parse_resolution(envelope: dict[str, object]) -> list[IngredientMatch]:
    """The determinism gate: envelope -> list[IngredientMatch], else raise ResolutionError.

    Reads the primary `structured_output`, falling back to the `result` JSON-string mirror;
    validates the payload against RecipeResolution before any match is trusted. No cardinality
    check — a reply with fewer/more matches than lines still parses (unmatched lines just aren't
    assessed); only the envelope + schema *shape* is gated.
    """
    if envelope.get("is_error"):
        raise ResolutionError("resolver reported an error", kind="llm_failed")

    payload = envelope.get("structured_output")
    if payload is None:
        raw = envelope.get("result")
        if not isinstance(raw, str):
            raise ResolutionError("no usable resolution payload", kind="bad_output")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResolutionError("resolution result was not JSON", kind="bad_output") from exc

    try:
        resolution = RecipeResolution.model_validate(payload)
    except ValidationError as exc:
        raise ResolutionError("resolution payload failed validation", kind="bad_output") from exc
    return resolution.matches


def resolve_recipe(
    recipe: Recipe, pantry_names: list[str], *, runner: ClaudeRunner | None = None
) -> list[IngredientMatch]:
    """Match one recipe's ingredient lines to pantry item names (the single LLM step).

    `runner` is injectable for deterministic offline tests; production defaults to the plain
    (tools-OFF) transport. Returns one IngredientMatch per line the model resolved.
    """
    runner = runner or run_claude
    prompt = _to_resolution_prompt(recipe, pantry_names)
    envelope = runner(prompt, RecipeResolution.model_json_schema(), system=_resolver_persona())
    return _parse_resolution(envelope)


# --- The deterministic gate: assess (hallucination guard + stock check) ---


def _in_stock(item: Ingredient) -> bool:
    """Does this pantry row count as on-hand? QUANTITY: on_hand > 0; PRESENCE: not OUT."""
    if item.tracking_mode == TrackingMode.QUANTITY:
        return (item.on_hand or 0) > 0  # None or 0 -> not in stock
    return item.status != StockStatus.OUT  # PRESENCE: OK or LOW counts as have


def assess(recipe: Recipe, matches: list[IngredientMatch], pantry: list[Ingredient]) -> RecipeFit:
    """Split a recipe's matches into have vs. missing against the real pantry.

    The hallucination guard: `by_name.get(pantry_name)` is None for any name the model invented
    that isn't actually in the pantry -> that match drops to missing. The persona *steers* to
    real names; assess *enforces* it. Matched-but-OUT / on_hand==0 also -> missing. Uncertain
    (confident=False) but stocked matches still count as have (surfaced ⚠ later, never blocked).
    """
    by_name = {i.name: i for i in pantry}
    have: list[IngredientMatch] = []
    missing: list[IngredientMatch] = []
    for m in matches:
        item = by_name.get(m.pantry_name) if m.pantry_name else None  # guard: name must be real
        if item is not None and _in_stock(item):
            have.append(m)
        else:
            missing.append(m)  # null / hallucinated name / OUT / empty
    return RecipeFit(recipe=recipe, have=have, missing=missing)
