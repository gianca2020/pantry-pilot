# ADR 0003 — Integer quantities in a canonical base unit

**Status:** Accepted

## Context
The ledger repeatedly sums quantities. Binary floats drift (`0.1 + 0.2 != 0.3`),
which would silently break the `on_hand == SUM(ledger)` invariant over time.

## Decision
Store quantities as **integers** in a canonical base unit per physical dimension
(`EACH` / `GRAM` / `MILLILITER`). Convert human units (cups, kg, tbsp) to the base
unit **once, at the input boundary**.

## Consequences
- Ledger sums are exact integer arithmetic — zero drift, fully deterministic.
- Imprecision in fuzzy human units ("1 cup") is rounded once, at the edge, instead
  of smeared through every calculation.
- Avoids the SQLite `Decimal` trap: SQLite has no native decimal type, so SQLAlchemy
  would silently coerce `Decimal` to `float` without a custom `TypeDecorator`.

## Alternatives rejected
- **`float`** — drift breaks the determinism guarantee.
- **`Decimal`** — exact, but needs extra plumbing to survive SQLite; integers are simpler.
