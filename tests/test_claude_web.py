"""Tests for the web-enabled claude-CLI transport (monkeypatches subprocess; never shells out).

Mirrors test_claude_cli.py: run_claude_web reuses the shared _invoke_claude body, so the only
web-specific things to prove are (a) the argv turns web tools ON and auto-approves them, and
(b) the failure -> .kind mapping is inherited unchanged.
"""

import subprocess

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError
from pantry_pilot.core.claude_web import CLAUDE_WEB_TIMEOUT_S, run_claude_web

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"recipes": {"type": "array"}},
    "required": ["recipes"],
}
_OK = '{"is_error": false, "structured_output": {"recipes": []}}'


class _FakeRun:
    """Records the subprocess.run call and returns a canned CompletedProcess."""

    def __init__(self, *, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self._cp = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return self._cp


def test_argv_enables_web_tools_and_scrubs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
    fake = _FakeRun(stdout=_OK)
    monkeypatch.setattr(subprocess, "run", fake)
    run_claude_web("best chicken dinner trending 2026-08", _SCHEMA, system="persona")
    argv, kwargs = fake.calls[0]
    assert argv[:2] == ["claude", "-p"]
    # Web tools available (comma-list) AND auto-approved in headless mode (space-list).
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch WebFetch"
    assert argv[argv.index("--model") + 1] == "opus"
    assert kwargs["input"] == "best chicken dinner trending 2026-08"  # prompt on stdin
    assert kwargs["timeout"] == CLAUDE_WEB_TIMEOUT_S == 180
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "ANTHROPIC_API_KEY" not in env  # billing guard


def test_success_returns_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeRun(stdout=_OK))
    env = run_claude_web("p", _SCHEMA, system="s")
    assert env["structured_output"] == {"recipes": []}


def test_missing_binary_maps_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude_web("p", _SCHEMA, system="s")
    assert exc.value.kind == "not_found"


@pytest.mark.parametrize(
    ("stderr", "kind"),
    [
        ("please run claude auth login", "auth"),
        ("Error: rate limit exceeded", "quota"),
        ("segfault", "failed"),
    ],
)
def test_nonzero_exit_maps_kind(monkeypatch: pytest.MonkeyPatch, stderr: str, kind: str) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeRun(returncode=1, stderr=stderr))
    with pytest.raises(ClaudeCliError) as exc:
        run_claude_web("p", _SCHEMA, system="s")
    assert exc.value.kind == kind


def test_non_json_stdout_maps_bad_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeRun(stdout="not json"))
    with pytest.raises(ClaudeCliError) as exc:
        run_claude_web("p", _SCHEMA, system="s")
    assert exc.value.kind == "bad_output"
