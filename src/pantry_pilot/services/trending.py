"""Source #2 ("what's hot right now") — the agentic web recipe fetcher (WAT "tool").

The conceptual core of source #2: turn a TrendingQuery into a web search, validate the model's
reply into Recipes (the determinism gate), and keep only cookable recipes from vetted sources.
Transport is injected (a ClaudeRunner) so every test runs offline against a canned envelope.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date
from typing import Literal

from pydantic import ValidationError

from pantry_pilot.core.claude_cli import ClaudeRunner
from pantry_pilot.core.claude_web import run_claude_web
from pantry_pilot.core.recipe_sources import ALLOW_DOMAINS, BLOCK_DOMAINS
from pantry_pilot.models.schemas import Recipe, TrendingQuery, TrendingResults

TrendingErrorKind = Literal["llm_failed", "bad_output"]


class TrendingRecipeError(Exception):
    """Content/validation failure turning the web model's reply into Recipes (not transport)."""

    def __init__(self, message: str, *, kind: TrendingErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def _domain(url: str) -> str:
    """Bare host of a URL, minus a leading 'www.' (suffix-matching is a later refinement)."""
    return urllib.parse.urlparse(url).netloc.removeprefix("www.")


def _to_search_terms(query: TrendingQuery, *, month: str) -> str:
    """Turn a TrendingQuery into a web-search string (month injected for deterministic tests)."""
    parts = [f"best {query.theme or 'recipes'}"]
    if query.cuisine:
        parts.append(query.cuisine)
    if query.meal_type:
        parts.append(query.meal_type)
    parts.append(f"trending {month}")
    if query.max_minutes:
        parts.append(f"under {query.max_minutes} minutes")
    return " ".join(parts)


def _persona() -> str:
    """System prompt steering the model to genuinely-trending recipes on vetted free sites."""
    allow = ", ".join(sorted(ALLOW_DOMAINS))
    block = ", ".join(sorted(BLOCK_DOMAINS))
    return (
        "You find recipes that are genuinely popular or trending right now "
        "(roughly the last month). "
        f"Use ONLY these free, readable sites for source_url: {allow}. "
        f"NEVER use paywalled sites, especially: {block}. "
        "Copy the real ingredients and numbered steps from the page verbatim — "
        "do NOT invent or paraphrase. "
        "Always include the exact source_url; do NOT output an id. "
        "Omit any recipe you cannot read in full."
    )


def _parse_trending(envelope: dict[str, object]) -> list[Recipe]:
    """The determinism gate: envelope -> list[Recipe], else raise TrendingRecipeError.

    Reads the primary `structured_output`, falling back to the `result` JSON-string mirror;
    validates the payload against TrendingResults before any recipe is trusted.
    """
    if envelope.get("is_error"):
        raise TrendingRecipeError("web fetch reported an error", kind="llm_failed")

    payload = envelope.get("structured_output")
    if payload is None:
        raw = envelope.get("result")
        if not isinstance(raw, str):
            raise TrendingRecipeError("no usable trending payload", kind="bad_output")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TrendingRecipeError("trending result was not JSON", kind="bad_output") from exc

    try:
        results = TrendingResults.model_validate(payload)
    except ValidationError as exc:
        raise TrendingRecipeError("trending payload failed validation", kind="bad_output") from exc
    return results.recipes


def _filter(recipes: list[Recipe]) -> list[Recipe]:
    """THE allow-gate: keep only creditable, cookable recipes from vetted (allow-listed) sources."""
    out: list[Recipe] = []
    for r in recipes:
        if not r.source_url or not r.steps:  # uncreditable / uncookable
            continue
        if _domain(r.source_url) not in ALLOW_DOMAINS:  # not a vetted free source
            continue
        out.append(r)
    return out


def find_trending(
    query: TrendingQuery,
    *,
    month: str | None = None,
    fetcher: ClaudeRunner | None = None,
) -> list[Recipe]:
    """Find currently-trending recipes off vetted free sites, validated into Recipes.

    `fetcher` and `month` are injectable for deterministic offline tests; production defaults to
    the web-enabled transport and the current month. Empty results are NOT an error -> [].
    """
    fetcher = fetcher or run_claude_web
    month = month or date.today().strftime("%Y-%m")
    prompt = _to_search_terms(query, month=month)
    envelope = fetcher(prompt, TrendingResults.model_json_schema(), system=_persona())
    return _filter(_parse_trending(envelope))
