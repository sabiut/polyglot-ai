"""Tests for the Notifier policy.

These tests cover only the pure-Python policy layer in
``core/notifications.py``; the toast widget + tray wiring is
exercised manually since it requires a live Qt event loop and
display server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from polyglot_ai.constants import EVT_AI_ERROR, EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.notifications import Notification, NotificationLevel, Notifier
from polyglot_ai.core.task_manager import EVT_TASK_STATE_CHANGED


class _StubSettings:
    """Minimal SettingsManager double — no DB, no async."""

    def __init__(self, **overrides: Any) -> None:
        self._values: dict[str, Any] = {
            "notifications.enabled": True,
            "notifications.ai_long_response_seconds": 8,
        }
        self._values.update(overrides)

    def get(self, key: str) -> Any:
        return self._values.get(key)


@dataclass
class _Capture:
    """Records every Notification handed to the delivery callback."""

    received: list[Notification]

    def __call__(self, n: Notification) -> None:
        self.received.append(n)


@pytest.fixture
def harness():
    """Builds a Notifier + capture sink + a window-focus toggle."""
    bus = EventBus()
    settings = _StubSettings()
    notifier = Notifier(bus, settings)
    capture = _Capture(received=[])
    notifier.set_delivery(capture)

    state = {"focused": False}
    notifier.set_window_focused_check(lambda: state["focused"])
    notifier.start()
    return bus, settings, notifier, capture, state


# ── AI stream duration filter ────────────────────────────────────────


def test_short_stream_does_not_notify(harness):
    """A stream that finishes under the threshold is not noisy."""
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_AI_STREAM_CHUNK, content="hi")
    bus.emit(EVT_AI_STREAM_DONE)
    assert capture.received == []


def test_long_unfocused_stream_notifies(harness, monkeypatch):
    bus, _settings, notifier, capture, state = harness
    state["focused"] = False
    # Pin the start time so the duration calc is deterministic.
    notifier._stream_started_at = time.monotonic() - 30.0
    bus.emit(EVT_AI_STREAM_DONE)
    assert len(capture.received) == 1
    n = capture.received[0]
    assert n.level == NotificationLevel.SUCCESS
    assert "30s" in n.body
    assert n.prefer_os is True


def test_long_focused_stream_does_not_notify(harness):
    """If the user is looking at the window we don't interrupt."""
    bus, _settings, notifier, capture, state = harness
    state["focused"] = True
    notifier._stream_started_at = time.monotonic() - 30.0
    bus.emit(EVT_AI_STREAM_DONE)
    assert capture.received == []


def test_done_without_chunk_is_safe(harness):
    """A stray DONE without a preceding CHUNK must not crash or fire."""
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_AI_STREAM_DONE)
    assert capture.received == []


def test_chunk_only_stamps_first_arrival(harness):
    """The duration measurement starts at the first chunk, not the latest."""
    bus, _settings, notifier, _capture, _state = harness
    bus.emit(EVT_AI_STREAM_CHUNK, content="a")
    first = notifier._stream_started_at
    assert first is not None
    time.sleep(0.01)
    bus.emit(EVT_AI_STREAM_CHUNK, content="b")
    assert notifier._stream_started_at == first


# ── Errors ───────────────────────────────────────────────────────────


def test_error_always_notifies(harness):
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_AI_ERROR, error="HTTP 500: provider exploded")
    assert len(capture.received) == 1
    n = capture.received[0]
    assert n.level == NotificationLevel.ERROR
    assert "provider exploded" in n.body


def test_error_truncates_long_body(harness):
    bus, _settings, _notifier, capture, _state = harness
    long = "x" * 500
    bus.emit(EVT_AI_ERROR, error=long)
    body = capture.received[0].body
    # 140 chars + ellipsis.
    assert len(body) <= 141
    assert body.endswith("…")


def test_error_uses_default_body_when_empty(harness):
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_AI_ERROR, error="")
    assert capture.received[0].body == "AI request failed."


def test_error_focused_window_does_not_request_os(harness):
    bus, _settings, _notifier, capture, state = harness
    state["focused"] = True
    bus.emit(EVT_AI_ERROR, error="boom")
    assert capture.received[0].prefer_os is False


# ── Task state ───────────────────────────────────────────────────────


def test_task_done_notifies(harness):
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_TASK_STATE_CHANGED, task_id="abc12345xyz", state="done")
    assert len(capture.received) == 1
    n = capture.received[0]
    assert n.level == NotificationLevel.SUCCESS
    assert "abc12345" in n.body  # truncated to first 8


def test_task_failed_notifies_as_error(harness):
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_TASK_STATE_CHANGED, task_id="zzz", state="failed")
    assert capture.received[0].level == NotificationLevel.ERROR


def test_task_in_progress_does_not_notify(harness):
    """Intermediate task state changes are too chatty for notifications."""
    bus, _settings, _notifier, capture, _state = harness
    bus.emit(EVT_TASK_STATE_CHANGED, task_id="x", state="in_progress")
    assert capture.received == []


# ── Master switch ────────────────────────────────────────────────────


def test_disabled_swallows_everything(harness):
    bus, settings, notifier, capture, _state = harness
    settings._values["notifications.enabled"] = False
    notifier._stream_started_at = time.monotonic() - 30.0
    bus.emit(EVT_AI_STREAM_DONE)
    bus.emit(EVT_AI_ERROR, error="boom")
    bus.emit(EVT_TASK_STATE_CHANGED, task_id="x", state="done")
    assert capture.received == []


def test_threshold_falls_back_on_garbage(harness):
    """A non-numeric setting must not break the filter — fall back to default."""
    bus, settings, notifier, capture, state = harness
    settings._values["notifications.ai_long_response_seconds"] = "not-a-number"
    state["focused"] = False
    notifier._stream_started_at = time.monotonic() - 30.0
    bus.emit(EVT_AI_STREAM_DONE)
    # Default threshold is 8s; 30s elapsed → still fires.
    assert len(capture.received) == 1
