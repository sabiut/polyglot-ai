"""Cross-cutting notification service.

The :class:`Notifier` is a thin orchestrator that listens to the
shared :class:`~polyglot_ai.core.bridge.EventBus`, applies user-level
filtering (window focus, "long response" threshold), and forwards
the surviving events to a delivery callback.

Delivery is intentionally injected, not owned: the UI layer creates
the actual toast widget and the system-tray hook, then calls
:py:meth:`Notifier.set_delivery` so this module stays pure-Python and
testable without Qt.

Filtering rules — kept conservative on purpose, the goal is "useful,
never spammy":

* ``EVT_AI_STREAM_DONE`` only fires a notification when the elapsed
  stream duration exceeds ``ai_long_response_seconds`` *and* the
  window is currently unfocused. Quick replies on a focused window
  produce nothing.
* ``EVT_AI_ERROR`` fires unconditionally — even on a focused window
  we want a clear toast, since the error path may be hidden in the
  middle of a long chat.
* ``EVT_TASK_STATE_CHANGED`` only fires on transition into a
  terminal state (``done``/``failed``); intermediate updates are
  swallowed.

The class is deliberately not a QObject — it's wired before the Qt
event loop starts (see ``startup/services.py``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from polyglot_ai.constants import EVT_AI_ERROR, EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.settings import SettingsManager
from polyglot_ai.core.task_manager import EVT_TASK_STATE_CHANGED

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    """Severity tier — the UI layer maps these to colours/sounds."""

    INFO = "info"
    SUCCESS = "success"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class Notification:
    """An immutable notification payload handed to the delivery callback."""

    title: str
    body: str
    level: NotificationLevel = NotificationLevel.INFO
    # When True, the UI should show this via the OS tray (if available),
    # in addition to (or instead of) the in-app toast. The Notifier sets
    # this flag based on window focus — UI doesn't have to know.
    prefer_os: bool = False


# Type aliases for the injected hooks. Both default to no-ops so the
# Notifier is safe to instantiate before the UI layer wires its
# delivery callback.
DeliveryFn = Callable[[Notification], None]
WindowFocusedFn = Callable[[], bool]


class Notifier:
    """Listens to the EventBus and forwards filtered events to the UI.

    The Notifier owns the *policy* (when to fire, what severity, what
    body text). The UI owns the *delivery* (toast widget, system
    tray). They communicate through the :class:`Notification`
    dataclass.

    Lifetime: app-wide; constructed in ``startup/services.py`` once
    settings are loaded. Subscriptions are registered in ``start()``
    so tests can construct without side-effects.
    """

    def __init__(self, event_bus: EventBus, settings: SettingsManager) -> None:
        self._event_bus = event_bus
        self._settings = settings
        self._deliver: DeliveryFn = lambda _n: None
        self._window_focused: WindowFocusedFn = lambda: True
        # AI streaming state. The bus emits one
        # EVT_AI_STREAM_CHUNK per delta and a single EVT_AI_STREAM_DONE
        # at the end; we capture the timestamp of the *first* chunk
        # since the previous DONE so we can measure duration. None
        # means "no stream in flight".
        self._stream_started_at: Optional[float] = None

    # ── Wiring ──────────────────────────────────────────────────────

    def set_delivery(self, deliver: DeliveryFn) -> None:
        """Register the UI delivery hook. Called once from main_window."""
        self._deliver = deliver

    def set_window_focused_check(self, check: WindowFocusedFn) -> None:
        """Register a callable that returns True iff the main window has focus."""
        self._window_focused = check

    def start(self) -> None:
        """Subscribe to the event bus. Idempotent."""
        self._event_bus.subscribe(EVT_AI_STREAM_CHUNK, self._on_ai_chunk)
        self._event_bus.subscribe(EVT_AI_STREAM_DONE, self._on_ai_done)
        self._event_bus.subscribe(EVT_AI_ERROR, self._on_ai_error)
        self._event_bus.subscribe(EVT_TASK_STATE_CHANGED, self._on_task_state)

    # ── Filtering / dispatch ────────────────────────────────────────

    def _enabled(self) -> bool:
        return bool(self._settings.get("notifications.enabled"))

    def _long_response_seconds(self) -> float:
        # Defensive cast — settings values come from a JSON cache that a
        # caller could mistype. Falling back to the default keeps the
        # filter usable rather than silently behaving as "always on" or
        # "never on".
        try:
            return float(self._settings.get("notifications.ai_long_response_seconds") or 8.0)
        except (TypeError, ValueError):
            return 8.0

    def _on_ai_chunk(self, **_kwargs) -> None:
        # Only stamp the start time on the *first* chunk of a stream.
        # Subsequent chunks within the same stream are no-ops here so
        # the duration measurement is from t=0, not t=last-chunk.
        if self._stream_started_at is None:
            self._stream_started_at = time.monotonic()

    def _on_ai_done(self, **_kwargs) -> None:
        started = self._stream_started_at
        # Reset before any early-return so a missed start doesn't
        # poison the next stream.
        self._stream_started_at = None
        if not self._enabled() or started is None:
            return
        elapsed = time.monotonic() - started
        if elapsed < self._long_response_seconds():
            return
        if self._window_focused():
            # User is looking at the window — they already saw it
            # finish. No need to interrupt.
            return
        self._deliver(
            Notification(
                title="AI response ready",
                body=f"Your AI request finished after {elapsed:.0f}s.",
                level=NotificationLevel.SUCCESS,
                prefer_os=True,
            )
        )

    def _on_ai_error(self, *, error: str = "", **_kwargs) -> None:
        if not self._enabled():
            return
        # Trim long error bodies — toasts get unreadable past ~140 chars,
        # and the original message is already in the chat anyway.
        body = error[:140] + ("…" if len(error) > 140 else "") if error else "AI request failed."
        self._deliver(
            Notification(
                title="AI error",
                body=body,
                level=NotificationLevel.ERROR,
                prefer_os=not self._window_focused(),
            )
        )

    def _on_task_state(self, *, task_id: str = "", state: str = "", **_kwargs) -> None:
        if not self._enabled() or state not in ("done", "failed"):
            return
        if state == "done":
            level = NotificationLevel.SUCCESS
            title = "Task complete"
            body = f"Task {task_id[:8] or '?'} marked done."
        else:
            level = NotificationLevel.ERROR
            title = "Task failed"
            body = f"Task {task_id[:8] or '?'} hit an error."
        self._deliver(
            Notification(
                title=title,
                body=body,
                level=level,
                prefer_os=not self._window_focused(),
            )
        )
