from datetime import UTC, datetime

from sqlmodel import Session, col, select

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode, TxnReason
from pantry_pilot.models.tables import Ingredient, PantryTransaction


def add_ingredient(
    session: Session,
    name: str,
    category: Category,
    tracking_mode: TrackingMode,
    base_unit: BaseUnit | None = None,
    status: StockStatus | None = None,
) -> Ingredient:
    """Add a new ingredient to the pantry, enforcing the QUANTITY/PRESENCE rules."""
    # Step 1 — guard the rules. The table can't validate itself, so the service must.
    if tracking_mode == TrackingMode.QUANTITY and base_unit is None:
        raise ValueError("base_unit is required for QUANTITY tracking mode")
    if tracking_mode == TrackingMode.PRESENCE and status is None:
        raise ValueError("status is required for PRESENCE tracking mode")

    # Step 2 — build the row. A new counted item starts empty (0); presence items stay None.
    on_hand = 0 if tracking_mode == TrackingMode.QUANTITY else None
    ingredient = Ingredient(
        name=name,
        category=category,
        tracking_mode=tracking_mode,
        base_unit=base_unit,
        status=status,
        on_hand=on_hand,
    )

    # Step 3 — save it and hand it back.
    session.add(ingredient)
    session.commit()
    session.refresh(ingredient)
    return ingredient


def record_transaction(
    session: Session,
    ingredient: Ingredient,
    change_amount: int,
    reason: TxnReason,
    note: str | None = None,
) -> PantryTransaction:
    """Record a change in the ledger, then recompute the ingredient's on_hand from that ledger."""
    # Step 1 — guard: only counted (QUANTITY) items have a numeric ledger.
    if ingredient.tracking_mode != TrackingMode.QUANTITY:
        raise ValueError("only QUANTITY ingredients can record transactions")

    # Step 2 — append the change to the ledger.
    txn = PantryTransaction(
        ingredient_id=ingredient.id,
        change_amount=change_amount,
        reason=reason,
        note=note,
    )
    session.add(txn)

    # Step 3 — recompute on_hand = the SUM of this ingredient's whole ledger (the log is truth).
    transactions = session.exec(
        select(PantryTransaction).where(PantryTransaction.ingredient_id == ingredient.id)
    ).all()
    ingredient.on_hand = sum(t.change_amount for t in transactions)

    # Step 4 — save both, and hand back the new transaction.
    session.add(ingredient)
    session.commit()
    session.refresh(txn)
    return txn


def get_ingredient(session: Session, name: str) -> Ingredient | None:
    """Look up an ingredient by its unique name (None if not found)."""
    return session.exec(select(Ingredient).where(Ingredient.name == name)).first()


def list_ingredients(
    session: Session,
    include_archived: bool = False,
    category: Category | None = None,
) -> list[Ingredient]:
    """List ingredients — hides archived by default; optional category filter."""
    query = select(Ingredient)
    if not include_archived:
        query = query.where(col(Ingredient.is_active).is_(True))
    if category is not None:
        query = query.where(Ingredient.category == category)
    return list(session.exec(query).all())


def archive_ingredient(session: Session, ingredient: Ingredient) -> Ingredient:
    """Soft-delete: mark inactive and stamp archived_at. Never a hard DELETE."""
    ingredient.is_active = False
    ingredient.archived_at = datetime.now(UTC)
    session.add(ingredient)
    session.commit()
    session.refresh(ingredient)
    return ingredient


def set_status(session: Session, ingredient: Ingredient, status: StockStatus) -> Ingredient:
    """Update a PRESENCE ingredient's stock status (OUT / LOW / OK)."""
    if ingredient.tracking_mode != TrackingMode.PRESENCE:
        raise ValueError("only PRESENCE ingredients have a status")
    ingredient.status = status
    session.add(ingredient)
    session.commit()
    session.refresh(ingredient)
    return ingredient
