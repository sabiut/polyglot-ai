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
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)
