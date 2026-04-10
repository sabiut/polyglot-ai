"""Shared fixtures for Qt UI tests (pytest-qt).

These tests spin up real ``QWidget`` instances against an offscreen
Qt platform so we can exercise the panel + dialog wiring without a
display. The key constraint: every test that creates a widget MUST
use the ``qtbot`` fixture from pytest-qt, which owns the
``QApplication`` lifecycle and guarantees cleanup.

A fresh ``TaskManager`` backed by a temp-file ``TaskStore`` is
provided per-test so store rows don't leak between cases.
"""

from __future__ import annotations

import os

import pytest

# Use Qt's offscreen platform so tests run in CI and on servers
# without a display. Must be set BEFORE QApplication is constructed
# (pytest-qt constructs it on first use of qtbot).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def task_manager(tmp_path):
    """Return a TaskManager bound to a fresh temp SQLite store.

    Each test gets an isolated DB file so runs can't cross-contaminate.
    The project root is set to ``tmp_path`` so ``create_task`` works.
    """
    from polyglot_ai.core.task_manager import TaskManager
    from polyglot_ai.core.task_store import TaskStore

    store = TaskStore(path=tmp_path / "tasks.db")
    manager = TaskManager(store=store)
    manager.set_project_root(tmp_path)
    return manager
