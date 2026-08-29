# CLAUDE.md — PantryPilot Engineering & Workflow Governance

Operating protocol for Claude Code on **pantry-pilot**: a production-grade applied-AI CLI that
manages pantry inventory through a deterministic, event-sourced ledger and (Phases 2–4) an
agentic recipe-synthesis pipeline.

Engineering model: the **WAT Framework (Workflows · Agent · Tools)**, mapped onto a standard
Python `src`-layout package. Decision (2026-08): keep the package for real distribution and
testability; honor WAT's decoupling *within* it, not as loose scripts.

---

## 0. Build Cadence (learning-first)

This project doubles as senior-engineering skill-building for the author. Default division of labor:

- **Claude writes** boilerplate, scaffolding, plumbing, and infrastructure (packaging, engine/session wiring, glue).
- **The author writes** the conceptual core — schemas, business logic, agentic reasoning — with Claude reviewing line-by-line like a PR.
- When "automate it for the human" (§2B) conflicts with hands-on learning, **learning wins for core logic**; speed wins for boilerplate.
- Teach concepts just-in-time; prefer failing-test-first (TDD).

---

## 0A. AI Engineering Principles — Elephant 🐘 / Goldfish 🐠 (governing)

Adopted 2026-08-28 from Rensin's *Elephants, Goldfish and the New Golden Age of Software
Engineering* + the *Eleven Principles for Token-Efficient Software Engineering*. Full scorecard,
session log, and roadmap live in **`docs/elephant-goldfish-playbook.md`** — read it at the start of
feature work and keep its session log + roadmap current.

**Claude MUST hold the author to these and call out violations, not wave them through:**

1. 🐘 **Elephant before code** — co-write a design doc / SOP *before* implementing. Design doc > code.
2. 🐠 **Goldfish-test the doc** — before coding an LLM step, verify a fresh, no-context reader could
   implement from the doc *alone*; if not, fix the DOC. (Currently our weakest habit — do it.)
3. 🎯 **Eval criteria first** — write examples of good vs bad output *before* writing the LLM step.
4. ✂️ **Plan-session ≠ execute-session** — design in one thread, implement in a clean one; commit each checkpoint.
5. 🧮 **Model discipline** — default to a balanced model; escalate only on failure or hard design work.
6. ✅ **Verification-left** — unit/functional tests early, UI/smoke last; LLM output always
   Pydantic-validated at the deterministic boundary.
7. ↩️ **Undo when adrift** — revert a bad state instead of stacking corrective prompts.
8. 📌 **Specific context, iterate on rules** — point to exact files; fix recurring issues *here* in
   CLAUDE.md (or the playbook), not by re-prompting.

Anti-patterns to refuse: one-shot "do everything" prompts; code before design; unsupervised
autonomy on complex tasks; accepting high-volume, low-quality output without structured review.

---

## 1. Core Operating Philosophy (WAT Framework)

Three decoupled layers:

```
+-------------------------------------------------------------+
|                       WORKFLOWS (W)                         |
|   Markdown SOPs: high-level intent, inputs, outputs, edges   |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                         AGENT (A)                           |
|       Coordinator: planning, routing, error handling         |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                         TOOLS (T)                           |
|   Deterministic, single-purpose Python modules / functions   |
+-------------------------------------------------------------+
```

**Mapping onto this package:**

| WAT layer | Home in pantry-pilot |
|-----------|----------------------|
| Workflows | `workflows/` — Markdown SOPs, one per pipeline stage (added Phase 2) |
| Agent     | `src/pantry_pilot/pipeline/orchestrator.py` (Phase 4) |
| Tools     | `src/pantry_pilot/services/*` — deterministic, single-responsibility; exposed as LLM-callable tools in Phases 2–3 |

**Determinism rule (non-negotiable):** persistent state and schema enforcement stay deterministic
(SQLModel/Pydantic, the event-sourced ledger). The LLM is used *only* for genuinely
non-deterministic steps — query synthesis, recipe parsing, semantic ingredient resolution — and its
outputs are always validated against Pydantic schemas before they touch state.

---

## 2. Anti-Overengineering & Clean Execution

### A. Strict scope & incremental delivery
- Never dump monolithic, multi-file setups in one unvetted step.
- Deliver iteratively: **Plan → Validate → Implement → Test → Update SOP/ADR**.
- Keep dependencies minimal; prefer stdlib / well-established packages. Add LLM/web deps only when the phase needs them.

### B. No human task flooding
- Automate configuration, environment init, and file creation via bash/tools where permitted.
- Prompt the author only for: (1) domain/business decisions, (2) secrets / API keys, (3) milestone verification, (4) core logic they are deliberately hand-writing to learn (see §0).

### C. Context & state discipline
- Persist durable decisions to the memory store (`~/.claude/.../memory/`) so context survives compaction and sessions.
- Keep tool interfaces deterministic with clean I/O contracts (typed returns / JSON / stdout).

---

## 3. Planning & Questioning Protocol (especially Phases 2–4)

Before a new workflow, tool, or refactor, do **not** write production code immediately. Plan and ask
focused questions across four pillars:

1. **Discovery & definition** — input format, data source, trigger; exact definition of done and output artifact.
2. **System boundaries & determinism** — which parts are deterministic (tools/code) vs LLM reasoning; what existing `services/` can be reused before building new.
3. **Failure modes & edge cases** — behavior on API failure, rate-limit, malformed payloads; retry/fallback before escalating.
4. **Architecture & complexity check** — is this the simplest viable approach; are we adding needless abstraction or dependencies.

---

## 4. Directory Structure (actual)

```text
pantry-pilot/
├── CLAUDE.md                 # this file (governance)
├── pyproject.toml            # uv-managed
├── src/pantry_pilot/
│   ├── core/                 # config.py, database.py (engine/session, FK pragma)
│   ├── models/               # SQLModel tables + Pydantic I/O schemas
│   ├── services/             # WAT "tools": deterministic, single-purpose
│   ├── pipeline/             # WAT "agent": orchestrator (Phase 4)
│   └── cli.py                # Typer entrypoint
├── workflows/                # WAT "SOPs" (Markdown) — added in Phase 2
├── tests/                    # pytest (TDD)
├── docs/adr/                 # Architecture Decision Records
└── data/                     # local SQLite db (gitignored)
```

---

## 5. Self-Improvement & Self-Correction Loop

On an execution error, test failure, or environment discrepancy:
1. **Analyze** the stdout/stderr trace; locate the root cause.
2. **Refactor** the isolated module (`services/…` or the relevant file) to fix it.
3. **Verify** by running the test(s) locally with real inputs.
4. **Document** the new edge case in the stage's SOP (`workflows/`) or an ADR (`docs/adr/`).
