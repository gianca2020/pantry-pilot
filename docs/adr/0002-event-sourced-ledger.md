# ADR 0002 — Event-sourced ledger as the source of truth

**Status:** Accepted

## Context
Stock changes over time. We could store a single mutable `quantity` column, or a
log of every change. We want deterministic, auditable, drift-proof state.

## Decision
`PantryTransaction` is an **append-only ledger** and the single source of truth.
`Ingredient.on_hand` is a **cache**, recomputed as `SUM(change_amount)` over the
ingredient's transactions inside the same DB transaction as each append.

## Consequences
- Full audit trail — every change is preserved with a reason and timestamp.
- Self-correcting — recomputing from the whole ledger means `on_hand` can never drift.
- A deduction is a single append (atomic; nothing to keep in sync).
- Cost: a read/aggregate on each write (negligible for a local pantry).

## Alternatives rejected
- **Mutable `quantity` as truth** — requires two writes (column + log) that can
  diverge; drift and audit-loss risk. The log would be decorative, not authoritative.
