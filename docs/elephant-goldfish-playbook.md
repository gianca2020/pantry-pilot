# Elephant 🐘 · Goldfish 🐠 — AI Engineering Playbook

> A living checklist for how **Gian + Claude** build PantryPilot with AI, adapted from
> Dave Rensin's *"Elephants, Goldfish and the New Golden Age of Software Engineering"*
> (Google Research, 2026) and the companion *"Eleven Principles for Token-Efficient
> Software Engineering"* (Google Cloud).
>
> **How to use this doc:** skim §1–§2 at the start of a work session; check the scorecard
> in §3 when planning a feature; log the session in §5; keep §6 (roadmap) honest.
> This doc is the reminder — if a habit here isn't happening, that's the signal to fix it.

Last updated: **2026-08-30**

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
| 1 | Start with a balanced model, scale up on failure | ❌ | No model-tier discipline yet. Decide a default + when to escalate. |
| 2 | Use skills/reusable workflows from the start | ➖ | CLAUDE.md governance ✅; packaged, reusable skills ❌. |
| 3 | Automate with scripts; read-only research *before* writing | ✅ | This session: checked installs & read official docs before acting. |
| 4 | Delegate output-heavy tasks to sub-agents; reconcile results only | ❌ | Not used yet; candidate for recipe retrieval/parsing research. |
| 5 | Divide & conquer: plan in Elephant, execute in clean Goldfish, checkpoint via commits | ➖ | Small focused commits ✅; explicit plan-session/execute-session split ❌. |
| 6 | **Shift verification left** (unit/functional early, UI/smoke last) | ✅ | Tests for every module (`test_*.py`), TDD-first. Strong. |
| 7 | Undo when adrift (revert, don't stack fixes) | ❓ | Not a formalized habit. |
| 8 | Be specific with context (exact files, inline `// FIX THIS`) | ✅ | This session pointed to `llm.py:11-17`, exact commands. |
| 9 | Iterate on *rules* (CLAUDE.md/AGENTS.md), not repeated prompts | ✅ | CLAUDE.md + the memory store are exactly this. |
| 10 | Avoid uncontrolled loops; event-driven, hard stop conditions | ⏳ | N/A until the Phase-4 orchestrator exists — bake it in then. |
| 11 | New session per new topic (reduce context bloat) | ➖ | Ad hoc. Pairs naturally with the Goldfish habit. |

---

## 3. Scorecard summary

**Already strong (keep doing):** design-first docs, verification-left/TDD, iterate-on-rules,
specific-context, read-only research before acting.

**Gaps we're deliberately adopting (in priority order):**
1. 🐠 **Run the Goldfish test** — after writing a SOP/ADR, open a fresh session, hand it *only* that doc, and ask "could you implement this?" Fix the doc where it stumbles. *(Principle 5 / essay.)* **✅ First done 2026-08-29 — the CLI-boundary design doc, two passes (see session log).**
2. 🎯 **Co-design eval criteria first** — before coding an LLM step, write down what a good/bad output looks like. *(Principle 6 / essay.)*
3. ✂️ **Split plan-session from execute-session** — design in one thread, implement in a clean one, checkpoint each with a commit. *(Principle 5 / eleven.)*
4. 🧮 **Model-tier discipline** — pick a default model; note when to escalate. *(Principle 1 / eleven.)*
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
| **4. Semantic ingredient resolution** (LLM boundary #3) | Match recipe ingredients ↔ pantry inventory (fuzzy/semantic), validated to schema. | **~2–3 sessions (4–6 hr)** |
| **5. Phase-4 orchestrator** (the WAT "Agent") | `pipeline/orchestrator.py` wires the tools + LLM steps; add hard stop conditions & no uncontrolled loops (Principle 10). | **~3–4 sessions (6–8 hr)** |
| **6. End-to-end smoke test** | Full `suggest → retrieve → parse → resolve` run; UI/smoke tests last (verification-left). | **~1 session (1–2 hr)** |

**Critical path to a working agent:** steps 2 → 3 → 4 → 5 → 6 ≈ **~20–30 focused hours**,
spread across sessions, each gated by its own Elephant doc + Goldfish check.

---

## Sources
- Rensin, D. — *Elephants, Goldfish and the New Golden Age of Software Engineering* — [Google Research](https://research.google/pubs/elephants-goldfish-and-the-new-golden-age-of-software-engineering/) · [Medium](https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874)
- *Guide to AI Tokenomics: Eleven Principles for Token-Efficient Software Engineering* — [Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-ai-tokenomics-eleven-principles-for-token-efficient-software-engineering)
