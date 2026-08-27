"""Tests for the Anthropic client factory (no network calls)."""

import pytest
from anthropic import Anthropic

from pantry_pilot.core.llm import get_client


def test_get_client_builds_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(get_client(), Anthropic)


def test_get_client_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_client()
