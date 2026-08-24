from sqlmodel import Session

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.tables import Ingredient


def add_ingredient(
    session: Session,
    name: str,
    category: Category,
    tracking_mode: TrackingMode,
    base_unit: BaseUnit | None = None,
    status: StockStatus | None = None,
) -> Ingredient:
    """Add a new ingredient to the pantry."""
    if tracking_mode == TrackingMode.QUANTITY and base_unit is None:
        raise ValueError("base_unit is required for QUANTITY tracking mode")
    if tracking_mode == TrackingMode.PRESENCE and status is None:
        raise ValueError("status is required for PRESENCE tracking mode")

    on_hand = 0 if tracking_mode == TrackingMode.QUANTITY else None
    ingredient = Ingredient(
        name=name,
        category=category,
        tracking_mode=tracking_mode,
        base_unit=base_unit,
        status=status,
        on_hand=on_hand,
    )
    session.add(ingredient)
    session.commit()
    session.refresh(ingredient)  # load the DB-assigned id
    return ingredient
