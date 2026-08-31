"""Web-enabled transport seam to the Claude Code CLI (headless, subscription-billed).

Source #2 ("what's hot right now") needs the model to search + read the live web, so this
transport turns the CLI's WebSearch/WebFetch tools ON. It reuses the exact subprocess +
failure-mapping body from `claude_cli._invoke_claude`, so it satisfies the same `ClaudeRunner`
Protocol and raises the same `ClaudeCliError` — only the argv and timeout differ.

Build-spike finding (2026-08-30): in headless `-p` mode, `--tools "WebSearch,WebFetch"` makes
the tools *available* but the permission layer still auto-DENIES them (→ 0 web requests, empty
results). You must ALSO auto-approve them with `--allowedTools "WebSearch WebFetch"`. Confirmed
working: ~120 s / ~14 turns, $0 real cost on the subscription (ANTHROPIC_API_KEY is scrubbed).
"""

from __future__ import annotations

import json

from pantry_pilot.core.claude_cli import _invoke_claude

CLAUDE_WEB_TIMEOUT_S = 180  # agentic: multiple search/fetch turns, so longer than run_claude


def run_claude_web(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
    """Invoke `claude -p` with web tools ON and return the parsed JSON envelope."""
    argv = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", "opus",
        "--append-system-prompt", system,
        "--tools", "WebSearch,WebFetch",          # tools AVAILABLE (comma-list)
        "--allowedTools", "WebSearch WebFetch",   # tools AUTO-APPROVED in headless mode
    ]
    return _invoke_claude(argv, prompt, timeout=CLAUDE_WEB_TIMEOUT_S)
