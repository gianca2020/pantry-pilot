# ADR 0010 — Phase 3 recipe resolver: "what can I cook tonight?" (LLM boundary #3)

**Status:** Accepted &nbsp;·&nbsp; **Phase:** 3 &nbsp;·&nbsp; **Design:** `docs/design/recipe-resolver.md` + `workflows/04-recipe-resolver.md`

## Context
Phases 2a–2c produce recipes (source #1 Spoonacular; source #2 trending web, [ADR 0009](0009-trending-web-retrieval.md))
but nothing closes the loop against the **pantry**: which of these can I actually make tonight, and what am I
missing? Phase 3 is **LLM boundary #3** — semantic matching of free-text recipe ingredient lines to canonical
pantry rows — plus the **state-mutation** half ("cook it" adjusts the pantry). Full design + eval rubric live in
the design doc; this ADR records the decisions.

## Decision
- **D1 — Presence-level precision, not quantity math.** Have/missing is decided from the DB (`on_hand > 0` for
  QUANTITY, `status != OUT` for PRESENCE). **No cup→gram unit conversion** and no exact quantity deduction in v1.
- **D2 — Pantry-first ranking**, built on a **per-recipe resolver.** `rank_recipes(recipes, pantry)` resolves +
  assesses each recipe and orders by *what you can cook tonight* (fewest missing → fewest uncertain → title).
- **D3 — Single-shot tool, NOT agentic.** `resolve_recipe` reuses `run_claude` (tools **OFF** — no web needed),
  one call per recipe, schema-validated. The planning agent is the deferred Phase-4 orchestrator, not this step.
- **D4 — Cook = presence-flip + nudge (ledger-honest).** Matched PRESENCE items step down one notch
  (OK→LOW→OUT, deduped by `pantry_name`); matched QUANTITY items are **reported** in `to_update` for a manual
  `pantry use`, **never** auto-deducted with a fabricated amount. No `record_transaction` in v1 — the
  event-sourced ledger stays truthful.
- **D5 — Confidence, highlighted not blocked.** Each match carries `confident` + `note`; uncertain
  (`confident=false`) but stocked matches still count as **have**, surfaced with a ⚠ (and the note), never
  demoted or blocked.
- **D6 — Candidates come from source #2** (`find_trending`, which fills `ingredients`). Source-#1 (Spoonacular)
  recipes carry no ingredient lines yet → **skipped** from ranking.
- **D7 — Skip-on-content-error, propagate-on-transport-error.** A single recipe's `ResolutionError` (content) is
  swallowed so the rest still rank; a `ClaudeCliError` (transport — claude missing/timeout/auth) **propagates**
  and aborts the run (CLI exit 1). N serial single-shot calls, no batching (N≈3–5).
- **D8 — Per-recipe shopping list.** `"restock <name>"` for a stocked-but-OUT item you own; `"buy: <recipe line>"`
  for a not-stocked item. **Can make? = no missing.** Not deduped across recipes — each shows its own.

## Error taxonomy (mirrors ADR 0009's two-class split)
- **Transport** → reuse `ClaudeCliError(.kind)` (incl. `timeout`).
- **Content/validation** → new `ResolutionError(.kind ∈ {"llm_failed","bad_output"})` in `services/resolver.py`:
  `is_error` envelope → `llm_failed`; non-JSON / unschematic / bad payload → `bad_output`.
- **Empty pantry / all-missing / recipe-without-ingredients are NOT errors** → `[]` / skipped.

## The two gates (persona steers, code enforces)
- **Schema gate** — `_parse_resolution` reads `structured_output` (fallback: the `result` JSON-string), then
  `RecipeResolution.model_validate` — no un-validated LLM JSON reaches app state (CLAUDE.md rule). **No
  cardinality check:** a reply with fewer/more matches than lines is tolerated (unmatched lines aren't assessed).
- **Hallucination guard** — `assess` looks each `pantry_name` up in the real pantry (`by_name.get`); any name the
  model invented that isn't actually stocked drops to **missing**. The persona says "verbatim, never invent";
  `assess` enforces it — the same belt-and-suspenders pattern as source #2's allow-list `_filter`.

## Consequences
- A ranked "what can I cook tonight?" view with honest ⚠ uncertainty and a per-recipe shopping list, plus a
  ledger-honest `cook` — **fully offline-testable** (injected fake `ClaudeRunner` + saved fixture / plain
  `Ingredient` objects; nothing shells out).
- Schema-valid ≠ correct: a wrong-but-schema-valid match, an over-claimed `have`, or an uncertainty hidden behind
  `confident=true` is **not** code-caught — a **grading-only** concern (design §5), verified in the build spike.
- Serial N calls (tools OFF → fast/cheap, $0 marginal on the subscription); no batching/parallelism in v1.

## Build-time correction (recorded honestly)
The locked plan's reference `_shopping_list(fit)` was **internally inconsistent with its own test**: it emitted
`"restock <name>"` whenever `pantry_name` was truthy, but a **hallucinated** name (e.g. `"honey"` not in the
pantry) is truthy too, so it produced `"restock honey"` where `test_shopping_list_restock_vs_buy` expected
`"buy: honey glaze"`. Design §6 resolves the ambiguity — an unreal name is *"treated as null"*. **Fix (author
chose Option A):** `assess` normalizes a hallucinated `pantry_name` to `None` as it drops the match into
`missing`, so `_shopping_list` (unchanged signature) correctly says `"buy:"`. This keeps every design-pinned
signature intact; the trade-off is a one-line in-place normalization of the throwaway match object.

## Goldfish outcome
Design-of-record Goldfished **×3** in the design session (scores **72 → 88 → 72 → v2 close ≈ 95%**); all
surfaced seams (prompt template, persona string, the `_parse` gate contract, the hallucination guard, the cook
dedup + ledger-honesty, the CLI skip-on-EOF) folded into the doc **before** any code. Build was a clean
plan/execute split (this session), TDD, one commit per task.

## Alternatives rejected / deferred (design §3)
- **Unit/quantity math** (cup→gram) + exact deduction — deferred; presence-level is the v1 precision (D1).
- **Agentic substitution reasoning** ("no buttermilk → milk + vinegar") — Phase-4 / future; v1 is single-shot.
- **Ranking source-#1 (Spoonacular) recipes** — skipped; they carry no ingredient lines yet (D6).
- **Persisting fetched recipes** — not in v1; the ranked `fits` live in memory for the optional cook.
- **The full pantry→suggest→fetch→resolve→cook orchestration** — the Phase-4 `pipeline/orchestrator.py` (WAT
  "Agent"); `resolve_recipe`/`rank_recipes`/`cook` stay pure Tools behind a thin `cook-ideas` front door.
