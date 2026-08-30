"""Tests for the claude-CLI transport seam (monkeypatches subprocess; never shells out)."""

import subprocess

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, run_claude

_SCHEMA: dict[str, object] = {"type": "object"}
_OK_STDOUT = '{"is_error": false, "structured_output": {"include_ingredients": ["chicken"]}}'


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


def test_returns_parsed_envelope_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(stdout=_OK_STDOUT)
    monkeypatch.setattr(subprocess, "run", fake)
    env = run_claude("Pantry items:\n- chicken (protein)", _SCHEMA, system="be tasty")
    assert env["structured_output"] == {"include_ingredients": ["chicken"]}


def test_argv_has_the_expected_flags_and_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(stdout=_OK_STDOUT)
    monkeypatch.setattr(subprocess, "run", fake)
    run_claude("PROMPT-TEXT", _SCHEMA, system="SYS")
    argv, kwargs = fake.calls[0]
    assert argv[:2] == ["claude", "-p"]
    assert "--output-format" in argv and "json" in argv
    assert "--model" in argv and "opus" in argv
    assert "--tools" in argv and "" in argv
    assert "--append-system-prompt" in argv and "SYS" in argv
    assert kwargs["input"] == "PROMPT-TEXT"          # prompt on stdin
    assert "PROMPT-TEXT" not in argv                  # never in argv


def test_scrubs_anthropic_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    fake = _FakeRun(stdout=_OK_STDOUT)
    monkeypatch.setattr(subprocess, "run", fake)
    run_claude("p", _SCHEMA, system="s")
    _, kwargs = fake.calls[0]
    env = kwargs["env"]
    assert isinstance(env, dict)  # narrow object -> dict for the membership check
    assert "ANTHROPIC_API_KEY" not in env  # billing can only hit the subscription


def test_missing_binary_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == "not_found"


def test_timeout_raises_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(*a: object, **k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=120)

    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == "timeout"


@pytest.mark.parametrize(
    ("stderr", "expected_kind"),
    [
        ("Please run claude auth login", "auth"),
        ("Error: rate limit exceeded", "quota"),
        ("segfault somewhere", "failed"),
    ],
)
def test_nonzero_exit_maps_stderr_to_kind(
    monkeypatch: pytest.MonkeyPatch, stderr: str, expected_kind: str
) -> None:
    fake = _FakeRun(returncode=1, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == expected_kind


@pytest.mark.parametrize("stdout", ["not json at all", "[1, 2, 3]"])
def test_non_dict_stdout_raises_bad_output(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    fake = _FakeRun(stdout=stdout)
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == "bad_output"
