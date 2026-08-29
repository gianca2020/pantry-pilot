# CLI LLM Boundary (Shape Y v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Learning-first override (CLAUDE.md §0):** the author hand-writes the conceptual core (Task 2 — the synthesizer parse/validate + its tests); Claude writes plumbing (Tasks 1, 3, 4). Type-every-line, just-in-time. This overrides fully-autonomous execution for Task 2.

**Goal:** Route PantryPilot's recipe-query synthesis through the Claude Code CLI (`claude -p`, subscription-billed), replacing the Anthropic SDK path, with the determinism boundary preserved.

**Architecture:** A new transport seam `core/claude_cli.py` shells out to `claude -p --json-schema … --output-format json` (list-form argv, prompt on stdin, `ANTHROPIC_API_KEY` scrubbed) and returns the parsed JSON envelope. `synthesize_recipe_query` extracts `structured_output` and re-validates with Pydantic (belt + suspenders). The SDK, `core/llm.py`, the `anthropic_api_key` setting, `tests/test_llm.py`, and the `anthropic` dependency are removed. Macro goals are dropped for v1 (deferred to GH #4).

**Tech Stack:** Python ≥3.12, uv, Typer, Pydantic / pydantic-settings, pytest, mypy (strict), ruff. Subprocess to the local `claude` CLI (Claude Code 2.1.197).

**Spec:** `docs/design/cli-llm-boundary.md`

## Global Constraints

- Python **≥3.12**; run everything via `uv run …`.
- `uv run mypy src` (strict) must pass; `uv run ruff check` clean (line-length 100).
- **Determinism boundary (non-negotiable):** every LLM output passes `RecipeQuery.model_validate()` before use.
- **Billing:** all inference bills the Claude **subscription**; the subprocess env must never contain `ANTHROPIC_API_KEY`.
- Subprocess is **list-form argv, never `shell=True`**; prompt on **stdin**; **never `--bare`** (it breaks OAuth/subscription).
- Model via the **`--model opus`** tier alias; disable tools with **`--tools ""`**.
- **Macro goals dropped (v1):** `suggest` takes no `--goal`; `synthesize_recipe_query` takes no `goal`. (GH #4.)
- All tests stay **offline/deterministic** (inject a fake `ClaudeRunner` or monkeypatch `subprocess.run`).
- Current branch: `dev-feature-2-recipe-query-synthesizer`. Commit at the end of each task.

---

### Task 1: Transport seam — `core/claude_cli.py`

**Ownership:** Claude (plumbing).

**Files:**
- Create: `src/pantry_pilot/core/claude_cli.py`
- Test: `tests/test_claude_cli.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `CLAUDE_TIMEOUT_S: int = 120`
  - `ClaudeErrorKind = Literal["not_found", "auth", "quota", "timeout", "bad_output", "failed"]`
  - `ClaudeCliError(Exception)` — `__init__(self, message: str, *, kind: ClaudeErrorKind)`, stores `self.kind`.
  - `ClaudeRunner(Protocol)` — `__call__(self, prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]`.
  - `run_claude(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_claude_cli.py`

```python
"""Tests for the claude-CLI transport seam (monkeypatches subprocess; never shells out)."""

import subprocess

import pytest

from pantry_pilot.core import claude_cli
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
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    env = run_claude("Pantry items:\n- chicken (protein)", _SCHEMA, system="be tasty")
    assert env["structured_output"] == {"include_ingredients": ["chicken"]}


def test_argv_has_the_expected_flags_and_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(stdout=_OK_STDOUT)
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
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
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    run_claude("p", _SCHEMA, system="s")
    _, kwargs = fake.calls[0]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]  # billing can only hit the subscription


def test_missing_binary_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(claude_cli.subprocess, "run", _boom)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == "not_found"


def test_timeout_raises_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(*a: object, **k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=120)

    monkeypatch.setattr(claude_cli.subprocess, "run", _timeout)
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
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == expected_kind


@pytest.mark.parametrize("stdout", ["not json at all", "[1, 2, 3]"])
def test_non_dict_stdout_raises_bad_output(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    fake = _FakeRun(stdout=stdout)
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("p", _SCHEMA, system="s")
    assert exc.value.kind == "bad_output"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (no `claude_cli` yet).

- [ ] **Step 3: Write the implementation** — `src/pantry_pilot/core/claude_cli.py`

```python
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
        raise ClaudeCliError("claude CLI not found — install Claude Code", kind="not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"Claude timed out after {CLAUDE_TIMEOUT_S}s", kind="timeout") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        low = stderr.lower()
        if "authenticat" in low or "logged in" in low:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Quality gates**

Run: `uv run mypy src` and `uv run ruff check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/pantry_pilot/core/claude_cli.py tests/test_claude_cli.py
git commit -m "feat(llm): add claude-CLI transport seam (run_claude + error taxonomy)"
```

---

### Task 2: Rewrite the synthesizer onto the runner (drop macro goal)

**Ownership:** **Author (core logic).** Claude reviews line-by-line. Type-every-line.

**Files:**
- Modify (replace contents): `src/pantry_pilot/services/synthesizer.py`
- Modify (replace contents): `tests/test_synthesizer.py`

**Interfaces:**
- Consumes: `ClaudeRunner`, `run_claude` from `pantry_pilot.core.claude_cli`; `RecipeQuery`; `Ingredient`.
- Produces:
  - `RecipeSynthesisError(Exception)` (vanilla — single message arg).
  - `_format_pantry(ingredients: list[Ingredient]) -> str`.
  - `synthesize_recipe_query(ingredients: list[Ingredient], *, runner: ClaudeRunner | None = None) -> RecipeQuery`.

- [ ] **Step 1: Write the failing tests** — replace `tests/test_synthesizer.py` with:

```python
"""Tests for the recipe-query synthesizer (offline: inject a fake ClaudeRunner)."""

import pytest

from pantry_pilot.core.claude_cli import ClaudeCliError, ClaudeRunner
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.services.synthesizer import (
    RecipeSynthesisError,
    _format_pantry,
    synthesize_recipe_query,
)


def _chicken() -> Ingredient:
    return Ingredient(
        name="chicken",
        category=Category.PROTEIN,
        tracking_mode=TrackingMode.QUANTITY,
        base_unit=BaseUnit.GRAM,
        on_hand=800,
    )


def _spinach() -> Ingredient:
    return Ingredient(
        name="spinach",
        category=Category.GREEN,
        tracking_mode=TrackingMode.PRESENCE,
        status=StockStatus.OK,
    )


def _runner_returning(envelope: dict[str, object]) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        return envelope

    return _run


def _runner_raising(exc: Exception) -> ClaudeRunner:
    def _run(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
        raise exc

    return _run


def test_format_pantry_lists_every_item_without_a_goal() -> None:
    text = _format_pantry([_chicken(), _spinach()])
    assert "Macro goal" not in text          # goal is gone
    assert "chicken" in text and "spinach" in text


def test_returns_validated_query_from_structured_output() -> None:
    envelope = {"is_error": False, "structured_output": {"include_ingredients": ["chicken"]}}
    result = synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))
    assert result.include_ingredients == ["chicken"]


def test_falls_back_to_result_json_string() -> None:
    envelope = {"result": '{"include_ingredients": ["chicken"]}'}  # no structured_output
    result = synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))
    assert result.include_ingredients == ["chicken"]


def test_is_error_envelope_raises() -> None:
    envelope = {"is_error": True, "structured_output": {"include_ingredients": ["chicken"]}}
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))


def test_no_payload_raises() -> None:
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning({"is_error": False}))


def test_schema_invalid_payload_raises() -> None:
    # missing the required include_ingredients -> Pydantic (suspenders) catches it
    envelope = {"structured_output": {"cuisine": "italian"}}
    with pytest.raises(RecipeSynthesisError):
        synthesize_recipe_query([_chicken()], runner=_runner_returning(envelope))


def test_transport_error_propagates() -> None:
    runner = _runner_raising(ClaudeCliError("down", kind="timeout"))
    with pytest.raises(ClaudeCliError):
        synthesize_recipe_query([_chicken()], runner=runner)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_synthesizer.py -v`
Expected: FAIL — old signature (`goal` required) / old `_format_pantry` arity.

- [ ] **Step 3: Write the implementation** — replace `src/pantry_pilot/services/synthesizer.py` with:

```python
"""Recipe-query synthesis — the one LLM step, deterministically gated.

Deterministic Python owns pantry-read, prompt-building, and the final
`RecipeQuery.model_validate()`; the LLM call goes through an injected `ClaudeRunner`
(default: the real `run_claude`). Tests pass a fake runner, so nothing shells out.
"""

import json

from pydantic import ValidationError

from pantry_pilot.core.claude_cli import ClaudeRunner, run_claude
from pantry_pilot.models.schemas import RecipeQuery
from pantry_pilot.models.tables import Ingredient


class RecipeSynthesisError(Exception):
    """Raised when Claude fails to produce a usable, valid RecipeQuery."""


def _format_pantry(ingredients: list[Ingredient]) -> str:
    """Turn the pantry into a plain-text block for the LLM to read."""
    lines = ["Pantry items:"]
    for item in ingredients:
        lines.append(f"- {item.name} ({item.category.value})")
    return "\n".join(lines)


def synthesize_recipe_query(
    ingredients: list[Ingredient],
    *,
    runner: ClaudeRunner | None = None,
) -> RecipeQuery:
    """Ask Claude to turn the pantry into a schema-validated RecipeQuery for tasty meals."""
    runner = runner or run_claude
    system = (
        "You convert a kitchen pantry into a recipe search query for meals that taste good. "
        "Use only ingredients present in the pantry for include_ingredients."
    )
    schema = RecipeQuery.model_json_schema()
    prompt = _format_pantry(ingredients)
    envelope = runner(prompt, schema, system=system)

    if envelope.get("is_error"):
        raise RecipeSynthesisError("Claude did not return a usable recipe query")

    payload = envelope.get("structured_output")
    if payload is None:
        try:
            payload = json.loads(envelope["result"])
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise RecipeSynthesisError("Claude did not return a usable recipe query") from exc

    try:
        return RecipeQuery.model_validate(payload)
    except ValidationError as exc:
        raise RecipeSynthesisError("Claude returned a query that failed validation") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_synthesizer.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Quality gates**

Run: `uv run mypy src` and `uv run ruff check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/pantry_pilot/services/synthesizer.py tests/test_synthesizer.py
git commit -m "feat(synthesizer): synthesize via claude-CLI runner; drop macro goal (GH #4)"
```

---

### Task 3: CLI + config cleanup; delete the SDK path

**Ownership:** Claude (plumbing).

**Files:**
- Modify: `src/pantry_pilot/cli.py` (the `suggest` command + imports)
- Modify: `src/pantry_pilot/core/config.py` (remove `anthropic_api_key`)
- Delete: `src/pantry_pilot/core/llm.py`
- Delete: `tests/test_llm.py`
- Modify: `tests/test_cli.py` (drop `--goal`)
- Modify: `tests/test_config.py` (remove the two `anthropic_api_key` tests)

**Interfaces:**
- Consumes: `RecipeSynthesisError`, `synthesize_recipe_query` (Task 2); `ClaudeCliError` (Task 1).

- [ ] **Step 1: Update the CLI-command test first** — in `tests/test_cli.py`, change the invocation line:

```python
    result = CliRunner().invoke(app, ["suggest"])   # was: ["suggest", "--goal", "protein"]
```

- [ ] **Step 2: Remove the two obsolete config tests** — delete `test_anthropic_api_key_reads_standard_env_var` and `test_anthropic_api_key_defaults_to_empty` from `tests/test_config.py` (lines 40–55), and drop the now-unused `from pathlib import Path` **only if** no remaining test uses `Path` (it does — keep it). Leave the other three tests untouched.

- [ ] **Step 3: Run the two tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_config.py -v`
Expected: `test_cli.py` FAILS (suggest still requires `--goal`); `test_config.py` PASSES (we only removed tests).

- [ ] **Step 4: Edit `src/pantry_pilot/cli.py`** — three edits:

Remove the import (line 9):
```python
import anthropic
```
Add to the synthesizer import (line 24) a sibling import above it:
```python
from pantry_pilot.core.claude_cli import ClaudeCliError
```
Replace the whole `suggest` command body (lines 146–175) with:
```python
@app.command()
def suggest() -> None:
    """Turn your pantry into a recipe-search query (the Phase-2a LLM step).

    WHAT: reads your pantry, asks Claude to synthesize a structured RecipeQuery, prints it.
    WHY:  it is the one non-deterministic step in the pipeline — everything around it
          (reading the pantry, printing) is plain deterministic code.
    """
    # Read the pantry deterministically, then close the DB session BEFORE the LLM call.
    with get_session() as session:
        ingredients = list_ingredients(session)

    # The LLM step. Any failure becomes a clean one-line message + exit 1 (never a traceback):
    #   RecipeSynthesisError - Claude returned nothing usable / schema-invalid
    #   ClaudeCliError       - claude not installed / not logged in / timeout / quota / bad output
    try:
        query = synthesize_recipe_query(ingredients)
    except (RecipeSynthesisError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold]Recipe query from your pantry:[/bold]")
    console.print_json(query.model_dump_json())
```
(`Category` stays imported — `add`/`list` still use it.)

- [ ] **Step 5: Edit `src/pantry_pilot/core/config.py`** — remove the `anthropic_api_key` field and its `Field` import usage. Result:

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANTRY_", env_file=".env")

    db_path: Path = Path("data/pantry.db")
    echo_sql: bool = False

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"
```
(Drop the now-unused `from pydantic import Field` import.)

- [ ] **Step 6: Delete the dead SDK files**

```bash
git rm src/pantry_pilot/core/llm.py tests/test_llm.py
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (no import errors; `suggest` works without `--goal`).

- [ ] **Step 8: Quality gates**

Run: `uv run mypy src` and `uv run ruff check`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(cli,config): drop SDK path + --goal; catch ClaudeCliError"
```

---

### Task 4: Drop the `anthropic` dependency + full verification

**Ownership:** Claude (plumbing).

**Files:**
- Modify: `pyproject.toml` (remove `anthropic>=1.1.0` from `dependencies`)

- [ ] **Step 1: Guard — prove nothing imports `anthropic`**

Run: `grep -rn "anthropic" src tests`
Expected: **no matches** (if any remain, fix before removing the dep).

- [ ] **Step 2: Remove the dependency line** from `pyproject.toml`'s `[project].dependencies` (the `"anthropic>=1.1.0",` entry).

- [ ] **Step 3: Re-lock and sync**

Run: `uv lock && uv sync`
Expected: lockfile updates; `anthropic` no longer resolved.

- [ ] **Step 4: Full green-bar verification**

Run: `uv run pytest -v` · `uv run mypy src` · `uv run ruff check`
Expected: all pass/clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): drop anthropic SDK (synthesis now runs via claude CLI)"
```

---

### Task 5: Docs — ADR 0007, SOP rewrite, playbook log

**Ownership:** Claude drafts; author reviews.

**Files:**
- Create: `docs/adr/0007-llm-boundary-via-claude-cli.md`
- Modify: `docs/adr/0006-llm-boundary-and-structured-output.md` (mark Superseded)
- Modify: `workflows/01-query-synthesis.md` (rewrite the boundary + edge-case table; remove the false `ANTHROPIC_API_KEY → RuntimeError` claim)
- Modify: `docs/elephant-goldfish-playbook.md` (session-log entry)

- [ ] **Step 1: Write ADR 0007** capturing: full replacement (no dual-mode), raw CLI (not Agent SDK), Shape Y, `--model opus` alias, belt+suspenders determinism, env-scrub billing guard, **never `--bare`**, `anthropic` dep removed, macro goals deferred (GH #4). Reference `docs/design/cli-llm-boundary.md`.

- [ ] **Step 2: Mark ADR 0006 `Status: Superseded by 0007`** (one-line edit at its status field).

- [ ] **Step 3: Rewrite `workflows/01-query-synthesis.md`** — step 3 = "call `claude -p` via `run_claude`"; step 4 = "Pydantic `model_validate` gate"; edge-case table = the §3.8 taxonomy (`not_found`/`auth`/`timeout`/`quota`/`bad_output`/`failed` + `RecipeSynthesisError`); Inputs = "a logged-in `claude` CLI on PATH" (remove `ANTHROPIC_API_KEY`); note macro goals dropped.

- [ ] **Step 4: Add a playbook session-log entry** (2026-08-29) summarizing the Elephant→Goldfish×2→build loop and the model-tier decision (`--model opus`).

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0007-llm-boundary-via-claude-cli.md docs/adr/0006-llm-boundary-and-structured-output.md workflows/01-query-synthesis.md docs/elephant-goldfish-playbook.md
git commit -m "docs: ADR 0007 (claude-CLI boundary) supersedes 0006; rewrite SOP 01 + playbook"
```

---

## Post-build (manual, spends a sliver of quota)

Not a code task — the real smoke test from spec §7:
- Seed a pantry (`pantry add chicken -c protein …`), run `pantry suggest`, confirm it prints a validated `RecipeQuery` JSON and exits 0. Grade the output against spec §4 (only-pantry ingredients, tasty keywords, no hallucinations).
- Failure check: temporarily hide `claude` on `PATH` → expect "claude CLI not found", exit 1.
- Build-spike (optional): measure token overhead with `--debug`; test whether a `SKILL.md` auto-invokes under headless `-p` (spec §3.9).

---

## Self-Review (against `docs/design/cli-llm-boundary.md`)

- **Spec coverage:** §3.1 seam → T1. §3.2 synthesizer/persona → T2. §3.3/3.6/3.7 argv/env/timeout → T1. §3.4 `--model opus` → T1. §3.5 parse + gate → T1 (transport) + T2 (contract). §3.8 taxonomy → T1 + T3 (cli catch). §3.9 SKILL.md → deferred (post-build spike). §4 eval criteria → grading rubric in post-build smoke. §5 cleanup + dep drop → T3 + T4. Macro-goal drop (banner) → T2 + T3. ADR/SOP → T5. ✅ all covered.
- **Placeholder scan:** every code step has real code; no TBD/TODO. ✅
- **Type consistency:** `ClaudeRunner`/`run_claude`/`ClaudeCliError.kind`/`synthesize_recipe_query(ingredients, *, runner)`/`_format_pantry(ingredients)` names + signatures match across T1↔T2↔T3. ✅
