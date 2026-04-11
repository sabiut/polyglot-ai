"""Tests for ``core.panel_state`` — the shared snapshot store.

The store is tiny (set/get/clear guarded by a lock), but it IS the
single source of truth for review state the AI sees, so we pin:

- fresh module state returns None
- set/get roundtrip preserves the dict identity
- clear resets to None
- None can be passed to set_last_review to clear
"""

from __future__ import annotations

import pytest

from polyglot_ai.core import panel_state


@pytest.fixture(autouse=True)
def _reset_panel_state():
    panel_state.clear()
    yield
    panel_state.clear()


def test_fresh_state_is_none():
    assert panel_state.get_last_review() is None


def test_set_and_get_roundtrip():
    snap = {"mode": "docker_compose", "total": 3}
    panel_state.set_last_review(snap)
    got = panel_state.get_last_review()
    assert got == snap  # content matches
    assert got is not snap  # shallow copy, not the same object


def test_set_none_clears():
    panel_state.set_last_review({"mode": "working"})
    assert panel_state.get_last_review() is not None
    panel_state.set_last_review(None)
    assert panel_state.get_last_review() is None


def test_clear_resets():
    panel_state.set_last_review({"mode": "working"})
    panel_state.clear()
    assert panel_state.get_last_review() is None


def test_last_write_wins():
    panel_state.set_last_review({"mode": "working"})
    panel_state.set_last_review({"mode": "dockerfile"})
    got = panel_state.get_last_review()
    assert got == {"mode": "dockerfile"}


# ── Workflow run state ─────────────────────────────────────────────────


def test_workflow_run_fresh_is_none():
    assert panel_state.get_last_workflow_run() is None


def test_workflow_run_set_and_get():
    snap = {"workflow": "verify-deploy", "status": "completed", "steps_completed": 3}
    panel_state.set_last_workflow_run(snap)
    got = panel_state.get_last_workflow_run()
    assert got == snap
    assert got is not snap  # shallow copy


def test_workflow_run_clear():
    panel_state.set_last_workflow_run({"workflow": "test"})
    panel_state.clear()
    assert panel_state.get_last_workflow_run() is None
    assert panel_state.get_last_review() is None
