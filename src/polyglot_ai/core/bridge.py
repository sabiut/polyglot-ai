"""EventBus — pure-Python pub/sub for core↔UI communication.

No Qt imports allowed in this module. The UI layer connects
to this bus via QtBridgeAdapter.
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
