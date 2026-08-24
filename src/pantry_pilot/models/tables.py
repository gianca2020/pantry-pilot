from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode


class Ingredient(SQLModel, table=True):
    """A pantry item. table=True makes this a real DB table (one row per ingredient)."""

    id: int | None = Field(default=None, primary_key=True)  # unique row id; the DB assigns it
    name: str = Field(index=True, unique=True)  # canonical name; no duplicates, fast lookup
    category: Category  # PROTEIN / CARB / GREEN / STAPLE
    tracking_mode: TrackingMode  # QUANTITY (counted) or PRESENCE (have/low/out)

    base_unit: BaseUnit | None = None  # QUANTITY only: EACH / GRAM / MILLILITER
    on_hand: int | None = None  # QUANTITY only: current amount (cached ledger sum; int = exact)
    status: StockStatus | None = None  # PRESENCE only: OUT / LOW / OK

    is_active: bool = True  # soft-delete flag; False = archived
    archived_at: datetime | None = None  # when it was archived (if ever)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # created (UTC)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # updated (UTC)
