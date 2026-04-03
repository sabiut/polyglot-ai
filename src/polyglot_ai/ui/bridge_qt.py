"""QtBridgeAdapter — connects core EventBus to Qt signals for thread-safe UI updates."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from polyglot_ai.core.bridge import EventBus


class QtBridgeAdapter(QObject):
    """Marshals EventBus callbacks onto the Qt main thread via signal."""

    _dispatch = pyqtSignal(str, dict)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._slots: dict[str, list[Callable[..., Any]]] = {}
        self._dispatch.connect(self._on_dispatch)

    def bind(self, event: str, slot: Callable[..., Any]) -> None:
        """Subscribe to a core event, delivering on Qt main thread."""
        if event not in self._slots:
            self._slots[event] = []
            self._bus.subscribe(event, self._make_emitter(event))
        self._slots[event].append(slot)

    def _make_emitter(self, event: str) -> Callable[..., Any]:
        def emitter(**kwargs: Any) -> None:
            self._dispatch.emit(event, kwargs)

        return emitter

    def _on_dispatch(self, event: str, kwargs: dict) -> None:
        for slot in self._slots.get(event, []):
            slot(**kwargs)
