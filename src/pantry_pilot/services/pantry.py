from sqlmodel import Session, select  # the "cart" + the query builder

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode, TxnReason
from pantry_pilot.models.tables import Ingredient, PantryTransaction


def add_ingredient(
    session: Session,  # an open DB session (the cart) to save into
    name: str,  # e.g. "chicken"
    category: Category,  # PROTEIN / CARB / GREEN / STAPLE
    tracking_mode: TrackingMode,  # QUANTITY (counted) or PRESENCE (have/low/out)
    base_unit: BaseUnit | None = None,  # required for QUANTITY items
    status: StockStatus | None = None,  # required for PRESENCE items
) -> Ingredient:  # hands back the saved ingredient
    """Add a new ingredient to the pantry, enforcing the QUANTITY/PRESENCE rules."""
    # Step 1 — guard the rules. The table can't validate itself, so the service must.
    if tracking_mode == TrackingMode.QUANTITY and base_unit is None:
        raise ValueError("base_unit is required for QUANTITY tracking mode")
    if tracking_mode == TrackingMode.PRESENCE and status is None:
        raise ValueError("status is required for PRESENCE tracking mode")

    # Step 2 — build the row. A brand-new counted item starts empty (0); presence items stay None.
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
    session.add(ingredient)  # put it in the cart
    session.commit()  # check out: write it to the database
    session.refresh(ingredient)  # reload so the DB-assigned id is filled in
    return ingredient


def record_transaction(
    session: Session,
    ingredient: Ingredient,  # the whole object, so we can check its mode AND update its on_hand
    change_amount: int,  # signed: +restock / -consume
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
