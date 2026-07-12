"""QtBridgeAdapter — connects core EventBus to Qt signals for thread-safe UI updates."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from polyglot_ai.core.bridge import EventBus


class QtBridgeAdapter(QObject):
    """Marshals EventBus callbacks onto the Qt main thread via signal.

    Two responsibilities:

    * :meth:`bind` — subscribe a slot that is guaranteed to run on the
      GUI thread even if the event was emitted from a worker.
    * Installing itself as the bus's *marshaller* (on construction) so
      that **every** ``emit()`` — including from raw ``subscribe()``
      subscribers and stray worker-thread emitters like ``file_ops``
      under ``asyncio.to_thread`` — is delivered on the GUI thread.
      Without this, a background ``emit()`` ran subscriber code (and its
      Qt widget mutations) on the worker thread, which is undefined
      behavior in PyQt6.
    """

    _dispatch = pyqtSignal(str, dict)
    # Carries a zero-arg delivery closure from a worker thread to the GUI
    # thread. ``object`` because the payload is a plain Python callable.
    _marshal = pyqtSignal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._slots: dict[str, list[Callable[..., Any]]] = {}
        self._dispatch.connect(self._on_dispatch)
        self._marshal.connect(self._run_deliver)
        # Route all off-thread emits onto the GUI thread.
        bus.set_marshaller(self._marshal_deliver)

    def _marshal_deliver(self, deliver: Callable[[], None]) -> None:
        """Run ``deliver`` on the GUI thread — inline if already there."""
        if QThread.currentThread() is self.thread():
            # Common case: emit came from the GUI thread. Deliver inline
            # so ordering and synchrony match the pre-marshaller behavior.
            deliver()
        else:
            # Off-thread emit — hand the closure to the GUI thread via a
            # queued signal so subscribers never touch widgets off-thread.
            self._marshal.emit(deliver)

    def _run_deliver(self, deliver: Callable[[], None]) -> None:
        deliver()

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
