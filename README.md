# 🥫 PantryPilot

A production-grade, applied-AI CLI for managing a local pantry — built on a
deterministic, **event-sourced** data layer.

> **Status:** Phase 1 complete (persistent data layer + CRUD CLI). Phases 2–4
> (recipe synthesis, semantic deduction, orchestration & evals) are on the roadmap.

## What it does (today)

Track ingredients in a local SQLite pantry, where the current stock of anything is
*derived* from an append-only ledger of every change — so it can never silently drift.

```sh
uv run pantry add chicken --category protein --unit gram --amount 800
uv run pantry add salt --category staple --mode presence --status ok
uv run pantry list
uv run pantry use chicken 200        # on_hand: 800 -> 600 (summed from the ledger)
uv run pantry restock chicken 500
uv run pantry status salt low
uv run pantry remove salt            # soft-delete: archived, history preserved
```

## Setup

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is managed by uv.

```sh
uv sync                 # install dependencies into a local venv
uv run pantry --help    # see all commands
```

## Architecture (the interesting part)

- **Event-sourced ledger** — `PantryTransaction` is append-only and authoritative;
  `Ingredient.on_hand` is a cache recomputed as `SUM(change_amount)`. State is
  reconstructable and drift-proof.
- **Integer canonical units** (`each` / `gram` / `milliliter`) — exact arithmetic,
  no floating-point drift.
- **Per-ingredient tracking mode** — `QUANTITY` (counted, ledgered) vs `PRESENCE`
  (have / low / out), so staples aren't pretend-measured.
- **Soft-delete only** — the audit trail is never destroyed.

Every decision is recorded as an ADR in [`docs/adr/`](docs/adr/).

### Layout

```text
src/pantry_pilot/
  core/       # config (Settings) + database (engine, session, FK pragma)
  models/     # enums + SQLModel tables (Ingredient, PantryTransaction)
  services/   # the pantry service — all actions + the invariant guard
  cli.py      # Typer CLI entrypoint
tests/        # pytest (in-memory DB fixtures)
docs/adr/     # architecture decision records
```

## Development

```sh
uv run pytest        # tests
uv run mypy          # strict type checking
uv run ruff check    # lint
```

## Roadmap

1. ✅ **Phase 1** — persistent data layer & CRUD CLI
2. **Phase 2** — structured query synthesizer & web/recipe tooling
3. **Phase 3** — semantic inventory resolver & state mutation
4. **Phase 4** — evals, observability & orchestration
