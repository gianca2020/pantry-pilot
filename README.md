# 🥫 PantryPilot

A production-grade, applied-AI CLI for managing a local pantry — built on a deterministic,
**event-sourced** data layer, with an LLM used *only* at a single, schema-validated boundary.

> **Status:** Phase 1 complete (event-sourced pantry + CRUD CLI). **Phase 2a done** — `pantry suggest`
> synthesizes a recipe-search query from your pantry via the **Claude Code CLI on your subscription**.
> Phases 2b–4 (recipe retrieval, semantic resolution, orchestration & evals) are on the roadmap.

## What it does (today)

Track ingredients in a local SQLite pantry, where the current stock of anything is *derived* from an
append-only ledger of every change — so it can never silently drift. Then turn that pantry into a
structured recipe-search query with one guarded LLM call.

```sh
uv run pantry add chicken --category protein --unit gram --amount 800
uv run pantry add spinach --category green --mode presence --status ok
uv run pantry list
uv run pantry use chicken 200        # on_hand: 800 -> 600 (summed from the ledger)
uv run pantry restock chicken 500
uv run pantry status spinach low
uv run pantry remove spinach         # soft-delete: archived, history preserved

uv run pantry suggest                # LLM: pantry -> a validated RecipeQuery (JSON)
```

## Setup

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is managed by uv. `pantry suggest` also needs the
[Claude Code](https://code.claude.com) CLI installed and logged in — it runs on your Claude subscription
(no API key, no extra spend).

```sh
uv sync                 # install dependencies into a local venv
uv run pantry --help    # see all commands
```

## Architecture (the interesting part)

- **Event-sourced ledger** — `PantryTransaction` is append-only and authoritative; `Ingredient.on_hand`
  is a cache recomputed as `SUM(change_amount)`. State is reconstructable and drift-proof.
- **Integer canonical units** (`each` / `gram` / `milliliter`) — exact arithmetic, no floating-point drift.
- **Per-ingredient tracking mode** — `QUANTITY` (counted, ledgered) vs `PRESENCE` (have / low / out), so
  staples aren't pretend-measured.
- **Soft-delete only** — the audit trail is never destroyed.
- **A single, guarded LLM boundary** — `pantry suggest` is the *only* non-deterministic step. It runs
  headless (`claude -p --json-schema …`) on your subscription, and the reply is re-validated against a
  Pydantic `RecipeQuery` before anything downstream trusts it (belt + suspenders). No SDK, no API key.
  See [ADR 0007](docs/adr/0007-llm-boundary-via-claude-cli.md).

Every decision is recorded as an ADR in [`docs/adr/`](docs/adr/); every pipeline stage has a Markdown SOP
in [`workflows/`](workflows/).

### Layout

```text
src/pantry_pilot/
  core/       # config (Settings), database (engine/session), claude_cli (the LLM transport seam)
  models/     # enums + SQLModel tables (Ingredient, PantryTransaction) + Pydantic I/O schemas
  services/   # deterministic "tools": the pantry service + the recipe-query synthesizer
  cli.py      # Typer CLI entrypoint
tests/        # pytest (in-memory DB fixtures; the LLM boundary is faked — nothing shells out)
workflows/    # Markdown SOPs, one per pipeline stage
docs/adr/     # architecture decision records
docs/design/  # pre-implementation design docs (the "Elephant")
```

## How we build (the workflow)

PantryPilot doubles as a senior-engineering learning vehicle, so *how* it's built is part of the point.
Two frameworks govern it (full detail in [`CLAUDE.md`](CLAUDE.md)):

**WAT — Workflows · Agent · Tools.** Markdown SOPs (`workflows/`) describe intent; deterministic,
single-purpose Python modules (`services/`) are the tools; the LLM is confined to genuinely
non-deterministic steps, and its output is always Pydantic-validated at the boundary.

**🐘 Elephant / 🐠 Goldfish** (from Rensin's *Elephants, Goldfish and the New Golden Age of Software
Engineering*). As AI writes more code than a human can carefully read, the **design doc matters more than
the code**:
- 🐘 **Elephant** — a long design session that produces a detailed design doc *before any code*.
- 🐠 **Goldfish** — a fresh, no-context session asked to implement from that doc *alone*. If it can't,
  the **doc** is incomplete, not the goldfish.
- Plus: eval-criteria-first, plan-session ≠ execute-session, TDD / verification-left, and
  feature-branch → PR discipline (`dev-feature-<n>-<slug>` → reviewed PR).

The living scorecard + session log is [`docs/elephant-goldfish-playbook.md`](docs/elephant-goldfish-playbook.md).

### Are we actually following it? (honest self-assessment)

| Habit | Status | Evidence |
|---|---|---|
| Design-first (docs before code) | ✅ strong | 7 ADRs + SOPs; the Phase-2a rework had a full design doc before a line was written |
| 🐠 **Goldfish test** | ✅ **first done 2026-08-29** | the CLI-boundary design doc was implemented by two fresh, no-context agents — pass 1 found 3 blocking gaps → fixed the doc; pass 2 came back clean. *(Our #1 gap for months.)* |
| Eval-criteria-first | ✅ now | good/bad `RecipeQuery` examples written before the LLM step |
| Verification-left / TDD | ✅ strong | a failing test before each change; 48 offline tests, `mypy --strict`, `ruff` all green |
| Plan ≠ execute session | ✅ | designed in one thread, built in a clean one; one commit per task |
| Model discipline | ➖ improving | settled on the `--model opus` tier alias with a documented escalation note |
| Undo when adrift | ➖ ad hoc | not yet a formalized habit |

**Bottom line:** design-first and verification-left are strong; the **Goldfish test — long the biggest
gap — is now practiced**, and eval-criteria-first landed with it. The rest are in progress and tracked
honestly in the playbook.

## Development

```sh
uv run pytest        # tests (all offline — the LLM boundary is injected/faked)
uv run mypy src      # strict type checking
uv run ruff check    # lint
```

## Roadmap

1. ✅ **Phase 1** — event-sourced data layer & CRUD CLI
2. ✅ **Phase 2a** — recipe-query synthesizer at a single LLM boundary (`pantry suggest`, via the Claude Code CLI)
3. **Phase 2b** — recipe retrieval (Spoonacular `complexSearch`, `sort=popularity`)
4. **Phase 3** — semantic inventory resolver & state mutation
5. **Phase 4** — evals, observability & orchestration (the WAT "Agent")

Ideas & v2 features are tracked as [GitHub issues](https://github.com/gianca2020/pantry-pilot/issues)
(barcode scanner, macro-goal targeting, richer ingredient views, DB tools).
