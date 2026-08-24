"""Shared pytest fixtures — a fresh in-memory database per test."""

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Import the models so their tables register on SQLModel.metadata before create_all.
from pantry_pilot.models.tables import Ingredient, PantryTransaction  # noqa: F401


@pytest.fixture
def session() -> Iterator[Session]:
    """Hand each test its own empty, in-memory database (gone when the test ends)."""
    engine = create_engine(
        "sqlite://",  # in-memory: lives in RAM, never touches disk
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # reuse one connection so the in-memory db persists
    )
    SQLModel.metadata.create_all(engine)  # build the Ingredient + PantryTransaction tables
    with Session(engine) as session:
        yield session
