"""Transport seam to the Spoonacular recipe API (deterministic; no LLM).

Mirrors core/claude_cli.py. This module owns *transport*: build the request, call the
network, map HTTP/network failures to a typed error, and parse JSON. The retrieval service
(services/retrieval.py) owns *contract + validation* (RecipeQuery -> params, results ->
Recipe). Tests inject a fake `RecipeFetcher`, so nothing here runs during unit tests.
"""

from __future__ import annotations

import json
from typing import Literal, Protocol

import httpx

from pantry_pilot.core.config import Settings

# The one endpoint we call. `sort=popularity` (added by the caller) is the "highly-rated" signal.
COMPLEX_SEARCH_URL = "https://api.spoonacular.com/recipes/complexSearch"
SPOONACULAR_TIMEOUT_S = 15

SpoonacularErrorKind = Literal[
    "auth", "quota", "rate_limit", "timeout", "network", "bad_output", "http_error"
]


class SpoonacularError(Exception):
    """Transport / infrastructure failure calling the Spoonacular API."""

    def __init__(self, message: str, *, kind: SpoonacularErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


# The HTTP statuses Spoonacular uses for the failures we can name; anything else non-200
# collapses to a generic `http_error`.
_STATUS_KIND: dict[int, SpoonacularErrorKind] = {
    401: "auth",        # invalid / missing API key
    402: "quota",       # daily points / quota exhausted
    429: "rate_limit",  # too many requests
}


class RecipeFetcher(Protocol):
    """The transport dependency services/retrieval.py depends on (an interface, for injection)."""

    def __call__(self, params: dict[str, str]) -> dict[str, object]: ...


def fetch_recipes(params: dict[str, str]) -> dict[str, object]:
    """GET complexSearch with the caller's params + the injected apiKey; return parsed JSON."""
    # The secret lives here in transport, never in the pure RecipeQuery->params mapping.
    key = Settings().spoonacular_api_key
    if not key:
        raise SpoonacularError(
            "no Spoonacular API key set (PANTRY_SPOONACULAR_API_KEY)", kind="auth"
        )

    try:
        response = httpx.get(
            COMPLEX_SEARCH_URL,
            params={**params, "apiKey": key},
            timeout=SPOONACULAR_TIMEOUT_S,
        )
    except httpx.TimeoutException as exc:  # a subclass of RequestError — must be caught first
        raise SpoonacularError(
            f"Spoonacular timed out after {SPOONACULAR_TIMEOUT_S}s", kind="timeout"
        ) from exc
    except httpx.RequestError as exc:  # connection refused / DNS / other network failure
        raise SpoonacularError("could not reach Spoonacular", kind="network") from exc

    status_code = response.status_code
    if status_code != 200:
        kind = _STATUS_KIND.get(status_code)
        if kind is None:
            kind = "http_error"
        raise SpoonacularError(f"Spoonacular returned HTTP {status_code}", kind=kind)

    # httpx's .json() raises the stdlib json.JSONDecodeError on a non-JSON body.
    try:
        payload: object = response.json()
    except json.JSONDecodeError as exc:
        raise SpoonacularError("Spoonacular returned unreadable output", kind="bad_output") from exc
    # Narrow Any -> dict so callers (and mypy --strict) can trust the shape.
    if not isinstance(payload, dict):
        raise SpoonacularError("Spoonacular returned unreadable output", kind="bad_output")
    return payload
