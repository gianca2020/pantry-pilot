# ADR 0012 — Per-step model tiering + `pantry plan` timeout resilience

**Status:** Proposed &nbsp;·&nbsp; **Phase:** 4 (post-orchestrator) &nbsp;·&nbsp; **Issue:** [#18](https://github.com/gianca2020/pantry-pilot/issues/18) &nbsp;·&nbsp; **Branch:** `dev-feature-10-model-tiering`

## Context
Live-testing the Phase-4 orchestrator (PR #17) surfaced two things:

1. **No model-tier discipline (Elephant/Goldfish Principle 5 — our biggest open gap).** All three LLM steps
   hardcode `--model opus` (`core/claude_cli.py:run_claude`, `core/claude_web.py:run_claude_web`). Opus is
   overkill for the two constrained/structured steps.
2. **`pantry plan` times out.** The trending web search regularly exceeds its 180s budget (twice in live
   runs, even with a focused theme) → `ClaudeCliError(kind="timeout")` → hard abort (`exit 1`).

**Framing (load-bearing, D3 below):** PantryPilot bills inference through the **`claude -p` CLI on the Max
subscription** (`ANTHROPIC_API_KEY` scrubbed → $0 marginal, flat rate). So model tiering is **NOT a cost
optimization** — it buys **latency** (Haiku/Sonnet are much faster than Opus) and usage-limit headroom. And
latency is exactly what the timeout needs, so #1 and #2 are the same lever.

## Decision

- **D1 — Model is a per-step choice, injected via a runner *factory*; the `ClaudeRunner` seam is unchanged.**
  Add `claude_runner(model="opus") -> ClaudeRunner` (`claude_cli.py`, tools OFF, 120s) and
  `claude_web_runner(model="opus") -> ClaudeRunner` (`claude_web.py`, web ON, 180s). Each returns a closure
  that bakes `--model <model>` into the argv and reuses the shared `_invoke_claude`. The `ClaudeRunner`
  Protocol stays `__call__(prompt, schema, *, system) -> dict` — so **every injected test fake is unchanged**
  (the model rides inside the default runner, not the call signature). Keep `run_claude = claude_runner()`
  and `run_claude_web = claude_web_runner()` as the opus back-compat instances (existing imports keep working).

- **D2 — Tier policy, single source of truth.** A small `core/models.py` holds the assignments (version-agnostic
  tier aliases, per the Phase-2a decision):
  - `SYNTH_MODEL = "haiku"` — pantry → RecipeQuery: constrained, schema-validated.
  - `RESOLVE_MODEL = "haiku"` — per-recipe ingredient match: simple matching, and the **slow N-serial-call**
    stage → biggest latency win.
  - `TRENDING_MODEL = "sonnet"` — the agentic web search + verbatim extraction: the one genuinely hard step
    (judgment + fidelity) *and* the timeout culprit → Sonnet keeps near-Opus quality while being faster.
  Each tool's default runner reads its constant (`synthesizer` → `claude_runner(SYNTH_MODEL)`, `resolver` →
  `claude_runner(RESOLVE_MODEL)`, `trending` → `claude_web_runner(TRENDING_MODEL)`). Escalate a step back to
  `opus` only if the eval A/B (D5) shows a regression.

- **D3 — This is latency/reliability, not cost.** Documented explicitly (Context above) so nobody later
  "optimizes cost" and mis-frames the change — on the flat-rate subscription there is no per-token bill.

- **D4 — Degrade-on-timeout (a scoped amendment to design §4.6 / D7).** `make_plan` Stage 2 treats a trending
  `ClaudeCliError(kind == "timeout")` as **degrade → Spoonacular ideas** (same as an empty/content-fail),
  instead of aborting. Every *other* transport kind (`auth`/`quota`/`not_found`/`failed`/`bad_output`) still
  **propagates** → CLI exit 1. So `pantry plan` never dies with `timed out after 180s` — it returns unranked
  ideas as the safety net. StageTrace: `outcome="degraded"`, `detail="timeout -> fallback"`. The web timeout
  stays **180s** for now (Sonnet should fit under it); revisit to 240s only if the A/B still bumps it — a
  data-driven call, not a guess.

- **D5 — Evals pick the final tiers (🎯 not vibes).** Extend `evals/plan_eval.py` with a **model-config
  dimension**: run each scenario under `all-opus` and the `tiered` config, print the deterministic scores
  (fits sorted / recipes allow-listed / no hallucinated `pantry_name` / shopping-list well-formed) **plus
  wall-clock per stage**, and compare. If a tiered step regresses on the deterministic criteria, escalate that
  one step. No new eval tier; reuse the existing `grade()`.

- **D6 — Determinism boundary unchanged.** The model swap changes *who* reasons, not the gates: every LLM
  output is still Pydantic-validated at its boundary (`_parse_*`), the hallucination guard (`assess`) and the
  allow-list `_filter` still enforce. A weaker model's output is still gated. A materially-worse-but-schema-valid
  result is a **grading concern** caught by D5, not by code.

## Error taxonomy (the change to the two-class split)
| Failure | Before (PR #17) | After (this ADR) |
|---|---|---|
| trending `TrendingRecipeError` (content) / empty | degrade | degrade *(unchanged)* |
| trending `ClaudeCliError(kind="timeout")` | **abort → exit 1** | **degrade → Spoonacular ideas** |
| trending `ClaudeCliError` (auth/quota/not_found/failed/bad_output) | abort → exit 1 | abort → exit 1 *(unchanged)* |
| `RecipeSynthesisError` / `SpoonacularError` / other transport | propagate | propagate *(unchanged)* |

## Consequences
- `pantry plan` runs **faster** (Haiku on the two structured steps; Sonnet on the web step) and **never hard-
  fails on a slow trending search** — the common failure mode from live testing is gone.
- **Model-tier discipline is now real** (Principle 5 flips ➖→✅ in the playbook), and it's **measured, not
  assumed** (D5).
- The runner-factory keeps the offline test seam intact — no churn to the injected fakes across
  test_synthesizer / test_resolver / test_trending / test_orchestrator / test_cli.
- Tiering is transport-level infra (shared by Phases 2a/2c/3), correctly **not** bundled into the orchestrator
  PR — its own focused branch/PR (#18).

## Build plan (TDD, commit per unit)
1. **Transport factories** — `claude_runner` / `claude_web_runner` + keep `run_claude`/`run_claude_web`. Test:
   monkeypatch `_invoke_claude`, assert the factory bakes `--model <model>` into argv.
2. **Tier policy + wiring** — `core/models.py` constants; tool defaults read them. Test: monkeypatch
   `_invoke_claude`, call each tool with no injected runner, assert the right `--model` is requested.
3. **Degrade-on-timeout** — `make_plan` Stage 2. Test: inject a trending fetcher that raises
   `ClaudeCliError(kind="timeout")` → degrade; `kind="auth"` → still propagates. (extends test_orchestrator.py)
4. **Eval A/B** — `plan_eval.py` model-config dimension (real-LLM, not in pytest).
5. **Docs** — this ADR + a note in `workflows/05-orchestrator.md` (degrade-on-timeout row) + playbook
   (Principle 5 ➖→✅, session-log entry, model-tier line).

## Proportionate process
Smaller than a phase → **ADR + TDD**, not a full Elephant + Goldfish×3 (the "scale process to the task" lesson
from Phase 2b). The eval A/B is the empirical gate that replaces a Goldfish here.

## Alternatives considered / deferred
- **Add `model` to the `ClaudeRunner` Protocol / tool signatures** — rejected: churns every injected test fake
  for no gain; the factory achieves per-step tiering without touching the seam.
- **Env-overridable tiers (pydantic Settings)** — deferred: the eval A/B injects model-bound runners directly,
  so env config isn't needed for v1; revisit if runtime tuning is wanted.
- **Bump the web timeout to 240s instead of / in addition to degrading** — deferred to a data-driven call
  after the A/B (D4); Sonnet is expected to fit 180s.
- **Global downgrade to one cheaper model** — rejected: the agentic web step needs the capability; tiering
  per-step is the whole point.
- **Haiku on `find_trending`** — rejected for the default (extraction-fidelity / trend-judgment risk); the A/B
  can still test it.
