"""Async helpers for safe task management in qasync.

qasync bridges asyncio with Qt but only handles one active task well.
These utilities prevent silent exception swallowing and blocking I/O
on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


def safe_task(
    coro: Coroutine,
    *,
    name: str = "unnamed",
    on_error: Callable[[Exception], Any] | None = None,
) -> asyncio.Task:
    """Create an asyncio task with error logging.

    Unlike bare ``asyncio.ensure_future``, exceptions are always logged
    and optionally forwarded to *on_error* instead of being silently
    swallowed by the event loop.
    """
    task = asyncio.ensure_future(coro)
    task.set_name(name)

    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("Async task %r failed: %s", name, exc, exc_info=exc)
            if on_error:
                try:
                    on_error(exc)
                except Exception:
                    logger.exception("on_error callback failed for task %r", name)

    task.add_done_callback(_done)
    return task


async def run_blocking(fn: Callable, *args: Any) -> Any:
    """Run a blocking function in the default thread-pool executor.

    Prevents synchronous I/O (file reads, keyring access, etc.) from
    blocking the asyncio/Qt event loop.

    Defensive against the qasync edge case where ``get_running_loop``
    raises ``RuntimeError`` from inside a Qt-timer-driven coroutine —
    same bug that hit the Arduino panel's board detector and the
    dependency installer dialog. When the standard async path fails,
    we drop to a real ``threading.Thread`` and pump the GUI's event
    loop while we wait so the user doesn't see a frozen window.
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)
    except RuntimeError:
        # No running loop visible to asyncio. Run on a real thread
        # and wait for completion while keeping Qt responsive — the
        # alternative is either crashing (current behaviour) or
        # blocking the GUI thread for the duration of the call.
        return _run_in_thread_pumping_qt(fn, *args)


def _run_in_thread_pumping_qt(fn: Callable, *args: Any) -> Any:
    """Spawn a thread and pump Qt events until it finishes.

    Used as a fallback when no asyncio loop is reachable. The
    ``processEvents`` poll keeps the GUI alive (timers fire,
    redraws happen) without an event loop driving us. 50 ms is a
    reasonable poll cadence — fast enough that the UI feels live,
    slow enough that we're not burning a CPU core.
    """
    import threading

    from PyQt6.QtCore import QCoreApplication, QEventLoop

    result: dict[str, Any] = {}

    def _target() -> None:
        try:
            result["value"] = fn(*args)
        except BaseException as exc:  # capture and re-raise on caller thread
            result["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    while thread.is_alive():
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        else:
            # Headless / test path — just wait briefly.
            thread.join(timeout=0.05)

    if "error" in result:
        raise result["error"]
    return result.get("value")
