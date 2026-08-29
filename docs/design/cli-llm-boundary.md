# Elephant Design — Route recipe-query synthesis through the Claude Code CLI (subscription)

**Shape Y, v1-scoped.** Supersedes ADR 0006's `messages.parse` boundary (ADR 0007 is written *after* the build).
**Status: APPROVED 2026-08-28 (Goldfish-tested ×2 — 2nd pass 2026-08-29 clean, no blocking gaps).** This is the design-of-record for the build. No implementation code is written
until the separate build gate (§6). Verified against live code + `claude --help` on 2026-08-28.

> Written to be self-sufficient — a fresh reader with no other context should be able to implement from this doc
> alone (the Goldfish test). §2 embeds the ground-truth current code so no repo spelunking is required.

> **⚠ SCOPE CHANGE (2026-08-29):** the **macro-goal input is dropped** for v1 — `suggest` synthesizes a query for
> *food that tastes good* from the pantry **alone** (no `goal`). Macro goals become a **later feature** (tracked as
> [GH #4](https://github.com/gianca2020/pantry-pilot/issues/4)). Effect on this doc: remove the `goal: Category` param from `synthesize_recipe_query`, drop the `--goal` CLI
> option, drop the *"Macro goal"* line in `_format_pantry` (so it is **modified**, not reused-unchanged), and reword the
> persona (§3.2) + eval criteria (§4) to target tastiness. `Category` stays for *ingredient* categories.

---

## 1. Context — why this change

PantryPilot's one LLM step, `synthesize_recipe_query(pantry, goal) -> RecipeQuery`, currently calls
`client.messages.parse(..., output_format=RecipeQuery)` on an injected Anthropic SDK client billed via
**API credits** — which are blocked at a $0 balance and, more importantly, violate a hard business rule:

- **Hard constraint (non-negotiable):** *all* inference must bill the user's existing **Claude subscription** —
  **zero extra dollar spend.** (Confirmed: Claude Code is logged in via subscription; `ANTHROPIC_API_KEY` is unset.)
- **Personal-use-only** CLI and a **learning vehicle** toward FDE / senior-AI-engineer skills.
- **Determinism boundary is sacred:** LLM output must be Pydantic-validated before it touches app state (CLAUDE.md §1).

**The change:** replace that single SDK call with a subprocess invocation of the locally-installed `claude`
CLI in headless mode (`claude -p ... --output-format json --json-schema <RecipeQuery schema>`), which bills the
**subscription quota**. Pantry-read, prompt-formatting, orchestration, and the final `model_validate()` stay in
deterministic Python. The Anthropic SDK, `core/llm.py`, the `anthropic_api_key` setting, and `tests/test_llm.py`
are removed. The determinism boundary is *strengthened*: native schema constraint (belt) **plus** Pydantic (suspenders).

**Verified spike (2026-08-28):** a real `claude -p --json-schema '<RecipeQuery.model_json_schema()>' --output-format
json` call (with `ANTHROPIC_API_KEY` stripped) worked with the real Pydantic schema unmassaged, returned a schema-valid
object in the envelope's **`structured_output`** field, and billed subscription quota (it succeeded where a $0-credit
API org failed). Overhead is large — ~27K tokens / `num_turns: 2` per call (~50–60× the raw API) — **acceptable for
low-volume personal use.**

---

## 2. Current state (ground truth, for a fresh reader)

**Package & imports:** top-level package is `pantry_pilot`; imports are absolute (e.g. `from pantry_pilot.services.synthesizer import synthesize_recipe_query`). `Ingredient` → `pantry_pilot.models.tables`; `Category` → `pantry_pilot.models.enums`; `RecipeQuery` → `pantry_pilot.models.schemas`.

- `services/synthesizer.py` — `synthesize_recipe_query(ingredients: list[Ingredient], goal: Category, *, client: Anthropic | None = None) -> RecipeQuery`.
  Core: `client.messages.parse(model="claude-opus-4-8", max_tokens=1024, system=<2-line persona>, messages=[{"role":"user","content":_format_pantry(...)}], output_format=RecipeQuery)`; raises `RecipeSynthesisError` on `stop_reason=="refusal"` or `parsed_output is None`. Injection seam: `client = client or get_client()`.
- `_format_pantry(ingredients, goal) -> str` currently renders `"Macro goal: <g>\n\nPantry items:\n- name (category)"`. **Modified in v1:** drop the `goal` arg + the "Macro goal" line, so it renders only `"Pantry items:\n- name (category)"` (macro goals cut — see banner).
- `models/schemas.py::RecipeQuery(BaseModel)` — required `include_ingredients: list[str]`; optional `exclude_ingredients`, `keywords`, `cuisine`, `dish_type`, `max_ready_minutes` (all `… | None = None`). No custom validators. **Unchanged.** Schema via `RecipeQuery.model_json_schema()`.
- `core/llm.py::get_client() -> Anthropic` — **to be deleted.**
- `core/config.py::Settings` — `env_prefix="PANTRY_"`, `.env`; fields `db_path`, `echo_sql`, `anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")`, `database_url` property. **Remove `anthropic_api_key`.**
- `cli.py::suggest(--goal)` — catches `(RecipeSynthesisError, anthropic.AnthropicError)`, prints `[red]Error:[/red] {exc}`, `raise typer.Exit(1)`; prints via `console.print_json(query.model_dump_json())`.
- `tests/test_synthesizer.py` — hand-rolled `_FakeClient.messages.parse()` returning `_FakeResponse(parsed_output, stop_reason)`, injected via `client=`; uses `cast(Anthropic, …)`.
- `tests/test_llm.py` — monkeypatches `Settings`/`Anthropic`. **To be deleted/replaced.**
- `pyproject.toml` — py≥3.12; deps `anthropic>=1.1.0`, `pydantic-settings`, `sqlmodel`, `typer`; dev `pytest`, `mypy` (strict), `ruff`. Only `synthesizer.py`/`llm.py`/`cli.py` import `anthropic`.
- ADRs `0001`–`0006` exist; next free = **0007**. ADR 0006 records the `messages.parse` boundary.

---

## 3. Target design (Shape Y, v1)

### 3.1 The subprocess seam — `core/claude_cli.py` (new; replaces `core/llm.py`)

Define a `Protocol` so `synthesizer.py` depends on an interface (clean test injection, passes `mypy --strict`
without the `cast(Anthropic, …)` hack):

```python
class ClaudeRunner(Protocol):
    def __call__(self, prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]: ...
```

- **Returns `dict` (the parsed JSON envelope), not raw `str`.** The runner owns *transport* (build argv, run subprocess,
  `json.loads` stdout) and raises on transport failures; the synthesizer owns *contract + validation*. Tests then return a
  plain dict literal — no hand-serialized JSON.
- Concrete impl `run_claude(prompt, schema, *, system) -> dict`.

### 3.2 `synthesize_recipe_query` — preserve the public contract

The caller-facing contract is the **positional** `synthesize_recipe_query(ingredients)` (the only way `cli.py`
calls it — **the `goal` param is dropped**, see the scope-change banner). The keyword-only param is a **test seam**, so
rename `client=` → `runner: ClaudeRunner | None = None` (the old name would now be misleading). `_format_pantry` is
**modified** to drop its "Macro goal" line (it now renders only the pantry items with their categories). The persona
`system` string is reworded to target tasty meals while keeping the load-bearing pantry-only rule (SKILL.md is deferred, §3.9):

```python
system = (
    "You convert a kitchen pantry into a recipe search query for meals that taste good. "
    "Use only ingredients present in the pantry for include_ingredients."
)
```

```python
runner = runner or run_claude
schema = RecipeQuery.model_json_schema()
prompt = _format_pantry(ingredients)
envelope = runner(prompt, schema, system=system)      # transport
# --- contract + determinism gate (§3.5) ---
```

### 3.3 The exact `claude -p` invocation (list-form; **never `shell=True`**)

Ingredient names are user-controlled and the schema is a large JSON blob → list-form `argv` (no shell metacharacter
injection). Prompt goes on **stdin** (avoids `ARG_MAX` limits and quoting hell); schema/system go in argv.

```python
argv = [
    "claude", "-p",
    "--output-format", "json",              # single JSON envelope on stdout
    "--json-schema", json.dumps(schema),    # native structured-output constraint (belt)
    "--model", "opus",                      # tier ALIAS — stays version-agnostic (see 3.4)
    "--append-system-prompt", system,       # ADD the synthesis persona (do NOT --system-prompt / replace)
    "--tools", "",                          # disable ALL built-in tools (verified 2.1.197: '"" to disable all')
]
result = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                        timeout=CLAUDE_TIMEOUT_S, cwd=_repo_root(), env=_scrubbed_env(), check=False)
```

- **`--append-system-prompt`, not `--system-prompt`:** *append* the persona; replacing the default system prompt could
  strip the Claude Code scaffolding that makes `--json-schema` reliably populate `structured_output`.
- **⛔ Do NOT use `--bare`:** live `--help` shows it sets `CLAUDE_CODE_SIMPLE=1` and forces auth to
  `ANTHROPIC_API_KEY`/`apiKeyHelper` only — *"OAuth and keychain are never read."* That would **break the subscription
  rail** (our whole point). Must be called out in ADR 0007 + the SOP so no future "optimization" adds it.
- **`--permission-mode`:** not needed once tools are disabled (nothing to permit); omit for v1.
- **`--json-schema` takes the schema as a single argv string** (`json.dumps(schema)`), not an `@file` path.

### 3.4 Model selection — `--model opus` (alias)

Locked: stick with Opus. Use the **tier alias** `opus`, not a hard-pinned `claude-opus-4-8`, so we stay agnostic of the
exact version (4.8 vs 5…) — this reconciles "stick with opus" with "keep it agnostic." *Alternative (one line):* pin
`--model claude-opus-4-8` if a future tier ever regresses synthesis quality. *(The §1 spike ran without `--model`,
defaulting to the harness's `claude-opus-4-8[1m]`; the shipped argv pins `--model opus`. Re-confirm token overhead on the first real build call.)*

### 3.5 Parsing + the determinism gate (belt **and** suspenders)

**Envelope contract** (from the 2026-08-28 spike; `--output-format json` emits one JSON object on stdout). Fields we rely
on: **`structured_output`** (an object — the `RecipeQuery`-shaped payload, **primary**), **`result`** (a string — a
JSON-encoded mirror of the same object, **fallback**), **`is_error`** (bool). Other fields (`total_cost_usd`, `num_turns`,
`stop_reason`, `model`, …) exist but are ignored. Representative happy-path envelope (usable verbatim as the fake-runner
return value in tests):

```json
{"is_error": false,
 "structured_output": {"include_ingredients": ["chicken"], "exclude_ingredients": null, "keywords": "high-protein chicken", "cuisine": null, "dish_type": null, "max_ready_minutes": 30},
 "result": "{\"include_ingredients\": [\"chicken\"]}",
 "total_cost_usd": 0.28, "num_turns": 2}
```

In `run_claude` (transport): nonzero `returncode` → `ClaudeCliError` (kind per §3.8); `json.loads(stdout)` raising
`JSONDecodeError` → `ClaudeCliError(kind="bad_output")`. `json.loads` returns `Any`, so narrow with `isinstance(env, dict)`
before returning (satisfies `mypy --strict`); a non-dict top level → `ClaudeCliError(kind="bad_output")`. Returns `dict[str, object]`.

In `synthesize_recipe_query` (contract + gate):
1. `envelope.get("is_error")` truthy → `RecipeSynthesisError`. A **missing** `is_error` is treated as falsy (not an error);
   step 2's payload checks then handle it. **Refusals surface here** — as `is_error: true` or a missing payload; `stop_reason`
   is intentionally **not** inspected (unlike the old SDK path). *(The synthesizer — not the runner — owns this: a nonzero
   **exit** is transport, the runner's job; `is_error` is a content-level failure on an otherwise-successful zero-exit envelope, a contract concern.)*
2. `payload = envelope.get("structured_output")` (an object); if `None` (whether the key is **absent** or JSON-`null`),
   **fallback** to `json.loads(envelope["result"])` — `result` is a JSON **string** mirror — guarded with
   `except (KeyError, json.JSONDecodeError, TypeError)` → `RecipeSynthesisError` (the `KeyError` covers `result` being absent too).
3. **`RecipeQuery.model_validate(payload)`** — the sacred boundary. Even though `--json-schema` (belt) constrained the
   shape, nothing reaches state until Pydantic (suspenders) re-validates. `ValidationError` → `RecipeSynthesisError`
   (wrapped `from exc`). Return the validated `RecipeQuery`.

Keep both: the schema flag is the model's *best-effort* constraint (CLI/model versions can drift); `model_validate` is
*our* guarantee and the only thing CLAUDE.md's non-negotiable rule trusts.

### 3.6 Disabling built-in tools

This is pure synthesis — no file reads, web, or bash. Disable all built-in tools so the model only reasons and emits
schema-shaped JSON (smaller attack surface, possibly fewer turns/tokens). **LOCKED: `--tools ""`** — verified against
Claude Code **2.1.197** (`--tools <tools...>`: *"Specify the list of available tools from the built-in set. Use `""` to
disable all tools, `"default"` to use all tools"*). No build-time verification needed.

### 3.7 Environment + timeout

- **Billing guard (belt-and-suspenders for the *dollar* constraint):** `_scrubbed_env()` = `os.environ.copy()` then
  `env.pop("ANTHROPIC_API_KEY", None)`. Do **not** strip the whole env — the CLI needs `HOME`/`PATH`/keychain paths to
  resolve the OAuth subscription credential. Scrubbing only the key makes accidental dollar-billing structurally impossible
  from this call site while preserving OAuth.
- **`cwd=_repo_root()`** — walk up from `Path(__file__).resolve()` (resolve first, so a relative `__file__` still walks correctly) to the dir containing `pyproject.toml`, so any project-scoped
  `.claude/` config/skill resolves reproducibly regardless of where `pantry suggest` is run. **If none is found** (e.g.
  installed as a wheel), fall back to `Path.cwd()` — never raise; cwd only affects optional (non-load-bearing) skill discovery.
- **`CLAUDE_TIMEOUT_S = 120`** module constant. `subprocess.TimeoutExpired` → `ClaudeCliError` (kind `timeout`).

### 3.8 Error taxonomy (lean, v1)

Two exception classes (a full subclass tree is a trivial later refinement — kept out of v1 for scope discipline). Both map
to a clean one-line message + `exit 1`, no traceback.

- **`ClaudeCliError(Exception)`** in `core/claude_cli.py` — transport/infra failures. Signature
  `__init__(self, message: str, *, kind: ClaudeErrorKind)`, with a module-level
  `ClaudeErrorKind = Literal["not_found", "auth", "quota", "timeout", "bad_output", "failed"]`; stores `self.kind` for
  testing + message selection.
- **`RecipeSynthesisError(Exception)`** stays in `synthesizer.py` — a **vanilla `Exception` subclass** (single message arg,
  no custom `__init__`) — for contract/validation failures. All three envelope-failure raise-sites (is_error / missing payload /
  refusal) use the **one** shared message *"Claude did not return a usable recipe query"*; only the Pydantic-`ValidationError`
  site uses the distinct *"Claude returned a query that failed validation"*.

| Failure | Detection | Raised | Message (exit 1) |
|---|---|---|---|
| `claude` binary absent | `FileNotFoundError` | `ClaudeCliError(kind="not_found")` | "claude CLI not found — install Claude Code" |
| Not logged in / auth | rc≠0 + `"authenticat"`/`"logged in"` in stderr | `kind="auth"` | "Claude is not authenticated (run `claude auth`)" |
| Timeout / hang | `subprocess.TimeoutExpired` | `kind="timeout"` | "Claude timed out after 120s" |
| stdout not JSON | `json.JSONDecodeError` | `kind="bad_output"` | "Claude returned unreadable output" |
| Quota / rate-limit | rc≠0 + `"rate limit"`/`"quota"`/`"overloaded"` | `kind="quota"` | "Claude quota or rate limit reached" |
| Other nonzero exit | fallback | `kind="failed"` | "Claude failed: {trimmed stderr}" |
| `is_error` / no `structured_output` / refusal | envelope inspection | `RecipeSynthesisError` | "Claude did not return a usable recipe query" |
| Pydantic `ValidationError` | `model_validate` raises | `RecipeSynthesisError` (wrapped) | "Claude returned a query that failed validation" |

(Stderr substring matching is heuristic — fine for a personal CLI; documented as such in the SOP.) *"Trimmed stderr"* =
whitespace-stripped and truncated to ~200 chars.

**`cli.py::suggest`:** drop `import anthropic`; catch `(RecipeSynthesisError, ClaudeCliError)` (one clause covers all).
Output/exit behavior unchanged.

### 3.9 SKILL.md — deferred, non-load-bearing in v1

The determinism-critical rule ("`include_ingredients` MUST be drawn only from the provided pantry") ships in the
**always-on `--append-system-prompt`** persona. A `.claude/skills/recipe-synthesis/SKILL.md` (front-matter + the richer
rubric from §4) is a *quality* layer only. **Open question (flagged): does a project SKILL.md auto-invoke under headless
`-p`?** Unverified — spike it during the build (compare output quality with/without the skill; inspect `--debug`). If it
doesn't auto-load, ship v1 on `--append-system-prompt` and defer skills. **The skill is never required for correctness.**

---

## 4. Eval criteria (write BEFORE code — §0A.3). Pantry: chicken, spinach, rice, garlic. Objective: **recipes that taste good** from these items (no macro goal).

**GOOD** — *why*:
1. `{"include_ingredients":["chicken","garlic"],"keywords":"garlic butter chicken"}` — real items, an appetising dish, concise keywords.
2. `{"include_ingredients":["chicken","rice","garlic"],"dish_type":"main course"}` — all pantry-real; optional fields honestly omitted.
3. `{"include_ingredients":["chicken","spinach"],"cuisine":"italian","max_ready_minutes":30}` — real cuisine, sensible time cap, tasty pairing.

**BAD** — *why*:
1. `{"include_ingredients":["chicken","quinoa","salmon"]}` — **hallucinated** items not in pantry (the cardinal sin).
2. `{"include_ingredients":[]}` — empty include with a non-empty pantry — did no work.
3. `{"include_ingredients":["chicken"],"cuisine":"atlantean"}` — invented cuisine facet.
4. `{"include_ingredients":["chicken"],"keywords":"<200-word paragraph>"}` — bloated keywords dilute search.
5. `{"include_ingredients":["chicken"],"max_ready_minutes":-15}` — schema-valid `int` but nonsense (motivates a future validator; out of v1 scope).

BAD-2 (a missing-field variant) is caught by Pydantic today; BAD-1/3/4/5 are **semantic** misses the schema doesn't catch —
they define the persona/skill target and the manual grading rubric for the build. *(This is precisely why schema-validity ≠ correctness — see the review note below.)*

---

## 5. Cleanup & dependency impact

- **Delete:** `core/llm.py`; `tests/test_llm.py`; `Settings.anthropic_api_key`; `import anthropic` in `cli.py` and `synthesizer.py`.
- **Macro-goal removal (v1 scope cut, 2026-08-29):** drop the `--goal` option from `cli.py::suggest`; drop the `goal: Category` param from `synthesize_recipe_query`; drop the "Macro goal" line from `_format_pantry`. Deferred to a later feature (GH #4). `Category` stays (ingredient categories).
- **Drop the `anthropic` dependency** from `pyproject.toml` + `uv lock` — after the rework no source imports it (double-check
  with `grep -rn anthropic src tests`). A real win: smaller install; the "subscription via CLI, not SDK" architecture becomes self-evident.
- **Docs (after the build, per the chosen sequence):** ADR 0007 supersedes 0006 (mark 0006 `Superseded by 0007`); rewrite SOP
  `workflows/01-query-synthesis.md` (new step 3/4 + the §3.8 taxonomy; remove the `ANTHROPIC_API_KEY` input). **Fix the known
  SOP drift:** `01-query-synthesis.md:39` (and ADR 0006:27) wrongly claim `get_client` raises `RuntimeError` on a missing key —
  it never did; the rewrite must not carry that error forward. Add a playbook session-log entry.

---

## 6. What approval of this doc authorizes (and what it does NOT)

Approving this **design** authorizes, in order:
1. Persist this doc to **`docs/design/cli-llm-boundary.md`** (standalone design doc). ✅ done.
2. **Goldfish-test it** (fresh session reads only this doc — can it implement from it alone?); fix gaps in the doc.
   ✅ done 2026-08-28 — an isolated agent implemented from this doc alone and surfaced 3 blocking gaps (missing persona
   string, unpinned tool flag, unspecified envelope shape) + typing/import nits, **all now folded into §2–§3.8 above.**
   ✅ **2nd pass 2026-08-29** — a fresh, unbiased agent re-tested and returned **no blocking gaps** (~90% implementable
   as-is); its 5 minor clarifications (missing-`is_error` precedence, refusal wiring, `RecipeSynthesisError` ctor,
   "trimmed stderr" definition, per-site messages) are now folded in too.
3. Then a **separate gate**: `superpowers:writing-plans` → an implementation plan → **TDD build**, where per CLAUDE.md §0 the
   author hand-writes the conceptual core (the synthesizer parse/validate logic, the eval rubric) and Claude writes the plumbing
   (`claude_cli.py` argv/subprocess, config/cli edits), type-every-line, just-in-time.

It does **NOT** authorize writing any implementation code from design-approval alone. **Elephant before code stays intact.**

---

## 7. Verification (how we'll prove it works, later)

- **Offline unit tests (verification-left):** inject a fake `ClaudeRunner` (a lambda returning an envelope dict) and
  monkeypatch `subprocess.run` for the runner's own tests — nothing shells out.
  - `tests/test_synthesizer.py` (rewritten): happy path; `result`-fallback extraction; `is_error`; no structured output;
    **schema-invalid payload → `RecipeSynthesisError`** (proves suspenders catch what the belt would've prevented); runner
    raises `ClaudeCliError` → propagates.
  - `tests/test_claude_cli.py` (new; replaces `test_llm.py`): argv assembly (asserts flags + prompt on stdin, not argv);
    env scrub (a stray `ANTHROPIC_API_KEY` is absent from `env=`); non-JSON stdout; missing binary; timeout; auth/quota/generic stderr → correct `.kind`.
  - Run: `uv run pytest`, `uv run mypy src`, `uv run ruff check`.
- **Real smoke (spends a sliver of quota, manual):** `pantry suggest --goal protein` on a seeded pantry → prints a
  validated `RecipeQuery` JSON, exit 0. Grade the output against §4. Failure modes: temporarily hide `claude` on `PATH`
  (→ "not found", exit 1).
- **Build-spike deliverables (read-only, no product code):** confirm the tool-disable flag (§3.6); measure token
  overhead/turns with `--debug` (does `--tools ""` cut the ~27K?); test SKILL.md headless auto-invoke (§3.9).

---

## 8. Critical files

- `src/pantry_pilot/core/claude_cli.py` — **NEW**: `ClaudeRunner` protocol, `run_claude`, `_scrubbed_env`, `_repo_root`, `CLAUDE_TIMEOUT_S`, `ClaudeCliError`. Replaces `core/llm.py`.
- `src/pantry_pilot/services/synthesizer.py` — swap `client`→`runner`; **drop the `goal` param**; add envelope parse + `model_validate` gate; **modify `_format_pantry`** (drop the "Macro goal" line); keep `RecipeSynthesisError`.
- `src/pantry_pilot/cli.py` — **drop the `--goal` option**; drop `import anthropic`; `except (RecipeSynthesisError, ClaudeCliError)`.
- `src/pantry_pilot/core/config.py` — remove `anthropic_api_key`.
- `tests/test_claude_cli.py` (NEW) + `tests/test_synthesizer.py` (rewrite fakes to `ClaudeRunner`); delete `tests/test_llm.py`.
- `pyproject.toml` — drop `anthropic` dep + re-lock.
- Deferred to after build: `docs/adr/0007-*.md`, `workflows/01-query-synthesis.md`, `docs/elephant-goldfish-playbook.md`.
