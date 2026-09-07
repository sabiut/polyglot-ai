"""Tests for the worker-thread → GUI-thread bridge.

Pins the reason the module exists: ``QTimer.singleShot`` from a plain
``threading.Thread`` silently never fires, so a result marshalled that
way is lost. The bridge must deliver on the GUI thread every time.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QThread, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from polyglot_ai.ui.thread_bridge import call_on_gui, run_in_thread  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _pump_until(pred, timeout_s: float = 3.0) -> bool:
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_qtimer_singleshot_from_worker_thread_never_fires(qapp):
    """The bug the bridge works around — documented so nobody 'simplifies' back to it."""
    fired = []
    t = threading.Thread(target=lambda: QTimer.singleShot(0, lambda: fired.append(1)))
    t.start()
    t.join()
    assert not _pump_until(lambda: fired, timeout_s=0.3)


def test_run_in_thread_delivers_result_on_gui_thread(qapp):
    seen = {}
    gui_thread = QThread.currentThread()

    def work():
        assert QThread.currentThread() is not gui_thread
        return 42

    def done(result):
        seen["result"] = result
        seen["thread"] = QThread.currentThread()

    run_in_thread(work, done)
    assert _pump_until(lambda: "result" in seen)
    assert seen["result"] == 42
    assert seen["thread"] is gui_thread


def test_run_in_thread_routes_exception_to_on_error(qapp):
    seen = {}

    def work():
        raise ValueError("boom")

    run_in_thread(
        work, lambda _r: seen.__setitem__("done", True), lambda e: seen.__setitem__("err", e)
    )
    assert _pump_until(lambda: "err" in seen)
    assert isinstance(seen["err"], ValueError)
    assert "done" not in seen


def test_run_in_thread_skips_callback_for_deleted_owner(qapp):
    owner = QWidget()
    seen = []
    gate = threading.Event()

    def work():
        gate.wait(2)
        return "late"

    run_in_thread(work, lambda r: seen.append(r), owner=owner)
    # Destroy the owner while the worker is still running.
    from PyQt6 import sip

    sip.delete(owner)
    gate.set()
    _pump_until(lambda: False, timeout_s=0.3)
    assert seen == []


def test_call_on_gui_runs_inline_on_gui_thread(qapp):
    seen = []
    call_on_gui(lambda: seen.append(1))
    assert seen == [1]  # no event-loop round trip needed
