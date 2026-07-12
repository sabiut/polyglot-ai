"""EventBus — pure-Python pub/sub for core↔UI communication.

No Qt imports allowed in this module. The UI layer connects
to this bus via QtBridgeAdapter.

Threading model
---------------
``EventBus`` is **synchronous and NOT thread-safe**. Key rules for callers:

1. ``emit()`` runs every subscriber inline, on the thread that called
   ``emit()``. A slow subscriber blocks the emitter.
2. Subscribers must assume they are running on whatever thread emitted
   the event. In this app that is overwhelmingly the Qt GUI thread, and
   subscribers should not rely on anything else.
3. Background producers (``threading.Thread`` workers, futures fired
   off with ``safe_task``, subprocess callbacks, etc.) historically had
   to marshal back onto the GUI thread before calling ``emit()``. That
   is now handled centrally: when the UI layer installs a marshaller via
   :meth:`EventBus.set_marshaller` (``QtBridgeAdapter`` does this on
   construction), off-thread ``emit()`` calls are delivered on the GUI
   thread automatically — inline when already on it, queued otherwise.
   With no marshaller installed (tests, headless), delivery is fully
   synchronous on the caller's thread, as before. Producers may still
   marshal explicitly, but a stray worker-thread ``emit()`` (e.g. from
   ``file_ops`` running under ``asyncio.to_thread``) no longer mutates
   Qt widgets off-thread.
4. ``subscribe()`` / ``unsubscribe()`` are not synchronized. Register
   subscribers during wiring (single-threaded app startup); avoid
   mutating the subscriber list from arbitrary threads.
5. Subscriber exceptions are caught and logged so one broken listener
   cannot poison the rest of the dispatch, but the emitter itself still
   sees synchronous completion.

If you need cross-thread delivery, wrap the bus in a queued adapter —
do not reach into this class. Keeping this tight is how the rest of the
codebase stays simple.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """Simple synchronous publish/subscribe event system."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        # Optional hook (installed by the Qt layer) that runs a delivery
        # closure on the correct thread. See set_marshaller / emit.
        self._marshaller: Callable[[Callable[[], None]], None] | None = None

    def set_marshaller(self, marshaller: Callable[[Callable[[], None]], None] | None) -> None:
        """Install (or clear) a thread-marshalling hook for ``emit``.

        ``marshaller(deliver)`` receives a zero-argument ``deliver``
        closure and must invoke it on the thread where subscribers are
        safe to run (the Qt GUI thread) — inline if already there,
        queued otherwise. ``QtBridgeAdapter`` installs this so a stray
        worker-thread ``emit()`` doesn't touch Qt widgets off-thread.
        Pass ``None`` to restore fully-synchronous delivery.
        """
        self._marshaller = marshaller

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, **kwargs: Any) -> None:
        # Snapshot subscribers so subscribe/unsubscribe during dispatch
        # (or on another thread) can't corrupt the iteration.
        subscribers = list(self._subscribers.get(event, []))
        if not subscribers:
            return

        def deliver() -> None:
            for callback in subscribers:
                try:
                    callback(**kwargs)
                except Exception:
                    logger.exception("Error in event handler for %s", event)

        if self._marshaller is not None:
            self._marshaller(deliver)
        else:
            deliver()

    def clear(self) -> None:
        self._subscribers.clear()
