# ADR 0004 — Per-ingredient tracking modes + soft delete

**Status:** Accepted

## Context
Not every ingredient deserves numeric tracking — nobody deducts 3 g of cumin.
Forcing everything into grams is fake precision and pure data-entry tax. Separately,
deleting an ingredient must not orphan its append-only ledger.

## Decision
Each ingredient declares a **tracking mode**:
- `QUANTITY` — integer stock in a base unit, backed by the ledger (e.g. chicken, rice).
- `PRESENCE` — a coarse status (`OUT` / `LOW` / `OK`), no arithmetic (e.g. salt, spices).

Retiring an ingredient is a **soft delete** (`is_active=False` + `archived_at`), never
a hard `DELETE`.

## Consequences
- Honest tracking: count what's worth counting; don't pretend-measure staples.
- The append-only audit and foreign-key integrity are preserved (no orphaned rows).
- `list` hides archived items by default; deduction logic branches on tracking mode.

## Alternatives rejected
- **Uniform numeric tracking** — fake precision and needless entry for staples.
- **Hard delete** — destroys audit history and violates foreign-key integrity.
