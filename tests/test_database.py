"""Step 6 — DB-level integration test: foreign-key enforcement.

Proves the `PRAGMA foreign_keys=ON` decision (ADR 0005) actually holds: you cannot
insert a transaction that points at an ingredient that doesn't exist.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pantry_pilot.models.enums import TxnReason
from pantry_pilot.models.tables import PantryTransaction


def test_foreign_keys_are_enforced(session: Session) -> None:
    # ingredient_id=999 doesn't exist -> the FK constraint must reject the insert.
    orphan = PantryTransaction(ingredient_id=999, change_amount=100, reason=TxnReason.RESTOCK)
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()
