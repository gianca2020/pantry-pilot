# Workflow 01 — Recipe-Query Synthesis

**Stage:** Phase 2a &nbsp;·&nbsp; **Tool:** `services/synthesizer.py` &nbsp;·&nbsp; **Command:** `pantry suggest`

## Intent
Turn a snapshot of the pantry into a structured, schema-validated recipe-search query (`RecipeQuery`)
that Phase 2b can hand to Spoonacular. Synthesis runs through the **Claude Code CLI** on the author's
subscription (see ADR 0007) — no Anthropic SDK, no API key. *(Macro-goal targeting was dropped for v1 —
deferred to GH #4.)*

## Trigger
```
pantry suggest
```

## Inputs
- The **active pantry** (all non-archived ingredients), read via `list_ingredients(session)`.
- A **logged-in `claude` CLI on `PATH`** (Claude Code, authenticated via the subscription / OAuth).

## Steps
1. *(deterministic)* CLI reads the active pantry, then closes the DB session.
2. *(deterministic)* `_format_pantry()` renders the pantry into a plain-text prompt.
3. *(LLM — the only non-deterministic step)* `synthesize_recipe_query()` calls the injected runner
   (`run_claude`), which invokes `claude -p --output-format json --json-schema '<RecipeQuery schema>'
   --model opus --append-system-prompt '<persona>' --tools ""` — prompt on stdin, env scrubbed of
   `ANTHROPIC_API_KEY`, never `--bare`.
4. *(validation — belt + suspenders)* read the payload from the envelope's `structured_output`
   (fallback: `json.loads(result)`), then `RecipeQuery.model_validate()` re-checks it in Python.
5. *(deterministic)* CLI prints the validated query as JSON.

## Output
A `RecipeQuery`: `include_ingredients`, `exclude_ingredients`, `keywords`, `cuisine`, `dish_type`,
`max_ready_minutes`. These map onto Spoonacular `complexSearch` parameters (consumed in Phase 2b).

## Determinism boundary
Only step 3 is non-deterministic, and its output passes **two** guards before anything trusts it:
`--json-schema` (the model's native constraint) and `RecipeQuery.model_validate()` (our guarantee — the
only thing the determinism rule actually relies on). "Highly-rated" is **not** the model's job — it is a
fixed `sort=popularity` applied deterministically in the Phase-2b retrieval call.

## Edge cases / failure modes
Transport/infra failures raise `ClaudeCliError` (carrying a `.kind`); content/validation failures raise
`RecipeSynthesisError`. The CLI catches both → one-line message, exit 1 (never a traceback).

| Situation | Raised (`.kind`) | Behaviour |
|---|---|---|
| `claude` not installed | `ClaudeCliError` (`not_found`) | "claude CLI not found" → exit 1 |
| Not logged in / auth error | `ClaudeCliError` (`auth`) | "Claude is not authenticated" → exit 1 |
| Timeout (>120s) | `ClaudeCliError` (`timeout`) | "Claude timed out" → exit 1 |
| Quota / rate limit | `ClaudeCliError` (`quota`) | "Claude quota or rate limit reached" → exit 1 |
| Non-JSON / no structured output | `ClaudeCliError` (`bad_output`) / `RecipeSynthesisError` | exit 1 |
| Refusal / schema-invalid output | `RecipeSynthesisError` | "did not return a usable / valid query" → exit 1 |
| Empty pantry | — | Still yields a query (`include_ingredients` may be empty); Phase 2b decides handling |

*(Auth/quota detection is heuristic stderr substring-matching — fine for a personal CLI.)*

## Tests
- `tests/test_synthesizer.py` — offline, injected fake `ClaudeRunner` (happy path, `result` fallback,
  `is_error`, no payload, schema-invalid, transport-error propagation, prompt contents).
- `tests/test_claude_cli.py` — offline, monkeypatched `subprocess.run` (argv assembly, env scrub, each
  error `.kind`).
- `tests/test_cli.py` — offline smoke test of the `suggest` command.
