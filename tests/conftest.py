"""Shared test fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.database import Database


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()
