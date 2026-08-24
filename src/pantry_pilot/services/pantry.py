from sqlmodel import Session  # the "cart" used to read/write the database

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.tables import Ingredient  # the table (row) we create here


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
