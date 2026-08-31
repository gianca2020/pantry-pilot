"""Tests for source #2 ("what's hot right now") — services/trending.py.

Everything here is OFFLINE: no network, no LLM. Where a test needs model output it injects a
fake ClaudeRunner returning a canned envelope; the fixture is the inner structured_output.
"""

import json
from pathlib import Path

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, ClaudeRunner
from pantry_pilot.core.recipe_sources import ALLOW_DOMAINS, BLOCK_DOMAINS
from pantry_pilot.models.schemas import Recipe, TrendingQuery
from pantry_pilot.services.trending import (
    TrendingRecipeError,
    _domain,
    _filter,
    _parse_trending,
    _persona,
    _to_search_terms,
    find_trending,
)

_FIX = Path(__file__).parent / "fixtures" / "trending_results.json"


def _inner() -> dict[str, object]:
    body = json.loads(_FIX.read_text())
    assert isinstance(body, dict)  # narrow object -> dict for mypy --strict
    return body


# --- 4a: _domain + _filter (the deterministic allow-gate) ---


def test_domain_strips_www() -> None:
    assert _domain("https://www.seriouseats.com/foo") == "seriouseats.com"
    assert _domain("https://budgetbytes.com/bar") == "budgetbytes.com"


def _r(url: str | None, steps: list[str] | None) -> Recipe:
    """A minimal Recipe for exercising _filter (ingredients present; we vary url + steps).

    Built via model_validate with the `sourceUrl` alias — source_url has a validation_alias,
    so the field-name kwarg would be silently ignored (this is how the real flow populates it).
    """
    return Recipe.model_validate(
        {"title": "t", "sourceUrl": url, "steps": steps, "ingredients": ["x"]}
    )


def test_filter_keeps_only_allowlisted_with_steps_and_url() -> None:
    keep = _r("https://www.allrecipes.com/r/1", ["step"])       # allow-listed + cookable
    block = _r("https://cooking.nytimes.com/r/2", ["step"])     # not on the allow-list
    nostep = _r("https://www.budgetbytes.com/r/3", None)        # uncookable (no steps)
    nourl = _r(None, ["step"])                                  # uncreditable (no source_url)
    assert _filter([keep, block, nostep, nourl]) == [keep]


def test_filter_empty_input_returns_empty() -> None:
    assert _filter([]) == []


# --- 4b: _to_search_terms ---


def test_search_terms_empty_query() -> None:
    assert _to_search_terms(TrendingQuery(), month="2026-08") == "best recipes trending 2026-08"


def test_search_terms_full_query() -> None:
    q = TrendingQuery(theme="chicken dinner", cuisine="thai", meal_type="dinner", max_minutes=30)
    assert (
        _to_search_terms(q, month="2026-08")
        == "best chicken dinner thai dinner trending 2026-08 under 30 minutes"
    )


# --- 4c: _persona ---


def test_persona_lists_sorted_allow_and_block_domains() -> None:
    p = _persona()
    assert ", ".join(sorted(ALLOW_DOMAINS)) in p
    assert ", ".join(sorted(BLOCK_DOMAINS)) in p


def test_persona_states_key_rules() -> None:
    p = _persona().lower()
    assert "do not output an id" in p
    assert "source_url" in p
    assert "verbatim" in p or "do not invent" in p


# --- 4d: _parse_trending (the determinism gate) ---


def test_parse_reads_structured_output() -> None:
    env = {"is_error": False, "structured_output": _inner()}
    recipes = _parse_trending(env)
    assert len(recipes) == 4
    assert recipes[0].id is None  # web recipes carry no id
    assert recipes[0].source_url == "https://www.halfbakedharvest.com/honey-garlic-chicken/"


def test_parse_falls_back_to_result_string() -> None:
    env = {"is_error": False, "result": json.dumps(_inner())}  # no structured_output key
    assert len(_parse_trending(env)) == 4


def test_parse_is_error_raises_llm_failed() -> None:
    with pytest.raises(TrendingRecipeError) as exc:
        _parse_trending({"is_error": True, "structured_output": _inner()})
    assert exc.value.kind == "llm_failed"


def test_parse_non_json_result_raises_bad_output() -> None:
    with pytest.raises(TrendingRecipeError) as exc:
        _parse_trending({"is_error": False, "result": "not json"})
    assert exc.value.kind == "bad_output"


def test_parse_unschematic_payload_raises_bad_output() -> None:
    with pytest.raises(TrendingRecipeError) as exc:
        _parse_trending({"is_error": False, "structured_output": {}})  # missing 'recipes'
    assert exc.value.kind == "bad_output"


# --- 4e: find_trending (entry point) ---


def _runner_returning(env: dict[str, object]) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return env

    return _run


def _runner_raising(exc: Exception) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise exc

    return _run


class _RecordingRunner:
    """A fake ClaudeRunner that records the prompt + system it was handed."""

    def __init__(self, env: dict[str, object]) -> None:
        self.env = env
        self.prompt: str | None = None
        self.system: str | None = None

    def __call__(
        self, prompt: str, schema: dict[str, object], *, system: str
    ) -> dict[str, object]:
        self.prompt = prompt
        self.system = system
        return self.env


def test_find_trending_filters_to_allowlisted_with_steps() -> None:
    env = {"is_error": False, "structured_output": _inner()}  # 4 in the fixture
    q = TrendingQuery(theme="chicken dinner")
    recipes = find_trending(q, month="2026-08", fetcher=_runner_returning(env))
    assert [r.title for r in recipes] == [
        "30 Minute Honey Garlic Chicken",
        "My go-to Chicken Breast recipe",
    ]  # only the 2 allow-listed + cookable survive


def test_find_trending_passes_prompt_and_persona() -> None:
    runner = _RecordingRunner({"is_error": False, "structured_output": {"recipes": []}})
    q = TrendingQuery(theme="chicken dinner")
    find_trending(q, month="2026-08", fetcher=runner)
    assert runner.prompt == _to_search_terms(q, month="2026-08")
    assert runner.system == _persona()


def test_find_trending_empty_is_not_an_error() -> None:
    env = {"is_error": False, "structured_output": {"recipes": []}}
    assert find_trending(TrendingQuery(), month="2026-08", fetcher=_runner_returning(env)) == []


def test_find_trending_transport_error_propagates() -> None:
    runner = _runner_raising(ClaudeCliError("timed out", kind="timeout"))
    with pytest.raises(ClaudeCliError):
        find_trending(TrendingQuery(), month="2026-08", fetcher=runner)
