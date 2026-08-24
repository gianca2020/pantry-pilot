"""Database engine and session lifecycle for PantryPilot.

The engine is created once and pools connections to the SQLite file named by
Settings. `get_session()` hands out short-lived Sessions (units of work);
`init_db()` creates the tables (a no-op until Step 3 defines the models).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from pantry_pilot.core.config import Settings

settings = Settings()

# The "water main": created once, knows where the db lives (settings.database_url)
# and pools connections to it. echo=True makes it print every SQL statement.
engine = create_engine(settings.database_url, echo=settings.echo_sql)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """Turn on foreign-key enforcement for every new connection.

    SQLite ships with foreign keys OFF by default — without this, our ledger
    could orphan a PantryTransaction whose ingredient row was deleted.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create every table registered on SQLModel.metadata.

    A no-op until Step 3 defines the Ingredient / PantryTransaction models.
    """
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Open a Session (a unit-of-work 'cart') and close it when the block ends.

    Usage:  with get_session() as session: ...
    """
    with Session(engine) as session:
        yield session
