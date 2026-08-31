# ADR 0011 — Phase 4 orchestrator: the WAT "Agent" (`pipeline/orchestrator.py`)

**Status:** Accepted &nbsp;·&nbsp; **Phase:** 4 &nbsp;·&nbsp; **Design:** `docs/design/orchestrator.md` + `workflows/05-orchestrator.md`

## Context
Phases 2a–3 built the Tools — `synthesize_recipe_query` (pantry → query), `find_recipes` (source #1
Spoonacular), `find_trending` (source #2 trendy web, WITH ingredient lines), `rank_recipes`/`cook`
(pantry-fit + state mutation) — but nothing **chained** them end-to-end. Phase 4 is that coordinator,
the WAT "Agent" (planning, routing, error handling), realized as a **deterministic pipeline (DAG)**,
not an LLM control loop. Full design + eval rubric live in the design doc; this ADR records the decisions.

**Where the agency lives:** not in the wiring, but in the *tools* the orchestrator drives —
`find_trending` is a genuine bounded web agent (~14 turns of WebSearch/WebFetch), and
`synthesize_recipe_query` + the per-recipe ingredient `match` are real LLM reasoning. Choosing a
deterministic coordinator decides *where* the agency is allowed to sit (inside gated tools), keeping
every project principle intact. The orchestrator adds **no new LLM boundary**.

## Decision
- **D1 — Agency = a deterministic pipeline (DAG).** `make_plan` sequences the tools; no loop → no cycle
  to bound → Principle 10 satisfied by construction. The one fan-out (rank) is hard-capped at `MAX_RANK`.
- **D2 — Trending-primary + Spoonacular fallback.** Trending has ingredient lines → **rankable** (primary).
  Spoonacular carries none → **unranked ideas** (fallback, no cook prompt). The fallback fires when
  trending returns `[]` **or** raises a content `TrendingRecipeError`. Source-#1 ingredient parsing deferred.
- **D3 — Pantry-derived intent, flags override.** One `synthesize_recipe_query(pantry)` → one
  `RecipeQuery`, mapped by a deterministic `_to_trending_query` to a `TrendingQuery`; explicit CLI flags
  win over the LLM's guess (`max_minutes` guards on `is not None` — 0 is a legit override; string fields
  use `or`). `include_/exclude_ingredients` are NOT mapped to trending's shape — they serve only the
  Spoonacular fallback's native `RecipeQuery` (one synthesis, two sources).
- **D4 — New `pantry plan`, present-and-confirm.** A thin front door that never auto-cooks; existing
  commands untouched; reuses `_ask_cook_choice`/`_uncertain`/`_shopping_list`/`cook`.
- **D5 — In-memory `PlanResult`.** The plan (intent, `source_used`, `fits`/`ideas`, `stages`, `degraded`)
  lives in memory; persistence deferred.
- **D6 — Structured `StageTrace` observability.** Per-stage `name · outcome · seconds · detail`, inferred
  by the `_timed` helper; `-v/--verbose` prints the trace post-hoc (no live progress callback in v1).
- **D7 — Content → degrade, transport → abort; `MAX_RANK` cap; no auto-retry.** `make_plan` catches ONLY
  the Stage-2 `TrendingRecipeError` (→ degrade). `RecipeSynthesisError` (content, Stage 1) and every
  transport error (`ClaudeCliError`/`SpoonacularError`, any stage) **propagate** → CLI exit 1. Empty
  pantry / both-sources-empty are NOT errors. No global timeout; per-stage bounds (120/180 s) suffice.
- **D8 — Rubric + Tier-2 eval harness.** A mandatory good/bad end-to-end rubric (design §5, incl. the
  degraded path) written before code, plus `evals/plan_eval.py` — real-LLM scenarios that auto-grade the
  deterministic criteria and print the subjective ones for spot-check. **No LLM-judge in v1.**

## Error taxonomy (the project's two-class split, applied to the chain)
| Failure | Handling |
|---|---|
| `RecipeSynthesisError` (content, Stage 1) | abort — foundational; propagate → CLI exit 1 |
| `TrendingRecipeError` (content) **or** empty trending | **degrade** → unranked Spoonacular ideas (`degraded=True`) |
| `ClaudeCliError` (transport, any stage) | propagate → CLI exit 1 |
| `SpoonacularError` (transport, fallback) | propagate → CLI exit 1 |
| per-recipe `ResolutionError` during rank | already swallowed inside `rank_recipes` |
| empty pantry / both sources empty | NOT an error — friendly note, plan still renders |

The only `try/except` inside `make_plan` is the Stage-2 `TrendingRecipeError` → degrade; everything else
surfaces to the CLI (mirrors `cook-ideas`).

## Determinism boundary (persona/tool steers, code enforces)
No new LLM boundary is added. Every LLM output stays Pydantic-validated **inside its tool** before it
reaches the orchestrator: `synthesize` validates `RecipeQuery`; `find_trending` validates `Recipe`s and
allow-list-`_filter`s them; `rank_recipes`/`assess` apply the hallucination guard. `make_plan` only
*sequences* these gated tools; state mutation is exclusively the existing `cook`.

## Consequences
- A single `pantry plan` turns the pantry into a ranked, cook-tonight plan off the trendy live web, with
  an honest Spoonacular fallback and a per-stage trace — **fully offline-testable** (injected fake
  runners/fetchers + plain `Ingredient` objects; nothing shells out).
- Principle 10 is now **verified in code**, not just designed: DAG (no loop), `MAX_RANK=5` cap,
  content→degrade / transport→abort, no auto-retry.
- Schema-valid ≠ good end-to-end: a weak-but-valid intent or an over-claimed match is a **grading-only**
  concern (design §5 #7/#8), verified by the build spike / live smoke / eval harness, not code.

## Build-time risks confronted (design §10)
- **R1 — `plan` vs `cook-ideas` overlap.** Resolved by extracting the shared table/⚠-notes/shopping-list/
  cook-prompt rendering into `cli._present_ranked`, called by BOTH commands (no copy-paste). Binding the
  cook return as `cook_result` sidesteps the `result.flipped` naming collision the Goldfish flagged.
  **Open question (not decided in v1):** should `plan` eventually supersede `cook-ideas` (reframed as the
  flags-only entry, or retired)? Flagged so the duplication is a conscious choice.
- **R2 — intent→trending mapping (the load-bearing, previously-unvalidated assumption).** Validated FIRST
  in a read-only build spike as a **go/no-go**: on the §5 pantry, `synthesize` produced a dish-shaped
  theme (*"garlic soy chicken stir fry rice bowl with spinach"*, Asian, main course, ≤40 min) and
  `find_trending` returned a genuinely-trendy allow-listed recipe — **GO**. The pantry-derived-intent
  value holds; it does NOT collapse toward flags-only.

## Goldfish outcome
Design-of-record Goldfished **×3** in the 2026-08-31 design session (two independent no-context passes
55% / 62% both stumbled on the same seams — no render-layer data contract + an under-specified `_timed`;
a confirming pass caught a `result.flipped` naming collision + missing `TrendingQuery` field types →
**final verification 96%, zero blocking gaps**). Every seam fixed **in the doc, not the goldfish**. Build
was a clean plan/execute split, TDD, one commit per task.

## Alternatives rejected / deferred (design §3)
- **An LLM control loop / tool-calling agent** — rejected for v1; agency stays in the tools (D1).
- **Source-#1 ingredient parsing** (LLM boundary #2) — deferred (roadmap #3); Spoonacular ideas stay unranked.
- **Agentic substitution reasoning** ("no buttermilk → milk + vinegar") — deferred.
- **Additional trendy platforms** (2nd source behind the seam / TikTok — GH #12) — deferred.
- **Persisting the plan; an LLM-as-judge eval tier; a global pipeline timeout / retries** — all deferred.
- **Re-applying CLI flags to the Spoonacular fallback query** — deferred; the degraded path is a
  best-effort safety net on the pantry-synthesized `RecipeQuery`, not a precise re-query (design §4.5).
