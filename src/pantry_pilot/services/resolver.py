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
import re
from typing import Literal

from pydantic import ValidationError
from sqlmodel import Session

from pantry_pilot.core.claude_cli import ClaudeRunner, claude_runner
from pantry_pilot.core.models import RESOLVE_MODEL
from pantry_pilot.models.enums import StockStatus, TrackingMode
from pantry_pilot.models.schemas import (
    CookResult,
    IngredientMatch,
    Recipe,
    RecipeFit,
    RecipeResolution,
)
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.pantry import get_ingredient, set_status

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


# A per-ingredient price token some sites embed in the line, e.g. "3 tbsp oyster sauce ($0.30)".
_PRICE = re.compile(r"\s*\(\$[^)]*\)")


def _clean_ingredient_lines(lines: list[str]) -> list[str]:
    """Strip scraped noise from recipe ingredient lines *before* they become match targets.

    Recipe pages carry two kinds of junk in their ingredient lists that we never want to match,
    shop for, or count as "missing": per-ingredient price tags (e.g. budgetbytes' "($0.30)") and
    section headers ("Sauce:", "For serving:"). We drop the price token and skip any header
    (a line that ends with ':') or blank, so the LLM only ever sees real ingredients — which keeps
    the match count, the shopping list, and the ⚠ notes honest.
    """
    cleaned: list[str] = []
    for line in lines:
        line = _PRICE.sub("", line).strip()
        if not line or line.endswith(":"):  # blank or a section header -> not an ingredient
            continue
        cleaned.append(line)
    return cleaned


def _to_resolution_prompt(recipe: Recipe, pantry_names: list[str]) -> str:
    """Build the user prompt: the pantry to match against + the recipe's ingredient lines.

    Deterministic and testable — a test asserts every pantry name and every ingredient line
    appears in the output. The recipe's ingredient lines are cleaned first (prices/headers
    stripped) so the model only matches real ingredients. Empty pantry -> a clear placeholder.
    """
    pantry = ", ".join(pantry_names) or "(pantry is empty)"
    lines = "\n".join(f"- {line}" for line in _clean_ingredient_lines(recipe.ingredients or []))
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
    runner = runner or claude_runner(RESOLVE_MODEL)
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
    that isn't actually in the pantry -> that match drops to missing, and its unreal `pantry_name`
    is normalized to None (design §6 "treated as null") so `_shopping_list` says "buy:" not
    "restock". The persona *steers* to real names; assess *enforces* it. Matched-but-OUT /
    on_hand==0 also -> missing (its real `pantry_name` is kept -> "restock"). Uncertain
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
            if item is None:  # not owned: a null match OR a hallucinated (unreal) name
                m.pantry_name = None  # §6 "treated as null" -> shopping list says "buy:"
            missing.append(m)  # null / hallucinated name / OUT / empty
    return RecipeFit(recipe=recipe, have=have, missing=missing)


# --- Ranking + shopping list ---


def _uncertain(fit: RecipeFit) -> list[IngredientMatch]:
    """The ⚠ subset of `have`: stocked matches the model flagged confident=False (non-blocking)."""
    return [m for m in fit.have if not m.confident]


def _shopping_list(fit: RecipeFit) -> list[str]:
    """One line per missing item: 'restock <name>' if you own it (OUT-of-stock),
    else 'buy: <recipe line>' when it isn't in the pantry at all.
    """
    return [
        f"restock {m.pantry_name}" if m.pantry_name else f"buy: {m.recipe_ingredient}"
        for m in fit.missing
    ]


def rank_recipes(
    recipes: list[Recipe], pantry: list[Ingredient], *, runner: ClaudeRunner | None = None
) -> list[RecipeFit]:
    """Resolve + assess each recipe, then order by what you can cook tonight.

    Skips ingredient-less recipes (source #1 carries no lines to resolve) and swallows a single
    recipe's ResolutionError (content) to keep ranking the rest — a transport ClaudeCliError is
    NOT caught here and propagates (systemic). Order: fewest missing, then fewest uncertain, then
    title (stable).
    """
    fits: list[RecipeFit] = []
    for r in recipes:
        if not r.ingredients:  # can't resolve without lines (source #1) -> skip
            continue
        try:
            matches = resolve_recipe(r, [i.name for i in pantry], runner=runner)
        except ResolutionError:  # one bad recipe (content) -> skip, keep the rest
            continue
        fits.append(assess(r, matches, pantry))
    fits.sort(key=lambda f: (len(f.missing), len(_uncertain(f)), f.recipe.title))
    return fits


def _step_down(status: StockStatus) -> StockStatus:
    """Nudge a PRESENCE status one notch toward empty: OK -> LOW, LOW -> OUT, OUT -> OUT."""
    steps = {StockStatus.OK: StockStatus.LOW, StockStatus.LOW: StockStatus.OUT}
    return steps.get(status, StockStatus.OUT)


# --- State mutation: cook (ledger-honest presence-flip + quantity nudge) ---


def cook(session: Session, fit: RecipeFit) -> CookResult:
    """Adjust the pantry for a cooked recipe — only what you actually `have` gets consumed.

    PRESENCE items step down one notch (OK->LOW->OUT), each pantry item consumed at most once
    (deduped by pantry_name). QUANTITY items are *reported* in `to_update` for a manual
    `pantry use`, never auto-deducted with a fabricated amount — the event-sourced ledger stays
    truthful (D4). An item archived/renamed since resolve (get_ingredient -> None) is skipped.
    """
    flipped: list[str] = []
    to_update: list[str] = []
    seen: set[str] = set()
    for m in fit.have:
        if not m.pantry_name or m.pantry_name in seen:  # consume each pantry item at most once
            continue
        seen.add(m.pantry_name)
        item = get_ingredient(session, m.pantry_name)
        if item is None:  # archived/renamed since resolve -> skip safely
            continue
        if item.tracking_mode == TrackingMode.PRESENCE:
            new = _step_down(item.status or StockStatus.OK)
            set_status(session, item, new)
            flipped.append(f"{item.name} -> {new.value}")
        else:  # QUANTITY: never guess grams (D4)
            to_update.append(item.name)
    return CookResult(flipped=flipped, to_update=to_update)
