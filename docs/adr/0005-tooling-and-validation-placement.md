# ADR 0005 — Tooling, FK enforcement, and where validation lives

**Status:** Accepted

## Context
We want a reproducible environment and modern Python. Two SQLite/SQLModel gotchas
shape the design: (1) SQLite disables foreign-key enforcement by default, and
(2) SQLModel `table=True` models **skip Pydantic validation** (verified via spike).

## Decision
- **Tooling:** `uv` for env/deps + a `src`-layout package + uv-managed Python 3.12.
- **FK enforcement:** enable `PRAGMA foreign_keys=ON` on every connection via a
  SQLAlchemy `connect` event listener.
- **Validation placement:** because table models don't validate, enforce the
  QUANTITY/PRESENCE invariant in the **service layer** (`add_ingredient`), the single
  place ingredients are created — not via a model validator (which would silently
  never run).

## Consequences
- Reproducible builds; the system Python is never touched.
- Foreign-key integrity is actually enforced (proven by a test).
- One validation chokepoint at the service — simple and sufficient for Phase 1.

## Alternatives rejected / deferred
- **Model `@model_validator`** — silently does nothing on `table=True` models.
- **Separate `IngredientCreate` schema** — the idiomatic SQLModel pattern; deferred
  until Phase 2, when validating untrusted LLM output makes it worth the extra layer.
