"""Tests for the Anthropic client factory (no network calls)."""

from types import SimpleNamespace

import pytest
from anthropic import Anthropic

from pantry_pilot.core.llm import get_client


def test_get_client_builds_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(get_client(), Anthropic)


def test_get_client_without_key_defers_to_sdk_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no configured key, get_client() builds a bare client and lets the SDK
    # resolve credentials (e.g. an `ant auth login` OAuth profile). Stub Settings
    # (so a developer's local .env can't leak a key in) and the Anthropic class
    # (so the test needs no real credentials).
    monkeypatch.setattr(
        "pantry_pilot.core.llm.Settings",
        lambda: SimpleNamespace(anthropic_api_key=""),
    )
    calls: list[dict[str, object]] = []

    def fake_anthropic(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr("pantry_pilot.core.llm.Anthropic", fake_anthropic)
    get_client()
    assert calls == [{}]  # built with no explicit api_key
