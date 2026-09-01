# Elephant 🐘 · Goldfish 🐠 — AI Engineering Playbook

> A living checklist for how **Gian + Claude** build PantryPilot with AI, adapted from
> Dave Rensin's *"Elephants, Goldfish and the New Golden Age of Software Engineering"*
> (Google Research, 2026) and the companion *"Eleven Principles for Token-Efficient
> Software Engineering"* (Google Cloud).
>
> **How to use this doc:** skim §1–§2 at the start of a work session; check the scorecard
> in §3 when planning a feature; log the session in §5; keep §6 (roadmap) honest.
> This doc is the reminder — if a habit here isn't happening, that's the signal to fix it.

Last updated: **2026-08-31**

---

## 1. The two-part method (the core idea)

As AI generates more code than a human can carefully read, the **written design document
becomes more important than the code**. So we separate *thinking* from *doing*:

| | What it is | Its job |
|---|---|---|
| 🐘 **Elephant** | A long session where human + AI discuss the problem and co-write a **detailed design doc** *before any code*. Holds all context, assumptions, and decisions. | Get the design + the grading criteria right. |
| 🐠 **Goldfish** | A **brand-new session with no memory** that is asked to read *only* the design doc. | Prove the doc is self-sufficient. If the goldfish can't implement from the doc alone, the **doc** is incomplete — not the goldfish. |

**The loop for every feature:** Feed the Elephant → test it on a Goldfish → refine the doc
→ only then write code → verify against criteria you agreed *before* coding.

**Anti-patterns to avoid:**
- One giant prompt that asks the AI to "do everything."
- Generating code before a design doc exists.
- Treating the AI as autonomous instead of an accelerant that needs human governance.
- Accepting high-volume, low-quality output without structured review.
- Piling corrective prompts onto an already-broken state (instead: undo/revert).

---

## 2. The principles, with our current status

Legend: ✅ doing it well · ➖ partial · ❌ gap to adopt

### From the Elephant/Goldfish essay
| # | Principle | Status | How it shows up (or should) in PantryPilot |
|---|-----------|:---:|---|
| 1 | **Iterative dialogue, not one-shot prompts** | ✅ | CLAUDE.md §3 four-pillar planning protocol; Plan→Validate→Implement→Test cadence. |
| 2 | **Design-first: docs above code** | ✅ | 6 ADRs + `workflows/` SOPs written before/around code. |
| 3 | **Developer as manager** (delegate, set boundaries) | ➖ | §0 build cadence splits "Claude writes boilerplate / human writes core logic." Working, but delegation to *sub-agents* not used yet. |
| 4 | **Elephant: long design session → design doc** | ➖ | Artifacts exist (ADR 0006, SOP 01), but not always produced in a dedicated design-only session. |
| 5 | **Goldfish: fresh session validates the doc** | ❌ | **Biggest gap.** We've never taken a SOP/ADR to a blank session to test if it's implementable from the doc alone. |
| 6 | **Co-design evaluation criteria before code** | ➖ | Pydantic schema validation + TDD are a partial proxy, but we don't write explicit "what makes a *good* RecipeQuery" grading rules first. |

### From the Eleven Principles (token-efficient companion)
| # | Principle | Status | Notes for us |
|---|-----------|:---:|---|
| 1 | Start with a balanced model, scale up on failure | ✅ | **Per-step model tiering shipped (ADR 0012, 2026-09-01):** Haiku for synth + resolve, Sonnet for the agentic web search; escalate a step only if the eval A/B regresses. Latency, not cost (flat-rate subscription). |
| 2 | Use skills/reusable workflows from the start | ➖ | CLAUDE.md governance ✅; packaged, reusable skills ❌. |
| 3 | Automate with scripts; read-only research *before* writing | ✅ | This session: checked installs & read official docs before acting. |
| 4 | Delegate output-heavy tasks to sub-agents; reconcile results only | ✅ | **First used 2026-09-01:** the ADR-0012 build ran subagent-driven — a Sonnet implementer per unit, Opus orchestrates + reviews each diff, the author reviews the PR. |
| 5 | Divide & conquer: plan in Elephant, execute in clean Goldfish, checkpoint via commits | ➖ | Small focused commits ✅; explicit plan-session/execute-session split ❌. |
| 6 | **Shift verification left** (unit/functional early, UI/smoke last) | ✅ | Tests for every module (`test_*.py`), TDD-first. Strong. |
| 7 | Undo when adrift (revert, don't stack fixes) | ❓ | Not a formalized habit. |
| 8 | Be specific with context (exact files, inline `// FIX THIS`) | ✅ | This session pointed to `llm.py:11-17`, exact commands. |
| 9 | Iterate on *rules* (CLAUDE.md/AGENTS.md), not repeated prompts | ✅ | CLAUDE.md + the memory store are exactly this. |
| 10 | Avoid uncontrolled loops; event-driven, hard stop conditions | ✅ | **Verified in code (Phase-4 orchestrator build, 2026-08-31):** deterministic DAG (no loop to bound), `MAX_RANK=5` fan-out cap, content→degrade / transport→abort, no auto-retry — `pipeline/orchestrator.py`, tested in `tests/test_orchestrator.py`. See ADR 0011 + `docs/design/orchestrator.md` §4.6. |
| 11 | New session per new topic (reduce context bloat) | ➖ | Ad hoc. Pairs naturally with the Goldfish habit. |

---

## 3. Scorecard summary

**Already strong (keep doing):** design-first docs, verification-left/TDD, iterate-on-rules,
specific-context, read-only research before acting.

**Gaps we're deliberately adopting (in priority order):**
1. 🐠 **Run the Goldfish test** — after writing a SOP/ADR, open a fresh session, hand it *only* that doc, and ask "could you implement this?" Fix the doc where it stumbles. *(Principle 5 / essay.)* **✅ First done 2026-08-29 — the CLI-boundary design doc, two passes (see session log).**
2. 🎯 **Co-design eval criteria first** — before coding an LLM step, write down what a good/bad output looks like. *(Principle 6 / essay.)*
3. ✂️ **Split plan-session from execute-session** — design in one thread, implement in a clean one, checkpoint each with a commit. *(Principle 5 / eleven.)*
4. ~~🧮 **Model-tier discipline**~~ ✅ — per-step tiers shipped (ADR 0012, 2026-09-01): Haiku synth/resolve, Sonnet trending; eval A/B decides escalation. Delegation (Principle 4) also first used, subagent-driven. *(Principle 1 / eleven.)*
5. ↩️ **Undo when adrift** — revert instead of stacking corrective prompts. *(Principle 7 / eleven.)*

---

## 4. Our standing definition of done for an LLM feature
Adopt this checklist per Phase 2–4 LLM step:
- [ ] Elephant: design doc / SOP written and discussed.
- [ ] Eval criteria: examples of good vs bad output written *before* code.
- [ ] Goldfish: fresh session can restate/implement the plan from the doc alone.
- [ ] Deterministic boundary: LLM output validated against a Pydantic schema.
- [ ] Tests: unit + functional pass (verification-left).
- [ ] ADR/SOP updated with any new edge case (CLAUDE.md §5).

---

## 5. Session log

### 2026-08-28 — `ant` CLI / subscription-auth plumbing
- **Goal:** enable subscription (OAuth) auth for the LLM boundary so `suggest` works without an API key.
- **What we did:**
  - Read-only research *first*: checked whether `brew`/`ant` were installed; pulled **official** `ant` docs instead of trusting a pasted snippet (Principle 3 & 8). Corrected the `xattr` quarantine step to "conditional, only on Gatekeeper error."
  - Installed `ant` CLI v1.28.0 via Homebrew; verified with `ant --version`.
  - `ant auth login` → OAuth profile `default` (org "Team's Individual Org"); verified inference access with `ant models list`.
  - Honestly flagged the **one unverified link**: the Python `anthropic` SDK actually reading the `ant` OAuth profile (`llm.py:11-17` assumes it). Not yet tested end-to-end.
  - Created this playbook.
- **Principles practiced:** design-first research (3), specific context (8), verification (6), intellectual honesty about what's *not* verified.
- **Loop closed (finding):** ran `suggest` with `ANTHROPIC_API_KEY` unset. ✅ SDK **did** resolve the `ant` OAuth creds (valid `request_id`, org-level error — *not* an auth error); `cli.py` printed a clean one-line error + exit 1, no traceback, as designed. ❌ Inference rejected: *"credit balance is too low."* Isolation test — the **`ant` CLI itself** hits the same error → **not a code/SDK problem**: this org bills inference via **API credits** and has **$0** balance. **Blocked on account billing (user decision), not code.** `synthesizer.py` still pins `model="claude-opus-4-8"`; org offers `claude-opus-5` — revisit when billing is sorted.

### 2026-08-29 — CLI LLM-boundary rework (Elephant → Goldfish ×2 → TDD build)
- **Goal:** move recipe-query synthesis off the Anthropic SDK onto `claude -p` (subscription) — see
  `docs/design/cli-llm-boundary.md` + ADR 0007.
- **Elephant:** wrote a full Shape-Y v1 design doc in a dedicated design session *before* any code.
- **🐠 Goldfish (finally did it!):** two fresh, no-context agents implemented from the doc alone. Pass 1
  surfaced 3 blocking gaps (missing persona string, unpinned tool flag, unspecified envelope shape) → folded
  fixes into the doc. Pass 2 came back clean (~90% implementable, no blocking gaps). **Closes our #1 gap.**
- **🎯 Eval criteria first:** good/bad `RecipeQuery` examples written before code (design doc §4).
- **✂️ Plan/execute split:** designed in one thread, built in a clean one via `writing-plans` →
  `executing-plans`; a commit per task.
- **Build:** TDD, 5 tasks. The author hand-wrote the conceptual core (the determinism gate) with PR-review
  coaching; Claude did the plumbing. TDD caught two real bugs (an auth-heuristic miss; a `mypy --strict`
  typing gap in the fallback). Full suite green (48 tests, mypy, ruff); `anthropic` dependency dropped.
- **🧮 Model discipline:** settled on the `--model opus` tier alias (version-agnostic) with a documented
  escalation note (pin an exact version only if a tier regresses) — partly closes the model-tier gap.
- **Branching:** its own feature branch `dev-feature-3-cli-llm-boundary` off `main` (feature-2 merged via PR #3).
- **New teaching habit:** a gradual-release ladder for hard logic — TODO comments → pseudocode → code + explanation.

### 2026-08-30 — Phase 2b: recipe retrieval (deterministic Spoonacular tool)
- **Goal:** turn a validated `RecipeQuery` into real, highly-rated candidate recipes — see ADR 0008 +
  `workflows/02-recipe-retrieval.md`.
- **🐘 Elephant, proportionate:** design-first in a plan session (plan file → ADR 0008). Deterministic
  tool, so **correctly NO Goldfish test / NO eval criteria** (those are LLM-step gates) — a good
  calibration that process should *scale to the task*.
- **Boundary reuse:** mirrored the Phase-2a seam — transport `core/spoonacular.py` (`RecipeFetcher`
  Protocol + `fetch_recipes`, httpx) vs logic `services/retrieval.py` (`_query_to_params`,
  `_parse_recipes`, `find_recipes`); a typed `SpoonacularError(.kind)` mirroring `ClaudeCliError`.
  Errors map on **HTTP status** — cleaner than the CLI seam's stderr heuristic.
- **✅ Verification-left:** TDD red→green, **fully offline** (injected fake fetcher + saved fixture JSON);
  21 new tests (48→69), `mypy`/`ruff` clean. Rich `Recipe` uses `validation_alias` for the camelCase
  API keys (`readyInMinutes`, `sourceUrl`).
- **🔌 Live smoke ✅ (loop closed):** real key via gitignored `.env`; `find_recipes` returned **5**
  popularity-sorted candidates that validated against `Recipe` — `sort=popularity` accepted, fixture
  shape matches reality. (Aside: `includeIngredients` is a ranking signal, not a strict AND filter.)
- **🧹 Hygiene (intellectual honesty):** the canonical `uv run mypy` (config `files=["src","tests"]`) was
  actually **red** on latent f3 test-file issues (f3 had verified with `mypy src` only) — fixed
  mechanically in a *separate* commit so bare mypy is green across 28 files.
- **✂️ Scope discipline:** Spoonacular is the deliberate single v1 source; richer/agentic retrieval
  deferred to **GH #10**. No CLI in v1 (chaining is the Phase-4 orchestrator's job); live smoke via a
  fixed `RecipeQuery` snippet.
- **Branching:** `dev-feature-4-recipe-retrieval` off `main`; small commits (deps → transport → feature → hygiene).
- **Learning note:** author set out to hand-write the core; on *resume* Claude implemented it at the
  ladder's "code + explanation" rung for PR-style review — learning-first honored, momentum kept.

### 2026-08-30 — Phase 2c: source #2 "what's hot right now" (agentic web retrieval)
- **Goal:** a second recipe source that finds currently-trending dishes off the live web and validates them
  into `Recipe`s with ingredients + steps — see ADR 0009 + `workflows/03-trending-retrieval.md` +
  `docs/design/trending-recipe-source.md`.
- **🐘 Elephant + 🐠 Goldfish ×3:** design-of-record written in a dedicated design session and Goldfished
  three times (45%→75%→80%, then reused transport plumbing embedded + `id` handling pinned) before any code.
- **🎯 Eval criteria first:** good/bad output rubric written in the design (§5) before coding the LLM step.
- **✂️ Plan/execute split:** fresh build session; `writing-plans` → TDD, a commit per unit.
- **🔬 Build spike (read-only) caught a design bug:** the design's `--tools "WebSearch WebFetch"` did NOT
  enable web in headless `-p` — the CLI reads the space-joined arg as one bogus tool name, and even the
  comma-list is auto-DENIED without `--allowedTools`. Confirmed combo: `--tools "WebSearch,WebFetch"` +
  `--allowedTools "WebSearch WebFetch"`; ~120 s / ~14 turns / **$0 real** on the subscription. Fixed the
  design §4.2. A textbook case of *spike-before-you-trust-the-doc*.
- **Boundary reuse:** `run_claude_web` shares `claude_cli._invoke_claude` byte-identically (DRY); the
  allow-list (`core/recipe_sources.py`) is the deterministic `_filter` gate (persona steers, filter enforces).
- **✅ Verification-left:** TDD red→green, **fully offline** (injected fake `ClaudeRunner` + saved fixture);
  32 new tests (69→101), `mypy --strict` + `ruff` clean. TDD caught a real bug — the `_filter` test built
  `Recipe(source_url=…)` by field-name, which the `sourceUrl` `validation_alias` silently drops; fixed to
  build via the alias (mirrors the real `model_validate` flow).
- **Learning note:** author started the core via the TODO→pseudocode→code ladder; on a time crunch asked
  Claude to finish it at the "code + explanation" rung for PR-style review — learning-first honored, momentum kept.
- **🔌 Live smoke ✅ (loop closed, 2026-08-30):** real `find_trending(TrendingQuery(theme="chicken dinner"))`
  → 3 recipes, ALL allow-listed (pinchofyum ×2, halfbakedharvest), each with real ingredients + numbered
  steps; ~108 s / $0. Spot-checked one URL via WebFetch — page exists, title matches, first step
  **byte-identical** (not paraphrased) → closes §5 BAD #1/#5 (fabrication), which code can't catch. GOOD.
- **Branching:** `dev-feature-6-trending-source` off `main` (design merged via PR #13; Phase 2b via #11); **PR #14**.

### 2026-08-30 — Phase 3: recipe resolver "what can I cook tonight?" (LLM boundary #3)
- **Goal:** rank fetched recipes by what the pantry can cook tonight — an LLM matches each recipe ingredient
  line to a pantry item, then deterministic code decides in-stock, ranks by fewest-missing, and `cook` adjusts
  the pantry — see ADR 0010 + `workflows/04-recipe-resolver.md` + `docs/design/recipe-resolver.md`.
- **🐘 Elephant + 🐠 Goldfish ×3:** design-of-record written in a dedicated design session and Goldfished three
  times (**72 → 88 → 72 → v2 ≈ 95%**); every surfaced seam (prompt template, persona, the `_parse` gate
  contract, the hallucination guard, cook dedup + ledger-honesty, CLI skip-on-EOF) folded into the doc first.
- **🎯 Eval criteria first:** good/bad match rubric written in the design (§5) before coding the LLM step.
- **✂️ Plan/execute split (now a firm habit):** designed in one thread; this was a clean **execute** session —
  `executing-plans`, TDD, a commit per task (7 tasks: schemas → resolver boundary → assess → rank → cook → CLI →
  docs). Baseline confirmed green before Task 1.
- **✅ Verification-left:** TDD red→green, **fully offline** (injected fake `ClaudeRunner` + saved fixture /
  plain `Ingredient` objects; the `session` fixture only for `cook`). 24 new tests (105→129), `mypy --strict` +
  `ruff` clean throughout.
- **🔍 TDD caught a real spec bug:** the locked plan's reference `_shopping_list` was inconsistent with its own
  test — it said `"restock honey"` for a *hallucinated* name where the test wanted `"buy: honey glaze"`. Design
  §6 ("treated as null") resolved it; author chose **Option A** — `assess` normalizes an unreal `pantry_name` to
  `None` so `_shopping_list` (unchanged signature) says `"buy:"`. Surfaced + decided PR-style, not waved through
  (CLAUDE.md §0A). Recorded in ADR 0010.
- **Learning note:** author took the **fallback** early (crunched) — Claude built the core (Tasks 2–5) at the
  "code + explanation" rung for PR-style review; learning-first honored via review, momentum kept.
- **Determinism reuse:** mirrors source #2's two-gate shape — schema gate (`_parse_resolution`) + a
  hallucination guard in `assess` (persona steers to real names; `assess` enforces). One non-deterministic step
  (`resolve_recipe`, tools OFF); stock/rank/mutation all deterministic + Pydantic-validated at the boundary.
- **Branching:** `dev-feature-8-recipe-resolver` off `main` (Phase 2c merged via PR #15).
- **🔬 Build spike ✅ (read-only, 2026-08-30):** graded a real `resolve_recipe` on a real recipetineats
  honey-garlic-chicken vs §5 — 3 correct verbatim matches (chicken/garlic/soy sauce), all 5 genuinely-absent
  lines correctly `null` → "buy:", **zero** hallucinated names (the guard never had to fire), honest confidence.
  GOOD.
- **🔌 Live smoke ✅ (loop closed, 2026-08-30):** seeded a throwaway pantry (temp `PANTRY_DB_PATH`, real dev DB
  untouched); `pantry cook-ideas --theme "chicken dinner"` → 4 recipes ranked fewest-missing (10/11/12/16), each
  with ⚠ notes (e.g. "garlic powder ≈ fresh garlic?") + a shopping list; cooking #1 flipped a PRESENCE item
  (garlic ok→low) and reported the QUANTITY nudge (`pantry use chicken`) with chicken still **800 g** —
  **ledger untouched** (D4). GOOD vs §5.

### 2026-08-31 — Phase 4: orchestrator DESIGN session (the WAT "Agent") — DESIGN ONLY
- **Goal:** co-write the design-of-record for `pipeline/orchestrator.py` — the coordinator that chains the
  Phase 2a–3 tools into one end-to-end pantry→plate flow — see `docs/design/orchestrator.md`. This was a
  **design-only Elephant session**: no production code; the build is a separate clean execute session.
- **🐘 Elephant, dedicated:** drove CLAUDE.md §3's four pillars and the ADR-0010-D3 "deferred planning agent"
  question before drafting. **Central decision — agency:** the orchestrator is a **deterministic pipeline
  (DAG)**, NOT an LLM control loop; the agency lives in the *tools* it drives (`find_trending` is already a
  bounded web agent). This satisfies the WAT "Agent" role *and* Principle 10 by construction (no cycle to
  bound). Decisions D1–D8: trending-primary + Spoonacular fallback; pantry-derived intent (reuse
  `synthesize` → one `RecipeQuery`, mapped to `TrendingQuery`); new `pantry plan` (present-and-confirm);
  in-memory `PlanResult`; structured `StageTrace` observability; content→degrade / transport→abort +
  `MAX_RANK` cap; Tier-2 eval harness.
- **🎯 Eval criteria first:** good/bad **end-to-end** rubric written in the design (§5) — incl. the degraded
  (fallback) path — before any code.
- **🐠 Goldfish ×3 (the gate, run properly):** two **independent** no-context passes (55% / 62%) both failed
  on the *same* seams — the doc named the render schemas but hid their **fields** behind `…`, and `_timed`'s
  outcome/detail contract was under-specified. Folded verbatim field lists + exact import paths (§2), a
  fully-specified `_timed` (§4.2), and an exact CLI render contract (§4.8). A **confirming** pass (82%)
  caught two real defects — a `result.flipped` vs `cook_result.flipped` **naming collision** and missing
  `TrendingQuery` field types — folded in. **Final verification pass: 96%, zero blocking gaps.** Every
  surfaced seam fixed **in the DOC, not the goldfish**.
- **🧮 Model discipline:** the chained tools keep `--model opus` (unchanged); no new LLM boundary is added —
  the orchestrator adds *coordination*, and every existing LLM output stays Pydantic-validated in its tool.
- **📌 Principle 10 designed-in** (see the scorecard flip above): DAG + `MAX_RANK` + content→degrade /
  transport→abort + no auto-retry, all documented in §4.6.
- **✂️ Plan/execute split honored:** this was the **plan** session — design doc committed, then STOP. The
  build is a separate clean execute session (`writing-plans` → TDD) on `dev-feature-9-orchestrator`, where
  ADR 0011 + SOP 05 + the build's playbook entry will land. Claude opens the PR; the author merges.
- **Branching:** `dev-feature-9-orchestrator` off `main` (Phase 3 merged via PR #16). Design checkpoint
  committed on that branch.

### 2026-08-31 — Phase 4: orchestrator BUILD (the WAT "Agent") — EXECUTE session
- **Goal:** implement the committed design-of-record (`docs/design/orchestrator.md`, Goldfished ×3):
  `pipeline/orchestrator.py` (`make_plan` DAG + `_to_trending_query` + `_timed` + `MAX_RANK`), the
  `PlanResult`/`StageTrace` schemas, a `pantry plan` CLI, offline tests, a Tier-2 eval harness — see
  ADR 0011 + `workflows/05-orchestrator.md`.
- **✂️ Plan/execute split (clean):** a dedicated execute session — `writing-plans` → `executing-plans`,
  TDD, a commit per task (schemas → orchestrator → CLI+shared-render → eval harness → docs). Baseline
  confirmed green (129 tests) before Task 1.
- **✅ Verification-left:** TDD red→green, **fully offline** (injected fake synth/rank runners +
  trending/spoon fetchers + plain `Ingredient` objects; `session` fixture only for cook). 18 new tests
  (129→147), `mypy --strict` + `ruff` clean throughout. Asserts the flow, the map + flag overrides,
  `_timed` inference, BOTH degrade branches, transport propagation, synthesis abort, and the `MAX_RANK`
  cap — never timings.
- **📌 Principle 10 verified in code** (scorecard flipped ➖→✅): DAG (no loop), `MAX_RANK=5` cap,
  content→degrade / transport→abort, no auto-retry — implemented faithfully, not just designed.
- **🧮 Model discipline / determinism:** NO new LLM boundary — the orchestrator only *coordinates*; every
  chained tool keeps `--model opus` and stays Pydantic-validated inside itself; state mutation is only the
  existing `cook`.
- **R2 — go/no-go build spike ✅ (read-only, do-FIRST):** graded a real `synthesize → _to_trending_query →
  find_trending` on the §5 pantry. Intent = *"garlic soy chicken stir fry rice bowl with spinach"* (Asian,
  main course, ≤40 min) → a genuinely-trendy allow-listed recipe (Half Baked Harvest sesame-ginger chicken
  fried rice). **GO** — the pantry-derived-intent value holds; it does NOT collapse toward flags-only.
  (Note: a specific theme can narrow to few results — 1 here; non-blocking.)
- **R1 — `plan` vs `cook-ideas` overlap:** extracted the shared table/⚠-notes/shopping-list/cook-prompt
  rendering into `cli._present_ranked`, called by BOTH commands (no copy-paste); binding the cook return as
  `cook_result` sidesteps the `result.flipped` collision the Goldfish flagged. `cook-ideas` regression
  tests stay green. *Open question (deferred):* should `plan` eventually supersede `cook-ideas`?
- **🔬 Learning split:** author took the **fallback** (crunched) — Claude built the conceptual core
  (`make_plan` flow, the map, the stop conditions, the eval rubric) at the "code + explanation" rung for
  PR-style review; learning-first honored via review, momentum kept (as in Phase 2b/2c/3).
- **🔌 Live smoke ✅ (ranked path, loop closed):** seeded a throwaway pantry (temp `PANTRY_DB_PATH`, dev DB
  untouched); `pantry plan --verbose` → synth 11.6 s / trending 156 s (4 recipes) / rank 117 s (4); `fits`
  sorted fewest-missing (7/9/10/10), all allow-listed, honest ⚠ ("green onions ≠ bulb onion", "generic vs
  brown rice"); cooking #1 flipped `garlic`/`soy sauce` OK→LOW + reported QUANTITY nudges
  (`onion`/`chicken`/`rice`) — **ledger untouched** (3 `initial` txns only, chicken still 800 g). GOOD vs §5.
- **Honest finding — degrade NOT demoed live:** a gibberish `--theme` did NOT force a degrade — the agentic
  trending tool robustly returns trendy recipes even for a nonsense theme (empty trending is genuinely
  rare). The degrade path stays covered by offline unit tests + the eval-harness `degrade` scenario — a
  *spike-tells-you-the-truth* moment about the system's robustness, recorded rather than glossed.
- **Branching:** built on `dev-feature-9-orchestrator`; one PR bundles design + build; the author merges.

### 2026-09-01 — Model tiering + `plan` timeout resilience (ADR 0012) — first delegated build
- **Goal:** two things surfaced live-testing PR #17 (issue #18): (1) all LLM steps hardcoded `--model opus`
  (no model-tier discipline — Principle 1 gap); (2) `pantry plan` kept hitting the 180s trending timeout and
  aborting. See ADR 0012.
- **🧮 Model discipline (Principle 1 ➖→✅):** per-step tiers via a **runner factory** (`claude_runner` /
  `claude_web_runner` bake `--model` into argv; the `ClaudeRunner` seam is unchanged → zero test-fake churn).
  Policy in `core/models.py`: **Haiku** synth + resolve, **Sonnet** the agentic web search. **Latency, not
  cost** — inference is flat-rate on the subscription; a faster model is also what attacks the timeout.
- **🛟 Degrade-on-timeout (ADR 0012 D4, amends D7):** a trending `ClaudeCliError(kind="timeout")` now degrades
  to Spoonacular ideas instead of aborting; every other transport error still propagates. `pantry plan` never
  dies with `timed out after 180s`.
- **🤝 Delegation (Principle 4 ❌→✅) — first use, and a workflow shift:** the author adopted **"AI builds, I
  review" for the rest of the project.** This build ran **subagent-driven**: a **Sonnet** implementer per unit
  (TDD + `mypy --strict`/`ruff` gates), **Opus orchestrates + reviews each diff**, the author reviews the PR.
  4 units (transports+tiers → degrade-on-timeout → eval A/B → docs); each reviewed clean.
- **🎯 Evals decide the tiers (D5):** `evals/plan_eval.py` gained an A/B dimension (`tiered` vs `all-opus`),
  printing the deterministic scorecard **and** per-stage wall-clock — the live A/B is the loop-closer that
  confirms the tiers (or escalates a regressing step).
- **✅ Verification-left:** TDD throughout; 160 tests green (+10), `mypy --strict` + `ruff` clean.
- **Proportionate process:** ADR + TDD, **no** Goldfish×3 (smaller than a phase — "scale process to the task").
- **Branching:** `dev-feature-10-model-tiering` off `main` (PR #17 merged as `7029c10`); focused PR, author merges.

---

## 6. Roadmap — what's next (with rough time estimates)

Estimates assume **learning-first cadence** (type-every-line for core logic per CLAUDE.md §0),
so they're padded vs. a pro just shipping. "Session" ≈ one focused ~1–2 hr sitting.

| Next step | What it is | Est. effort |
|---|---|---|
| ~~**0. Close today's loop**~~ ✅ | Ran `suggest` w/o API key: SDK resolves OAuth creds & error-handling works; inference blocked by **$0 credit balance** (account, not code). | *done 2026-08-28* |
| **1. First Goldfish test** (habit adoption) | Take SOP `01-query-synthesis.md` + ADR 0006 to a fresh session; fix any gaps it can't implement from. | **~30–45 min** |
| ~~**2. Recipe-retrieval tool**~~ ✅ | `services/retrieval.py` + `core/spoonacular.py`: `RecipeQuery` → validated `Recipe`s via complexSearch (`sort=popularity`), offline-tested. ADR 0008 + SOP 02. | *done 2026-08-30* |
| **3. Recipe parsing** (LLM boundary #2) | LLM: raw recipe text → validated `Recipe` schema. New SOP + eval criteria + Goldfish test first. | **~2–3 sessions (4–6 hr)** |
| ~~**4. Semantic ingredient resolution**~~ ✅ (LLM boundary #3) | Match recipe ingredient lines ↔ pantry (LLM matches; code decides in-stock/rank/mutate), validated to schema. `services/resolver.py` + `pantry cook-ideas`. ADR 0010 + SOP 04. | *done 2026-08-30 (Phase 3)* |
| ~~**5. Phase-4 orchestrator**~~ ✅ (the WAT "Agent") | `pipeline/orchestrator.py` chains the tools into `pantry plan` — deterministic DAG, `MAX_RANK` cap, content→degrade / transport→abort, no loops (Principle 10 verified in code). ADR 0011 + SOP 05. | *done 2026-08-31 (Phase 4)* |
| **6. End-to-end smoke test** | Full pantry→plate run — **substantially covered by `pantry plan`** (ranked live smoke closed 2026-08-31); remaining: the degraded path live (trending rarely empties) + a broader persona sweep. | **~partial; ~0.5 session** |

**Critical path to a working agent:** steps 2 → 3 → 4 → 5 → 6 ≈ **~20–30 focused hours**,
spread across sessions, each gated by its own Elephant doc + Goldfish check.

---

## Sources
- Rensin, D. — *Elephants, Goldfish and the New Golden Age of Software Engineering* — [Google Research](https://research.google/pubs/elephants-goldfish-and-the-new-golden-age-of-software-engineering/) · [Medium](https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874)
- *Guide to AI Tokenomics: Eleven Principles for Token-Efficient Software Engineering* — [Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-ai-tokenomics-eleven-principles-for-token-efficient-software-engineering)
