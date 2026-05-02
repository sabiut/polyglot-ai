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

    Defensive against the qasync edge case where
    ``asyncio.get_running_loop`` raises ``RuntimeError`` from inside
    a Qt-timer-driven coroutine — same bug that hit the Arduino
    panel's board detector and the dependency installer dialog.

    Two-step fallback:

    1. Try ``get_running_loop`` — the standard, fast path.
    2. Fall back to ``get_event_loop_policy().get_event_loop()``,
       which is more lenient about the loop's "running" state.
       qasync registers its loop with the policy even when
       ``get_running_loop`` doesn't see it, so this catches the
       qasync edge case without re-entering Qt's event loop.

    If both fail, run synchronously and accept a brief GUI freeze.
    The earlier "thread + processEvents pump" approach was nixed
    because pumping events from inside a coroutine triggered
    "Cannot enter into task" errors from qasync when a Qt timer
    fired and tried to schedule another asyncio task during the
    pump.
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)
    except RuntimeError:
        pass

    # qasync-friendly fallback: the policy still knows the loop
    # even when asyncio doesn't consider it "running".
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop is not None and not loop.is_closed():
            return await loop.run_in_executor(None, fn, *args)
    except (RuntimeError, DeprecationWarning):
        pass

    # No usable loop. Run synchronously — GUI freezes for the
    # duration, but for the call sites that hit this branch
    # (pyserial scan ~1 ms, atomic file copy ~10 ms) the freeze
    # is invisible. Slow operations like the system installer
    # should never reach this fallback in practice because the
    # qasync loop IS registered with the policy.
    logger.debug("run_blocking: no loop available, running synchronously")
    return fn(*args)
