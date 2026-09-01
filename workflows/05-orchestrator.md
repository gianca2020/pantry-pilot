# Workflow 05 — Orchestrator ("plan tonight's cooking from your pantry")

**Stage:** Phase 4 &nbsp;·&nbsp; **Agent:** `pipeline/orchestrator.py` (the WAT "Agent") &nbsp;·&nbsp; **Chains:** synthesizer · trending · resolver · retrieval &nbsp;·&nbsp; **Command:** `pantry plan`

## Intent
Turn the pantry into an end-to-end plan: synthesize *what you need*, search the trendy live web for it,
rank the results by **what you can cook tonight**, and (on confirm) adjust the pantry — degrading to
unranked Spoonacular ideas when nothing trendy is rankable. **The orchestration is deterministic; the
LLM only reasons inside the tools it chains** (no new LLM boundary). See ADR 0011 + the design doc.

## Trigger
`pantry plan [-t/--theme] [-c/--cuisine] [-m/--meal] [--max-minutes N] [-v/--verbose]` — a thin front
door, or programmatically `make_plan(pantry, ...) -> PlanResult`. Flags override the pantry-derived
trendy search (the primary source only).

## Inputs
- The **pantry** — `list_ingredients(session)` → `list[Ingredient]` (session closed BEFORE the slow LLM calls).
- An authenticated Claude Code CLI (subscription; `ANTHROPIC_API_KEY` scrubbed → $0 marginal). **Per-step
  model tiers (ADR 0012):** synth + resolve on **Haiku** (tools OFF, 120 s), trending on **Sonnet** (web ON,
  180 s) — via the `claude_runner` / `claude_web_runner` factories; injected fakes override in tests.
- A Spoonacular API key (`PANTRY_SPOONACULAR_API_KEY`, via gitignored `.env`) — used ONLY on the degraded path.

## Steps (the DAG — design §4.2)
1. *(LLM, Stage 1 — INTENT)* `synthesize_recipe_query(pantry)` → a `RecipeQuery`. A content
   `RecipeSynthesisError` **aborts** (can't tell what you need); transport `ClaudeCliError` propagates.
2. *(deterministic — MAP)* `_to_trending_query(query, theme, cuisine, meal, max_minutes)` → `TrendingQuery`;
   explicit flags win (`max_minutes` on `is not None`; strings on `or`; `theme` falls back
   keywords → first include).
3. *(LLM+web, Stage 2 — TRENDING)* `find_trending(tq)` → allow-listed Recipes WITH ingredients + steps.
   Hand-timed so the trace records `seconds` even on degrade. A content `TrendingRecipeError` (or an empty
   result) **degrades**; a transport **timeout also degrades** (ADR 0012 D4 — a slow search isn't a hard
   failure); every other transport `ClaudeCliError` propagates.
4. *(deterministic — DEGRADE, only if nothing rankable)* `find_recipes(query)` (source #1 Spoonacular) →
   unranked **ideas**; returns a `PlanResult(source_used="spoonacular_fallback", degraded=True)` — **no
   cook prompt**. `SpoonacularError` (transport) propagates.
5. *(LLM+deterministic, Stage 4 — RANK)* `rank_recipes(recipes[:MAX_RANK], pantry)` → `list[RecipeFit]`
   ordered fewest-missing (the fan-out is hard-capped at `MAX_RANK=5`). Per-recipe `ResolutionError`
   already swallowed inside `rank_recipes`; transport propagates.
6. *(deterministic, on demand)* the CLI renders `_present_ranked(fits)` — the ranked table + each
   recipe's **link** (research it yourself) + ⚠ notes + shopping list — then `_ask_cook_choice`.
   Selecting a recipe = **"help me cook this dish"** (`_help_me_cook`): surface the link + numbered
   steps to cook from, then `cook(session, fits[i])` adjusts the pantry as a footer (present-and-confirm).

## Output
- `make_plan(...)` → `PlanResult(intent, source_used, fits, ideas, stages, degraded)`.
- Ranked (browse): table `Title | Missing | ⚠ | Can make?`; then per-recipe **🔗 link** + ⚠ notes +
  shopping list; then the cook prompt.
- On select ("help me cook"): `Let's cook {title}` + 🔗 link + numbered **Steps**; then a `Pantry updated:`
  footer (PRESENCE flip + QUANTITY nudge, ledger untouched).
- Degraded: table `Title | Ready | Source` (unranked ideas, no cook prompt).
- `-v` → the per-stage trace (`name · outcome · seconds · detail`).
- **Ingredient lines are cleaned before matching** (`resolver._clean_ingredient_lines`): scraped
  per-item prices (`($0.30)`) and section headers (`Sauce:`, `For serving:`) are stripped, so the
  match count, shopping list, and ⚠ notes stay honest (review-driven refinement, PR #17).

## Determinism boundary
`make_plan` only *sequences* gated tools — no cycle to bound (Principle 10 by construction), `MAX_RANK`
caps the one fan-out, content→degrade / transport→abort (except a trending **timeout** → degrade, ADR 0012
D4), **no auto-retry**. Every LLM output stays
Pydantic-validated inside its tool; state mutation is exclusively the existing `cook`.

## Edge cases / failure modes (design §6)
| Situation | Detection | Result |
|---|---|---|
| Trending empty **or** `TrendingRecipeError` (content) | `make_plan` Stage 2 | degrade → unranked Spoonacular ideas (`degraded=True`) |
| Trending `ClaudeCliError(kind="timeout")` (Stage 2) | `make_plan` Stage 2 | **degrade** → Spoonacular ideas (ADR 0012 D4 — a slow search isn't a hard failure) |
| `RecipeSynthesisError` (content, Stage 1) | `synthesize` | abort → CLI exit 1 |
| `ClaudeCliError` (transport — non-timeout, or a synth/rank timeout) | the transport | propagate → CLI exit 1 |
| `SpoonacularError` (transport, fallback) | `fetch_recipes` | propagate → CLI exit 1 |
| per-recipe `ResolutionError` mid-rank | `rank_recipes` | swallowed — the rest still rank |
| >5 trending recipes | `make_plan` | ranked set capped at `MAX_RANK=5` |
| empty pantry / both sources empty | `make_plan` | NOT an error — friendly note, no cook prompt |
| `pantry plan` run non-interactively / EOF | `_ask_cook_choice` | cook skipped (no hang); plan still printed |
| cook item archived/renamed since resolve | `get_ingredient` → None | skipped (existing `cook` behavior) |

## Tests
- `tests/test_orchestrator.py` — offline backbone: fake `synth_runner`/`rank_runner` (canned envelopes) +
  `trending_fetcher`/`spoon_fetcher` (canned recipes, allow-listed) + plain `Ingredient` objects. Covers
  the flow, the map + flag overrides, `_timed` inference, BOTH degrade branches (empty + `TrendingRecipeError`),
  transport propagation, synthesis abort, and the `MAX_RANK` cap (asserts names/outcomes, never timings).
- `tests/test_schemas.py` — `StageTrace`/`PlanResult` defaults + `source_used` Literal validation.
- `tests/test_cli.py` — monkeypatch `make_plan`/`init_db`; `pantry plan` renders the ranked table vs the
  degraded ideas table; `-v` prints the trace; cook via `CliRunner(input="1\n")`; error → exit 1. The
  `cook-ideas` tests stay green after the `_present_ranked` extraction (R1 regression-safe).
- `evals/plan_eval.py` — Tier-2 real-LLM harness (excluded from `pytest`): auto-grades the deterministic
  §5/§7 criteria + prints subjective for spot-check.

## Build spikes / live smoke
- **Build spike ✅ (read-only, R2 go/no-go, 2026-08-31):** graded a real `synthesize → _to_trending_query
  → find_trending` on the §5 pantry. Intent = *"garlic soy chicken stir fry rice bowl with spinach"*
  (Asian, main course, ≤40 min); result = a genuinely-trendy allow-listed recipe (Half Baked Harvest
  sesame-ginger chicken fried rice, 15 ingredients / 5 steps). **GO** — the pantry-derived theme is a
  strong trendy search, not a weak Spoonacular-flavored one. (Observation: a specific theme can narrow to
  few results — 1 here; non-blocking, 1 is rankable and 0 degrades.)
- **Live smoke ✅ (ranked path, loop closed, 2026-08-31):** seeded a throwaway pantry (temp
  `PANTRY_DB_PATH`, dev DB untouched); `pantry plan --verbose` → synth 11.6 s, trending 156 s (4 recipes),
  rank 117 s (4); `fits` sorted fewest-missing (7/9/10/10), all allow-listed, honest ⚠ ("green onions ≠
  bulb onion", "generic vs brown rice"); cooking #1 flipped `garlic`/`soy sauce` OK→LOW and reported the
  QUANTITY nudges (`onion`/`chicken`/`rice`) — **ledger untouched** (3 `initial` txns only, chicken still
  800 g). GOOD vs §5.
- **Degrade path — verified by tests, NOT the live smoke (honest note):** a gibberish `--theme` did NOT
  force a degrade — the agentic trending tool robustly returns trendy recipes even for a nonsense theme
  (a good robustness property; empty trending is genuinely rare). The degrade path stays covered by the
  offline unit tests (`test_make_plan_degrades_*`, `test_plan_degraded_*`) + the eval-harness `degrade`
  scenario.
