"""Tests for ADR 0012 (per-step model tiering): runner factories + tier-policy wiring.

RED FIRST: these fail until claude_runner/claude_web_runner/core/models.py exist and the three
tool defaults (synthesizer, resolver, trending) are wired to the tiered factories.

Every test monkeypatches `_invoke_claude` (never subprocess) and asserts the argv the transport
built — nothing shells out. CRITICAL subtlety for the web tests: claude_web.py does
`from ...claude_cli import _invoke_claude`, binding the name into claude_web's own namespace, so
those tests must patch `pantry_pilot.core.claude_web._invoke_claude`, not the claude_cli one.
"""

from __future__ import annotations

import pytest

import pantry_pilot.core.claude_cli as claude_cli
import pantry_pilot.core.claude_web as claude_web
from pantry_pilot.core.claude_cli import claude_runner, run_claude
from pantry_pilot.core.claude_web import claude_web_runner, run_claude_web
from pantry_pilot.core.models import RESOLVE_MODEL, SYNTH_MODEL, TRENDING_MODEL
from pantry_pilot.models.enums import BaseUnit, Category, TrackingMode
from pantry_pilot.models.schemas import Recipe, TrendingQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.resolver import resolve_recipe
from pantry_pilot.services.synthesizer import synthesize_recipe_query
from pantry_pilot.services.trending import find_trending

_SCHEMA: dict[str, object] = {"type": "object"}


def _capture_invoke(
    monkeypatch: pytest.MonkeyPatch, target: object, envelope: dict[str, object]
) -> list[list[str]]:
    """Monkeypatch `_invoke_claude` on `target` to record argv and return a canned envelope."""
    calls: list[list[str]] = []

    def _fake(argv: list[str], prompt: str, *, timeout: int) -> dict[str, object]:
        calls.append(argv)
        return envelope

    monkeypatch.setattr(target, "_invoke_claude", _fake)
    return calls


# --- claude_runner (core/claude_cli.py) ---


def test_claude_runner_bakes_model_into_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_invoke(monkeypatch, claude_cli, {"is_error": False})
    runner = claude_runner("haiku")
    runner("prompt", _SCHEMA, system="sys")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_claude_runner_default_is_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_invoke(monkeypatch, claude_cli, {"is_error": False})
    runner = claude_runner()
    runner("prompt", _SCHEMA, system="sys")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "opus"


def test_run_claude_back_compat_uses_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_invoke(monkeypatch, claude_cli, {"is_error": False})
    run_claude("prompt", _SCHEMA, system="sys")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "opus"


# --- claude_web_runner (core/claude_web.py) ---


def test_claude_web_runner_bakes_model_and_web_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_invoke(monkeypatch, claude_web, {"is_error": False})
    runner = claude_web_runner("sonnet")
    runner("prompt", _SCHEMA, system="sys")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch WebFetch"


def test_run_claude_web_back_compat_uses_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_invoke(monkeypatch, claude_web, {"is_error": False})
    run_claude_web("prompt", _SCHEMA, system="sys")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "opus"


# --- Tier policy constants (core/models.py) ---


def test_tier_policy_constants() -> None:
    assert SYNTH_MODEL == "haiku"
    assert RESOLVE_MODEL == "haiku"
    assert TRENDING_MODEL == "sonnet"


# --- Wiring: each tool's default runner requests its tiered model ---


def _chicken() -> Ingredient:
    return Ingredient(
        name="chicken",
        category=Category.PROTEIN,
        tracking_mode=TrackingMode.QUANTITY,
        base_unit=BaseUnit.GRAM,
        on_hand=800,
    )


def test_synthesizer_default_runner_requests_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"is_error": False, "structured_output": {"include_ingredients": ["chicken"]}}
    calls = _capture_invoke(monkeypatch, claude_cli, envelope)
    synthesize_recipe_query([_chicken()])
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_resolver_default_runner_requests_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"is_error": False, "structured_output": {"matches": []}}
    calls = _capture_invoke(monkeypatch, claude_cli, envelope)
    resolve_recipe(Recipe(title="X", ingredients=["2 lb chicken"]), ["chicken"])
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_trending_default_fetcher_requests_sonnet(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {"is_error": False, "structured_output": {"recipes": []}}
    calls = _capture_invoke(monkeypatch, claude_web, envelope)
    find_trending(TrendingQuery(theme="x"), month="2026-09")
    argv = calls[0]
    assert argv[argv.index("--model") + 1] == "sonnet"
