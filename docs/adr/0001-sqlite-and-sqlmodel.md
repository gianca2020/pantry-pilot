# ADR 0001 — SQLite + SQLModel for the data layer

**Status:** Accepted

## Context
PantryPilot needs persistent, queryable, integrity-checked pantry state. It's a
local, single-user CLI, and a learning vehicle for senior data-modeling.

## Decision
Use **SQLite** (embedded, single-file DB) with **SQLModel** (SQLAlchemy + Pydantic)
as the ORM/type layer.

## Consequences
- Zero setup — the DB is one file (`data/pantry.db`); no server to run.
- It's *real* SQL (transactions, foreign keys, joins), so every concept transfers
  to Postgres later.
- The ORM decouples us from the engine: swapping to Postgres later is mostly a
  `database_url` change (see the `Settings.database_url` seam).
- SQLite ships with foreign-key enforcement **off** — we must enable it (see ADR 0005).

## Alternatives rejected
- **JSON flat file** — no queries, no integrity, no transactions.
- **Raw `sqlite3`** — no ORM, no type safety, hand-written SQL everywhere.
- **Postgres/MySQL** — a client-server database is overkill for a local single-user CLI.
