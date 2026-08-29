# ADR 0007 — Route the LLM boundary through the Claude Code CLI (subscription-billed)

**Status:** Accepted &nbsp;·&nbsp; **Supersedes:** [ADR 0006](0006-llm-boundary-and-structured-output.md)

## Context
ADR 0006 put the recipe-query synthesis boundary on the Anthropic **Messages API SDK**
(`client.messages.parse(..., output_format=RecipeQuery)`), billed via **API credits**. Two problems
surfaced: (1) a hard business constraint — *all* inference must bill the author's existing **Claude
subscription**, with **zero extra dollar spend** (API credits are off the table even at pennies); and
(2) the API-credit org sat at a $0 balance, so the SDK path could not actually run.

PantryPilot is a **personal-use** tool and a learning vehicle toward FDE / senior-AI-engineering work, so
routing the one LLM step through the locally-installed **Claude Code CLI** (which authenticates via the
subscription's OAuth) is acceptable and on-goal. Full design: `docs/design/cli-llm-boundary.md`.

## Decision
- **Full replacement, not dual-mode.** The Messages API path is removed entirely (`core/llm.py`, the
  `anthropic` dependency, and the `anthropic_api_key` setting are deleted). No config switch.
- **Boundary mechanism:** `synthesize_recipe_query` calls the CLI headless via an injected runner —
  `claude -p --output-format json --json-schema '<RecipeQuery schema>' --model opus
  --append-system-prompt '<persona>' --tools ""` — list-form argv (never `shell=True`), prompt on stdin.
- **Determinism = belt + suspenders.** `--json-schema` constrains the output shape natively **and**
  `RecipeQuery.model_validate()` re-validates in Python before anything downstream trusts it. Read the
  payload from the envelope's `structured_output` (fallback: the JSON string in `result`).
- **Model:** the `--model opus` tier **alias** (version-agnostic), not a pinned `claude-opus-4-8`.
- **Billing guard:** the subprocess env is `os.environ` minus `ANTHROPIC_API_KEY`, so a stray key can
  never silently flip inference to dollar-billed API usage.
- **⛔ Never `--bare`:** it forces `ANTHROPIC_API_KEY`/apiKeyHelper-only auth ("OAuth and keychain are
  never read"), which would break the subscription rail.
- **Testability by injection (preserved):** an injected `ClaudeRunner` (a `Protocol`) replaces the old
  Anthropic client; tests pass a fake runner and never shell out.
- **Error taxonomy:** transport/infra failures raise `ClaudeCliError` (with a `.kind` of
  `not_found | auth | quota | timeout | bad_output | failed`); contract/validation failures raise
  `RecipeSynthesisError`. The CLI catches both → one-line message + exit 1.
- **Scope cut:** the **macro `goal`** input is dropped for v1 — `suggest` synthesizes a query for *food
  that tastes good* from the pantry alone. Macro-goal targeting is deferred (GH #4). No MCP tool or hooks
  in v1 (deferred to the Phase-4 orchestrator).

## Consequences
- Inference bills subscription **quota, not dollars** — the hard constraint is met.
- Fewer dependencies: the `anthropic` SDK, `ANTHROPIC_API_KEY`, and `core/llm.py` are gone; the
  "subscription via CLI, not SDK" architecture is self-evident from the manifest.
- **New cost:** ~50–60× the token overhead per call vs the raw API (the CLI loads its full harness each
  invocation — ~27K tokens for a trivial call). Fine for low-volume personal use; a scaling concern.
- Behaviour now depends on an external contract we don't own (the CLI's JSON envelope shape); the
  dual-extraction fallback + offline runner tests hedge this, but a CLI upgrade should trigger a re-spike.
- ToS: acceptable for a solo personal tool; would **not** be for a shared/hosted product.

## Alternatives rejected / deferred
- **Keep the Messages API on subscription** — not possible; the subscription isn't exposed as an API key,
  and the API-credit org is $0 / off-budget.
- **Dual-mode (SDK *or* CLI via a switch)** — rejected; doubles the surface for a solo tool that needs one path.
- **The Claude Agent SDK** instead of the raw CLI — heavier dependency and the more policy-gray path; rejected for v1.
- **Hooks for schema validation** — unnecessary given native `--json-schema`; reserved for other guardrails.
- **MCP tool / agentic tools in v1** — deferred to Phase-4 orchestration where real agenticness pays off.
