"""Shared test fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from polyglot_ai.core import aws_resources
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.database import Database

_REAL_RUNNING_INSTANCES = aws_resources.running_instances


@pytest.fixture(autouse=True)
def _no_instance_lookup_in_tests(monkeypatch):
    """No test reaches the AWS CLI through the connection dialog by accident.

    The dialog lists the account's running EC2 instances whenever
    Session Manager is the route, in the background. On a developer's
    machine that would run ``aws`` with their own credentials from any
    test that opens such a dialog. Tests of the lookup itself ask for
    :func:`real_running_instances` and replace the CLI underneath it.
    """
    monkeypatch.setattr(aws_resources, "running_instances", lambda profile="", region="": ([], ""))


@pytest.fixture
def real_running_instances():
    return _REAL_RUNNING_INSTANCES


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()
