"""Tests for the update-notifier wiring (startup/update_notifier.py).

The GitHub probe itself is pinned in tests/core/test_update_check.py;
here we cover the seam: bus events → toast / dialog, and the
background-thread → GUI-thread hop.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.update_check import UpdateInfo
from polyglot_ai.startup import update_notifier as un


class _RecordingToastManager:
    def __init__(self) -> None:
        self.shown = []

    def show(self, notification) -> None:
        self.shown.append(notification)


@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    qtbot.addWidget(w)
    w._toast_manager = _RecordingToastManager()
    w._action_check_updates = QAction("Check for Updates…", w)
    return w


def _info() -> UpdateInfo:
    return UpdateInfo(
        current_version="0.13.0",
        latest_version="0.14.0",
        release_url="https://example.test/releases/v0.14.0",
        published_at="2026-07-18T00:00:00Z",
    )


def test_update_available_event_shows_toast(window):
    bus = EventBus()
    un.install_update_check(window, bus)

    bus.emit(un.EVT_UPDATE_AVAILABLE, info=_info())

    shown = window._toast_manager.shown
    assert len(shown) == 1
    assert "0.14.0" in shown[0].title
    assert "0.13.0" in shown[0].body


def test_update_available_with_no_info_is_silent(window):
    # The auto-check path never emits on "no update", but a stray
    # empty emit must not toast or crash.
    bus = EventBus()
    un.install_update_check(window, bus)

    bus.emit(un.EVT_UPDATE_AVAILABLE, info=None)

    assert window._toast_manager.shown == []


def test_missing_toast_manager_is_tolerated(qtbot):
    # install_notifications can fail (best-effort) — the update path
    # must degrade to a no-op, not an AttributeError.
    w = QMainWindow()
    qtbot.addWidget(w)
    w._action_check_updates = QAction("Check for Updates…", w)
    bus = EventBus()
    un.install_update_check(w, bus)

    bus.emit(un.EVT_UPDATE_AVAILABLE, info=_info())  # should not raise


def test_manual_check_reports_up_to_date(window, monkeypatch):
    seen = {}

    def fake_information(parent, title, text):
        seen["text"] = text

    monkeypatch.setattr(un.QMessageBox, "information", staticmethod(fake_information))
    bus = EventBus()
    un.install_update_check(window, bus)

    bus.emit(un.EVT_UPDATE_MANUAL_RESULT, info=None)

    assert "up to date" in seen["text"]


def test_manual_trigger_runs_check_and_emits_result(window, monkeypatch, qtbot):
    # End-to-end through the real background thread: action triggered
    # → check_for_update(force=True) on a worker → result emitted on
    # the bus. EventBus without a marshaller delivers synchronously on
    # the worker thread, which is fine for asserting the payload.
    results = []
    done = []

    monkeypatch.setattr(un, "check_for_update", lambda *, current_version, force: _info())

    bus = EventBus()
    un.install_update_check(window, bus)
    # Replace the GUI-facing subscriber so the test captures the
    # payload instead of opening a real dialog.
    bus._subscribers[un.EVT_UPDATE_MANUAL_RESULT] = [
        lambda info=None, **_: (results.append(info), done.append(True))
    ]

    window._action_check_updates.trigger()

    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert results[0].latest_version == "0.14.0"
