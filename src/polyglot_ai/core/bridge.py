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
3. **Background producers (``threading.Thread`` workers, futures fired
   off with ``safe_task``, subprocess callbacks, etc.) MUST marshal back
   onto the GUI thread before calling ``emit()``.** Use a Qt signal or
   ``QTimer.singleShot(0, lambda: bus.emit(...))`` from the worker
   thread; do not emit directly.
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

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, **kwargs: Any) -> None:
        # Iterate over a shallow copy to safely handle subscribe/unsubscribe during emit
        for callback in list(self._subscribers.get(event, [])):
            try:
                callback(**kwargs)
            except Exception:
                logger.exception("Error in event handler for %s", event)

    def clear(self) -> None:
        self._subscribers.clear()
