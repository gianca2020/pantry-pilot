"""Transport seam to the Claude Code CLI (headless, subscription-billed).

Replaces the old Anthropic-SDK `core/llm.py`. The runner owns *transport* (build argv,
run the subprocess, parse stdout); the synthesizer owns *contract + validation*.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

CLAUDE_TIMEOUT_S = 120

ClaudeErrorKind = Literal["not_found", "auth", "quota", "timeout", "bad_output", "failed"]


class ClaudeCliError(Exception):
    """Transport / infrastructure failure invoking the claude CLI."""

    def __init__(self, message: str, *, kind: ClaudeErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


class ClaudeRunner(Protocol):
    def __call__(
        self, prompt: str, schema: dict[str, object], *, system: str
    ) -> dict[str, object]: ...


def _repo_root() -> Path:
    """Walk up from this file to the dir containing pyproject.toml; else fall back to cwd."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def _scrubbed_env() -> dict[str, str]:
    """Full environment minus ANTHROPIC_API_KEY, so inference can only bill the subscription."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def run_claude(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
    """Invoke `claude -p` headless and return the parsed JSON envelope."""
    argv = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", "opus",
        "--append-system-prompt", system,
        "--tools", "",
    ]
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            cwd=_repo_root(),
            env=_scrubbed_env(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClaudeCliError(
            "claude CLI not found — install Claude Code", kind="not_found"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"Claude timed out after {CLAUDE_TIMEOUT_S}s", kind="timeout") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        low = stderr.lower()
        if "auth" in low or "login" in low or "log in" in low:
            raise ClaudeCliError("Claude is not authenticated (run `claude auth`)", kind="auth")
        if "rate limit" in low or "quota" in low or "overloaded" in low:
            raise ClaudeCliError("Claude quota or rate limit reached", kind="quota")
        raise ClaudeCliError(f"Claude failed: {stderr[:200]}", kind="failed")

    try:
        envelope: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCliError("Claude returned unreadable output", kind="bad_output") from exc
    if not isinstance(envelope, dict):
        raise ClaudeCliError("Claude returned unreadable output", kind="bad_output")
    return envelope
