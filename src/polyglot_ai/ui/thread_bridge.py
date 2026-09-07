"""Run blocking work off the GUI thread and hand the result back safely.

``QTimer.singleShot(0, fn)`` called from a plain ``threading.Thread``
never fires: the timer is created in a thread with no Qt event
dispatcher, Qt logs "Timers can only be used with threads started
with QThread", and the callback is dropped on the floor. Several
panels shipped with exactly that pattern, which is why their
background refreshes silently never updated the UI. A queued
``pyqtSignal`` is the one reliable route back, so this module gives
every panel the same small helper instead of each re-inventing it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class _GuiInvoker(QObject):
    # ``object`` because the payload is a zero-arg Python callable.
    _call = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run)

    @staticmethod
    def _run(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            logger.exception("GUI-thread callback failed")


_invoker: _GuiInvoker | None = None
_invoker_lock = threading.Lock()


def _get_invoker() -> _GuiInvoker:
    global _invoker
    with _invoker_lock:
        if _invoker is None:
            inv = _GuiInvoker()
            app = QApplication.instance()
            # Created lazily, possibly from a worker thread — park it on
            # the GUI thread so the queued connection lands there.
            if app is not None and inv.thread() is not app.thread():
                inv.moveToThread(app.thread())
            _invoker = inv
        return _invoker


def call_on_gui(fn: Callable[[], None]) -> None:
    """Run ``fn`` on the GUI thread — inline if already there."""
    inv = _get_invoker()
    if QThread.currentThread() is inv.thread():
        fn()
    else:
        inv._call.emit(fn)


def _widget_alive(owner: QObject | None) -> bool:
    if owner is None:
        return True
    try:
        from PyQt6 import sip

        return not sip.isdeleted(owner)
    except Exception:
        return True


def run_in_thread(
    work: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None] | None = None,
    *,
    owner: QObject | None = None,
    name: str | None = None,
) -> threading.Thread:
    """Run ``work()`` on a daemon thread; deliver its result on the GUI thread.

    ``on_done(result)`` runs on the GUI thread when ``work`` returns;
    ``on_error(exc)`` when it raises (always logged either way). Pass the
    panel as ``owner`` so callbacks are skipped once it has been
    destroyed — a slow subprocess must not poke a deleted widget.
    """
    _get_invoker()  # create on the calling (GUI) thread

    def runner() -> None:
        try:
            result = work()
        except Exception as exc:
            logger.exception("Background work %r failed", name or work)
            # Python clears ``exc`` when the except block ends, so bind
            # it to a name the deferred callback can still see.
            error = exc
            if on_error is not None:
                call_on_gui(lambda: _widget_alive(owner) and on_error(error))
            return
        call_on_gui(lambda: _widget_alive(owner) and on_done(result))

    thread = threading.Thread(target=runner, daemon=True, name=name)
    thread.start()
    return thread
