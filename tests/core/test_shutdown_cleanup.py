"""Shutdown-teardown hygiene.

Two shutdown-time noise sources, both reproduced from a real session log:

1. ``cancel_pending_tasks`` — a background task still mid-await when the
   loop is torn down logs "Task was destroyed but it is pending!" and skips
   its finally block. We cancel and drain stragglers first.

2. MCPClient routes subprocess stderr to a log file (not the launching
   terminal), so a child's shutdown BrokenPipeError traceback doesn't spam
   the shell.
"""

from __future__ import annotations

import asyncio

from polyglot_ai.app import cancel_pending_tasks, close_databases


def test_cancel_pending_tasks_drains_stragglers():
    loop = asyncio.new_event_loop()
    try:
        finally_ran = {"v": False}

        async def straggler():
            try:
                await asyncio.sleep(3600)  # would outlive the app
            finally:
                finally_ran["v"] = True

        # Schedule the task and let it start running (reach its await).
        task = loop.create_task(straggler())
        loop.run_until_complete(asyncio.sleep(0))
        assert not task.done()

        n = cancel_pending_tasks(loop)

        assert n >= 1
        assert task.cancelled()
        assert finally_ran["v"] is True, "task's finally must run on cancel"
    finally:
        loop.close()


def test_cancel_pending_tasks_noop_when_idle():
    loop = asyncio.new_event_loop()
    try:
        assert cancel_pending_tasks(loop) == 0
    finally:
        loop.close()


class _Manager:
    def __init__(self, hang: bool = False) -> None:
        self.hang = hang
        self.disconnected = False

    async def disconnect_all(self) -> None:
        if self.hang:
            await asyncio.sleep(3600)  # a query that never comes back
        self.disconnected = True


def test_close_databases_disconnects_and_then_sweeps_tunnels(monkeypatch):
    """Quitting used to leave SSH / Session Manager tunnels running: the
    shutdown sequence disconnected MCP servers but never the databases."""
    from polyglot_ai.core import db_explorer, db_tunnel

    manager = _Manager()
    swept = []
    monkeypatch.setattr(db_explorer, "_global_db_manager", manager)
    monkeypatch.setattr(db_tunnel, "stop_all", lambda: swept.append(True) or 0)
    loop = asyncio.new_event_loop()
    try:
        close_databases(loop)
    finally:
        loop.close()
    assert manager.disconnected and swept == [True]


def test_close_databases_gives_up_on_a_stuck_disconnect_but_still_stops_tunnels(monkeypatch):
    import time

    from polyglot_ai.core import db_explorer, db_tunnel

    manager = _Manager(hang=True)
    swept = []
    monkeypatch.setattr(db_explorer, "_global_db_manager", manager)
    monkeypatch.setattr(db_tunnel, "stop_all", lambda: swept.append(True) or 1)
    loop = asyncio.new_event_loop()
    try:
        start = time.monotonic()
        close_databases(loop, timeout=0.2)  # must not raise
        assert time.monotonic() - start < 2
    finally:
        loop.close()
    assert not manager.disconnected and swept == [True]


def test_close_databases_is_quiet_when_the_panel_was_never_used(monkeypatch):
    from polyglot_ai.core import db_explorer

    monkeypatch.setattr(db_explorer, "_global_db_manager", None)
    loop = asyncio.new_event_loop()
    try:
        close_databases(loop)
    finally:
        loop.close()


def test_shutdown_sequence_closes_databases_while_the_loop_is_usable():
    """Pin the wiring: after the event loop stops, before it is closed."""
    import inspect

    import polyglot_ai.app as app

    source = inspect.getsource(app)
    stopped = source.index("loop.run_forever()")
    assert stopped < source.index("close_databases(loop)", stopped) < source.index("lock.unlock()")


def test_mcp_stderr_routed_to_log_file(tmp_path, monkeypatch):
    from polyglot_ai.core import mcp_client as mc
    import polyglot_ai.constants as constants

    # Point LOG_DIR at a temp dir so we don't touch the real log.
    monkeypatch.setattr(constants, "LOG_DIR", tmp_path)

    client = mc.MCPClient()
    fh = client._mcp_stderr_log()
    # A real, writable file under LOG_DIR — not the process stderr.
    import sys

    assert fh is not sys.stderr
    assert (tmp_path / "mcp-servers.log").exists()
    # Second call returns the same handle (opened once).
    assert client._mcp_stderr_log() is fh
    fh.write("diagnostic line\n")
    fh.flush()
    assert "diagnostic line" in (tmp_path / "mcp-servers.log").read_text()


async def test_disconnect_all_closes_stderr_log(tmp_path, monkeypatch):
    from polyglot_ai.core import mcp_client as mc
    import polyglot_ai.constants as constants

    monkeypatch.setattr(constants, "LOG_DIR", tmp_path)
    client = mc.MCPClient()
    fh = client._mcp_stderr_log()
    assert not fh.closed
    await client.disconnect_all()
    assert fh.closed
    assert client._stderr_log is None
