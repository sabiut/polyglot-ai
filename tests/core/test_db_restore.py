"""Loading a backup: what a file is, whether it fits, and how it is run."""

from __future__ import annotations

import asyncio
import gzip
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from polyglot_ai.core import db_restore
from polyglot_ai.core.db_backup import (
    BackupCancelled,
    BackupError,
    BackupSource,
    NativeTool,
    run_backup,
)
from polyglot_ai.core.db_dialects import DbTarget
from polyglot_ai.core.db_restore import (
    BackupFile,
    RestorePlan,
    _check_complete,
    _MssqlRunner,
    _MySQLRunner,
    _OracleRunner,
    _PostgresRunner,
    _run_or_interrupt,
    inspect_file,
    plan_restore,
    restore_command,
    run_restore,
    split_statements,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX scripts")

EXPORT = """-- Polyglot AI built-in export
-- Connection: orders ({engine})  app@db.example.com:5432/shop
-- Taken:      2026-11-12 22:04:54 +1100
--
-- Contains every table and its rows.

BEGIN;
CREATE TABLE "t" (a int);
INSERT INTO "t" (a) VALUES (1);
COMMIT;
-- End of export: 1 table, 1 row
"""


def _export(tmp_path: Path, engine="PostgreSQL", name="out.sql", text=None) -> Path:
    path = tmp_path / name
    content = (text if text is not None else EXPORT).replace("{engine}", engine)
    if name.endswith(".gz"):
        path.write_bytes(gzip.compress(content.encode()))
    else:
        path.write_text(content)
    return path


def _sqlite(path: Path, names=("a", "b")) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE items (name TEXT)")
    connection.executemany("INSERT INTO items VALUES (?)", [(n,) for n in names])
    connection.commit()
    connection.close()
    return path


def _names(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [r[0] for r in connection.execute("SELECT name FROM items ORDER BY rowid")]
    finally:
        connection.close()


@pytest.fixture
def no_tools(monkeypatch, tmp_path):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _fake_tool(directory: Path, name: str, script: str) -> None:
    directory.mkdir(exist_ok=True)
    tool = directory / name
    tool.write_text("#!/bin/sh\n" + script)
    tool.chmod(0o755)


# ── what a file is ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("engine", "db_type"),
    [
        ("PostgreSQL", "postgresql"),
        ("MySQL / MariaDB", "mysql"),
        ("SQL Server", "mssql"),
        ("Oracle", "oracle"),
    ],
)
def test_a_built_in_export_says_what_it_was_made_from(tmp_path, engine, db_type):
    file = inspect_file(_export(tmp_path, engine))
    assert (file.kind, file.engine, file.compressed) == ("builtin", db_type, False)
    assert file.taken == "2026-11-12 22:04:54 +1100"
    assert file.connection.startswith("orders (")
    assert "taken 2026-11-12" in file.description and file.size > 0


def test_compression_is_read_from_the_bytes_not_the_name(tmp_path):
    packed = _export(tmp_path, name="out.sql.gz")
    renamed = packed.rename(tmp_path / "backup.sql")
    file = inspect_file(renamed)
    assert (file.kind, file.engine, file.compressed) == ("builtin", "postgresql", True)


def test_sqlite_and_pg_custom_files_are_known_by_their_first_bytes(tmp_path):
    assert inspect_file(_sqlite(tmp_path / "copy.anything")).kind == "sqlite"
    archive = tmp_path / "out.dump"
    archive.write_bytes(b"PGDMP\x01\x0e\x00" + b"\0" * 64)
    file = inspect_file(archive)
    assert (file.kind, file.engine) == ("pg_custom", "postgresql")


@pytest.mark.parametrize(
    ("first_lines", "engine", "description"),
    [
        ("--\n-- PostgreSQL database dump\n--\n", "postgresql", "PostgreSQL dump (SQL)"),
        ("-- MySQL dump 10.13  Distrib 8.0.39\n", "mysql", "MySQL dump (SQL)"),
        ("/*M!999999 enable the sandbox mode */\n-- MariaDB dump 10.19\n", "mysql", None),
        ("CREATE TABLE t (a int);\n", "", "SQL script"),
    ],
)
def test_vendor_dumps_are_recognised_by_their_header(tmp_path, first_lines, engine, description):
    path = tmp_path / "dump.sql"
    path.write_text(first_lines + "SELECT 1;\n")
    file = inspect_file(path)
    assert (file.kind, file.engine) == ("sql", engine)
    if description:
        assert file.description == description


def test_missing_and_broken_files_are_explained(tmp_path):
    with pytest.raises(BackupError, match="no file"):
        inspect_file(tmp_path / "nope.sql")
    broken = tmp_path / "broken.sql.gz"
    broken.write_bytes(b"\x1f\x8b" + b"not really gzip")
    with pytest.raises(BackupError, match="can't be read"):
        inspect_file(broken)


# ── whether it fits ─────────────────────────────────────────────────


def test_a_built_in_export_loads_through_the_driver(tmp_path):
    file = inspect_file(_export(tmp_path))
    plan = plan_restore(file, "postgresql")
    assert (plan.how, plan.atomic) == ("driver", True)
    assert "nothing is changed" in plan.summary
    mssql = plan_restore(inspect_file(_export(tmp_path, "SQL Server")), "mssql")
    assert (mssql.how, mssql.atomic) == ("driver", False)
    assert "the ones before it stay" in mssql.summary


def test_another_engines_backup_is_refused(tmp_path):
    plan = plan_restore(inspect_file(_export(tmp_path)), "mssql")
    assert plan.how == ""
    assert plan.problem == (
        "This backup was made from PostgreSQL; its SQL won't load into SQL Server."
    )


def test_sqlite_only_takes_a_sqlite_file_and_the_other_way_round(tmp_path):
    export = inspect_file(_export(tmp_path))
    copy = inspect_file(_sqlite(tmp_path / "copy.db"))
    assert "restored from a SQLite database file" in plan_restore(export, "sqlite").problem
    assert "can't go into PostgreSQL" in plan_restore(copy, "postgresql").problem
    plan = plan_restore(copy, "sqlite")
    assert (plan.how, plan.atomic) == ("sqlite", True)
    assert "saved next to it" in plan.summary


def test_a_vendor_dump_needs_the_vendor_tool(tmp_path, no_tools):
    path = tmp_path / "dump.sql"
    path.write_text("-- PostgreSQL database dump\n")
    plan = plan_restore(inspect_file(path), "postgresql")
    assert plan.how == "" and "psql" in plan.problem and "postgresql-client" in plan.problem


def test_a_vendor_dump_uses_the_tool_when_installed(tmp_path, monkeypatch):
    _fake_tool(tmp_path / "bin", "psql", 'echo "psql (PostgreSQL) 16.4"\n')
    _fake_tool(tmp_path / "bin", "pg_restore", 'echo "pg_restore (PostgreSQL) 16.4"\n')
    _fake_tool(tmp_path / "bin", "mariadb", 'echo "mariadb from 11.8.3-MariaDB, client 15.2"\n')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    path = tmp_path / "dump.sql"
    path.write_text("-- PostgreSQL database dump\n")
    plan = plan_restore(inspect_file(path), "postgresql")
    assert (plan.how, plan.tool.name, plan.atomic) == ("native", "psql", True)

    archive = tmp_path / "out.dump"
    archive.write_bytes(b"PGDMP\x01\x0e\x00")
    assert plan_restore(inspect_file(archive), "postgresql").tool.name == "pg_restore"

    path.write_text("-- MySQL dump 10.13\n")
    mysql = plan_restore(inspect_file(path), "mysql")
    assert (mysql.how, mysql.tool.name, mysql.atomic) == ("native", "mariadb", False)


def test_a_plain_script_runs_in_batches_on_sql_server_and_nowhere_else(tmp_path):
    path = tmp_path / "script.sql"
    path.write_text("CREATE TABLE t (a int)\nGO\n")
    file = inspect_file(path)
    assert plan_restore(file, "mssql").how == "driver"
    oracle = plan_restore(file, "oracle")
    assert oracle.how == "" and "built-in export" in oracle.problem


# ── cutting a script into statements ────────────────────────────────


def _split(text, **options):
    return list(split_statements(text.splitlines(keepends=True), **options))


def test_statements_end_at_semicolons_outside_strings_names_and_comments():
    statements = _split(
        "-- a comment; with a semicolon\n"
        "INSERT INTO t VALUES ('a;b', 'it''s; fine');\n"
        'CREATE TABLE "odd;name" (a int); /* block; comment */ SELECT `x;y`;\n'
        "SELECT 1 -- trailing; comment\n"
        ";\n"
    )
    assert statements == [
        "INSERT INTO t VALUES ('a;b', 'it''s; fine')",
        'CREATE TABLE "odd;name" (a int)',
        "SELECT `x;y`",
        "SELECT 1",
    ]


def test_a_string_can_run_over_several_lines():
    statements = _split(
        "INSERT INTO t VALUES ('line one;\n-- not a comment\nline three');\nSELECT 2;"
    )
    assert statements == [
        "INSERT INTO t VALUES ('line one;\n-- not a comment\nline three')",
        "SELECT 2",
    ]


def test_block_comments_can_run_over_several_lines_and_are_dropped():
    assert _split("SELECT /* one;\n two; */ 1;\n") == ["SELECT   1"]


def test_backslash_escapes_only_where_the_engine_has_them():
    line = "INSERT INTO t VALUES ('it\\'s; here');\nSELECT 2;\n"
    assert _split(line, backslash_escapes=True) == [
        "INSERT INTO t VALUES ('it\\'s; here')",
        "SELECT 2",
    ]
    # Without them the backslash is an ordinary character and the string ends at the quote.
    assert _split("INSERT INTO t VALUES ('a\\');\nSELECT 2;\n") == [
        "INSERT INTO t VALUES ('a\\')",
        "SELECT 2",
    ]


def test_comment_only_text_gives_no_statement():
    assert _split("-- nothing\n/* at all */\n\n;\n") == []


def test_go_batches_keep_their_semicolons_and_ignore_go_inside_strings():
    statements = _split(
        "SET QUOTED_IDENTIFIER ON;\nGO\n"
        "CREATE TABLE [dbo].[odd]]name] (a int);\n"
        "INSERT INTO t VALUES (N'first\nGO\nlast');\n"
        "go\n"
        "SELECT 1;\n  GO ;\n",
        batch_separator="GO",
    )
    assert statements == [
        "SET QUOTED_IDENTIFIER ON;",
        "CREATE TABLE [dbo].[odd]]name] (a int);\nINSERT INTO t VALUES (N'first\nGO\nlast');",
        "SELECT 1;",
    ]


def test_brackets_are_only_names_in_batch_mode():
    # PostgreSQL array syntax must not be read as a quoted name.
    assert _split("SELECT a[1]; SELECT b[2];\n") == ["SELECT a[1]", "SELECT b[2]"]


def test_the_last_statement_needs_no_terminator():
    assert _split("SELECT 1;\nSELECT 2") == ["SELECT 1", "SELECT 2"]


# ── completeness ────────────────────────────────────────────────────


def test_an_export_cut_short_is_refused(tmp_path):
    cut = EXPORT.rsplit("-- End of export", 1)[0]
    file = inspect_file(_export(tmp_path, text=cut))
    with pytest.raises(BackupError, match="incomplete"):
        _check_complete(file, None)
    _check_complete(inspect_file(_export(tmp_path, name="whole.sql")), None)
    _check_complete(inspect_file(_export(tmp_path, name="whole.sql.gz")), None)


def test_run_restore_refuses_a_cut_export_before_connecting(tmp_path):
    cut = EXPORT.rsplit("-- End of export", 1)[0]
    path = _export(tmp_path, text=cut)
    stages = []
    source = BackupSource("pg", "postgresql", "postgresql://a:b@127.0.0.1:1/d")
    with pytest.raises(BackupError, match="incomplete"):
        run_restore(source, path, progress=lambda u: stages.append(u.stage))
    assert stages == ["checking"]


def test_run_restore_gives_the_plans_problem(tmp_path):
    source = BackupSource("ms", "mssql", "mssql://a:b@127.0.0.1:1/d")
    with pytest.raises(BackupError, match="made from PostgreSQL"):
        run_restore(source, _export(tmp_path))


# ── the driver runners ──────────────────────────────────────────────


class _Cursor:
    def __init__(self, log, sets=0, fail_on_set=None):
        self._log, self._sets, self._fail = log, sets, fail_on_set
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def execute(self, sql):
        self._log.append(sql)

    def nextset(self):
        if self._sets == self._fail:
            raise RuntimeError("Violation of PRIMARY KEY constraint")
        self._sets -= 1
        return self._sets >= 0

    def close(self):
        self.closed = True


def _connection_with(driver_connection):
    class Driver:
        _conn = driver_connection

    class Connection:
        _driver = Driver()

    return Connection()


def test_mssql_runner_reads_every_result_so_a_late_error_surfaces():
    log = []
    cursor = _Cursor(log, sets=3, fail_on_set=1)

    class Raw:
        def cursor(self):
            return cursor

    runner = _MssqlRunner(_connection_with(Raw()))
    asyncio.run(runner.open())
    with pytest.raises(RuntimeError, match="PRIMARY KEY"):
        asyncio.run(runner.run("INSERT …; INSERT …; INSERT …"))
    assert cursor.closed


def test_oracle_runner_skips_sqlplus_settings_and_commits_explicitly():
    log = []

    class Raw:
        autocommit = True
        commits = rollbacks = 0

        def cursor(self):
            return _Cursor(log)

        def commit(self):
            Raw.commits += 1

        def rollback(self):
            Raw.rollbacks += 1

    raw = Raw()
    runner = _OracleRunner(_connection_with(raw))

    async def go():
        await runner.open()
        assert raw.autocommit is False
        for statement in (
            "SET DEFINE OFF",
            "set sqlblanklines on",
            "CREATE TABLE t (a int)",
            "COMMIT",
        ):
            await runner.run(statement)
        await runner.failed()
        await runner.close()

    asyncio.run(go())
    assert log == ["CREATE TABLE t (a int)"]
    assert (Raw.commits, Raw.rollbacks, raw.autocommit) == (1, 1, True)


class _RecordingRunner:
    """Stands in for an engine: records statements, can fail on the nth."""

    made: list = []
    fail_at = 0
    block_at = 0  # this statement never returns by itself …
    stops = True  # … and this is what interrupting it reports

    def __init__(self, connection):
        self.ran, self.events = [], []
        type(self).made.append(self)

    async def open(self):
        self.events.append("open")

    async def run(self, statement):
        if len(self.ran) + 1 == type(self).fail_at:
            raise RuntimeError("relation already exists")
        if len(self.ran) + 1 == type(self).block_at:
            await asyncio.Event().wait()  # a lock nobody releases
        self.ran.append(statement)

    async def interrupt(self, work):
        self.events.append("interrupt")
        work.cancel()
        await asyncio.wait({work})
        return type(self).stops

    async def failed(self):
        self.events.append("failed")

    async def close(self):
        self.events.append("close")


@pytest.fixture
def scripted_engine(monkeypatch):
    """run_restore with the connection and the engine's runner replaced."""
    from polyglot_ai.core import db_explorer

    class Connection:
        _pg_pool = _mysql_pool = _driver = object()
        instances: list = []

        def __init__(self, name, db_type, connection_string, read_only=True, options=None):
            self.read_only = read_only
            self.disconnected = False
            Connection.instances.append(self)

        async def connect(self):
            return True, "ok"

        async def disconnect(self):
            self.disconnected = True

    _RecordingRunner.made, _RecordingRunner.fail_at = [], 0
    _RecordingRunner.block_at, _RecordingRunner.stops = 0, True
    monkeypatch.setattr(db_explorer, "DatabaseConnection", Connection)
    for db_type in ("postgresql", "mysql", "mssql", "oracle"):
        monkeypatch.setitem(db_restore._RUNNERS, db_type, _RecordingRunner)
    return Connection


def test_driver_restore_runs_each_statement_in_order(tmp_path, scripted_engine):
    source = BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d")
    result = run_restore(source, _export(tmp_path, name="out.sql.gz"))
    (runner,) = _RecordingRunner.made
    assert runner.ran == [
        "BEGIN",
        'CREATE TABLE "t" (a int)',
        'INSERT INTO "t" (a) VALUES (1)',
        "COMMIT",
    ]
    assert runner.events == ["open", "close"]
    assert (result.how, result.statements) == ("driver", 4)
    (connection,) = scripted_engine.instances
    # Its own connection, writable whatever the saved one is, and closed after.
    assert connection.read_only is False and connection.disconnected


def test_a_failing_statement_rolls_back_an_atomic_load(tmp_path, scripted_engine):
    _RecordingRunner.fail_at = 3
    source = BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d")
    with pytest.raises(BackupError) as caught:
        run_restore(source, _export(tmp_path))
    message = str(caught.value)
    assert message.startswith("Statement 3 failed: ")
    assert "relation already exists" in message
    assert '[INSERT INTO "t" (a) VALUES (1)]' in message
    assert message.endswith("Nothing was changed.")
    assert _RecordingRunner.made[0].events == ["open", "failed", "close"]
    assert scripted_engine.instances[0].disconnected


def test_a_failing_statement_elsewhere_says_what_was_already_applied(tmp_path, scripted_engine):
    _RecordingRunner.fail_at = 3
    source = BackupSource("ms", "mssql", "mssql://a:b@h:1433/d")
    text = EXPORT.replace("BEGIN;\n", "SET NOCOUNT ON;\nGO\n").replace(
        "COMMIT;\n", "GO\nSELECT 1;\nGO\nSELECT 2;\nGO\n"
    )
    with pytest.raises(BackupError, match="The 2 statements before it were applied"):
        run_restore(source, _export(tmp_path, "SQL Server", text=text))


def test_cancelling_a_driver_restore_reports_what_was_applied(tmp_path, scripted_engine):
    cancel = threading.Event()
    original = _RecordingRunner.run

    async def run_then_cancel(self, statement):
        await original(self, statement)
        cancel.set()

    _RecordingRunner.run = run_then_cancel
    try:
        with pytest.raises(BackupCancelled, match="after 1 statement; that was applied"):
            run_restore(
                BackupSource("my", "mysql", "mysql://a:b@h:3306/d"),
                _export(tmp_path, "MySQL / MariaDB"),
                cancel=cancel,
            )
        cancel.clear()
        _RecordingRunner.made.clear()
        with pytest.raises(BackupCancelled) as atomic:
            run_restore(
                BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d"),
                _export(tmp_path),
                cancel=cancel,
            )
        assert str(atomic.value) == "Restore cancelled. Nothing was changed."
        assert _RecordingRunner.made[0].events == ["open", "failed", "close"]
    finally:
        _RecordingRunner.run = original


# ── Cancel while a statement is running ─────────────────────────────


def _timed(source, path, after=0.3):
    """run_restore with Cancel pressed ``after`` seconds in → (message, seconds past Cancel)."""
    started = time.monotonic()
    with pytest.raises(BackupCancelled) as stopped:
        run_restore(source, path, cancel=_cancel_after(after))
    return str(stopped.value), time.monotonic() - started - after


def test_cancel_reaches_a_statement_that_is_waiting_for_a_lock(tmp_path, scripted_engine):
    """Cancel was only looked at between statements: this one held the restore for good."""
    _RecordingRunner.block_at = 3
    message, late = _timed(
        BackupSource("my", "mysql", "mysql://a:b@h:3306/d"), _export(tmp_path, "MySQL / MariaDB")
    )
    assert message == "Restore cancelled after 2 statements; those were applied."
    assert late < 2
    (runner,) = _RecordingRunner.made
    assert runner.events == ["open", "interrupt", "failed", "close"]
    assert scripted_engine.instances[0].disconnected


def test_cancel_of_a_running_statement_in_a_transaction_changes_nothing(tmp_path, scripted_engine):
    _RecordingRunner.block_at = 2
    message, late = _timed(
        BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d"), _export(tmp_path)
    )
    assert message == "Restore cancelled. Nothing was changed."
    assert late < 2


def test_a_statement_that_would_not_stop_is_reported_as_possibly_completing(
    tmp_path, scripted_engine
):
    _RecordingRunner.block_at, _RecordingRunner.stops = 1, False
    message, _late = _timed(
        BackupSource("ora", "oracle", "oracle://a:b@h:1521/d"), _export(tmp_path, "Oracle")
    )
    assert message.startswith("Restore cancelled before any statement was applied.")
    assert "hadn't stopped after 10 seconds and may still complete." in message


def test_a_statement_that_finishes_is_not_disturbed_by_the_watcher(tmp_path, scripted_engine):
    result = run_restore(
        BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d"),
        _export(tmp_path),
        cancel=threading.Event(),  # never pressed
    )
    assert result.statements == 4
    assert _RecordingRunner.made[0].events == ["open", "close"]


def test_a_statement_that_ends_while_being_stopped_is_counted(tmp_path, scripted_engine):
    async def too_late(self, work):
        await asyncio.wait({work})  # the lock was released just then
        return True

    release = []
    original_run, original_interrupt = _RecordingRunner.run, _RecordingRunner.interrupt

    async def run(self, statement):
        if len(self.ran) == 1:
            gate = asyncio.Event()
            release.append(gate)
            asyncio.get_running_loop().call_later(0.5, gate.set)
            await gate.wait()
        self.ran.append(statement)

    _RecordingRunner.run, _RecordingRunner.interrupt = run, too_late
    try:
        message, _late = _timed(
            BackupSource("my", "mysql", "mysql://a:b@h:3306/d"),
            _export(tmp_path, "MySQL / MariaDB"),
        )
    finally:
        _RecordingRunner.run, _RecordingRunner.interrupt = original_run, original_interrupt
    assert message == "Restore cancelled after 2 statements; those were applied."
    assert len(_RecordingRunner.made[0].ran) == 2


class _BlockingRaw:
    """A synchronous driver connection whose ``block_on`` statement waits for a cancel."""

    def __init__(self, block_on="", honours_cancel=True):
        self.log, self.calls = [], []
        self.autocommit = True
        self._block_on, self._honours = block_on, honours_cancel
        self.released = threading.Event()
        self.thread_names = set()
        outer = self

        class Low:  # pymssql: connection._conn.cancel()
            def cancel(self):
                outer.cancel()

        self._conn = Low()

    def cancel(self):
        self.calls.append("cancel")
        if self._honours:
            self.released.set()

    def cursor(self):
        outer = self

        class Cursor(_Cursor):
            def execute(self, sql):
                outer.thread_names.add(threading.current_thread().name)
                if sql == outer._block_on:
                    outer.released.wait(30)
                    raise RuntimeError("Operation cancelled by user")
                outer.log.append(sql)

        return Cursor(self.log)

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


async def _interrupt_after(runner, statement, seconds=0.2):
    """Run ``statement`` on ``runner`` and press Cancel while it is running."""
    cancelled = asyncio.ensure_future(asyncio.sleep(seconds))
    started = time.monotonic()
    with pytest.raises(BackupCancelled) as stopped:
        await _run_or_interrupt(runner, statement, cancelled)
    return stopped.value.stopped, time.monotonic() - started - seconds


@pytest.mark.parametrize("runner_type", [_MssqlRunner, _OracleRunner])
def test_a_synchronous_driver_is_told_to_cancel_from_another_thread(runner_type):
    raw = _BlockingRaw(block_on="INSERT INTO t SELECT * FROM locked")
    runner = runner_type(_connection_with(raw))

    async def go():
        await runner.open()
        await runner.run("CREATE TABLE t (a int)")
        stopped, late = await _interrupt_after(runner, "INSERT INTO t SELECT * FROM locked")
        # The connection is still the runner's: it undoes and closes in order.
        await runner.failed()
        await runner.close()
        return stopped, late

    stopped, late = asyncio.run(go())
    assert stopped is True and late < 2
    assert raw.log == ["CREATE TABLE t (a int)"]
    assert raw.calls[0] == "cancel"
    # The driver was never called on the restore's own thread.
    assert raw.thread_names == {"db-restore-driver"}
    if runner_type is _OracleRunner:
        assert raw.calls == ["cancel", "rollback"] and raw.autocommit is True


def test_a_synchronous_driver_that_ignores_cancel_is_left_to_finish_by_itself(monkeypatch):
    monkeypatch.setattr(db_restore, "INTERRUPT_GRACE_SECONDS", 0.3)
    raw = _BlockingRaw(block_on="UPDATE big SET x = 1", honours_cancel=False)
    connection = _connection_with(raw)
    driver = connection._driver
    closed = threading.Event()
    driver.close = lambda: (raw.calls.append("driver.close"), closed.set())
    runner = _OracleRunner(connection)

    async def go():
        await runner.open()
        stopped, late = await _interrupt_after(runner, "UPDATE big SET x = 1")
        # Neither of these may touch a connection that is inside a statement.
        await runner.failed()
        await runner.close()
        return stopped, late

    stopped, late = asyncio.run(go())
    assert stopped is False and 0.2 < late < 2
    # Taken away, so disconnect() doesn't close it under the running statement.
    assert connection._driver is None
    assert raw.calls == ["cancel"]

    raw.released.set()  # the server answers at last
    assert closed.wait(5)
    assert raw.calls == ["cancel", "rollback", "driver.close"] and raw.autocommit is True


def test_mysql_kills_the_query_from_a_second_login(monkeypatch):
    from polyglot_ai.core import db_explorer

    killed = asyncio.Event()
    seen = []

    class Cursor:
        def __init__(self, blocking):
            self._blocking = blocking

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            seen.append(sql)
            if sql.startswith("KILL"):
                killed.set()
            elif self._blocking:
                await killed.wait()
                raise RuntimeError("(1317, 'Query execution was interrupted')")

    class First:
        closed = False

        def thread_id(self):
            return 77

        async def ping(self, reconnect=True): ...

        def cursor(self):
            return Cursor(blocking=True)

        def close(self):
            First.closed = True

    class Pool:
        _connection = First()

    class Saved:
        name, db_type, options = "shop", "mysql", {"ssl": "require"}
        _connection_string = "mysql://a:b@h:3306/d"
        _mysql_pool = Pool()

    class Second:
        made: list = []

        def __init__(self, name, db_type, connection_string, read_only=True, options=None):
            self.args = (name, db_type, connection_string, read_only, options)
            self.disconnected = False
            Second.made.append(self)

        async def connect(self):
            return True, "ok"

        async def disconnect(self):
            self.disconnected = True

        @property
        def _mysql_pool(self):
            class Acquire:
                async def __aenter__(self):
                    class Conn:
                        def cursor(self):
                            return Cursor(blocking=False)

                    return Conn()

                async def __aexit__(self, *exc):
                    return False

            class OtherPool:
                def acquire(self):
                    return Acquire()

            return OtherPool()

    monkeypatch.setattr(db_explorer, "DatabaseConnection", Second)
    runner = _MySQLRunner(Saved())

    async def go():
        await runner.open()
        return await _interrupt_after(runner, "ALTER TABLE t ADD c int")

    stopped, late = asyncio.run(go())
    assert stopped is True and late < 2
    assert seen == ["ALTER TABLE t ADD c int", "KILL QUERY 77"]
    (second,) = Second.made
    assert second.args == ("shop (cancel)", "mysql", "mysql://a:b@h:3306/d", False, Saved.options)
    assert second.disconnected and First.closed is False


def test_mysql_hangs_up_when_the_kill_cannot_be_sent(monkeypatch):
    from polyglot_ai.core import db_explorer

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            await asyncio.Event().wait()

    class First:
        closed = False

        def thread_id(self):
            return 77

        async def ping(self, reconnect=True): ...

        def cursor(self):
            return Cursor()

        def close(self):
            First.closed = True

    class Pool:
        _connection = First()

    class Saved:
        name, db_type, options = "shop", "mysql", {}
        _connection_string = "mysql://a:b@h:3306/d"
        _mysql_pool = Pool()

    class Unreachable:
        disconnected = False

        def __init__(self, *args, **kwargs): ...

        async def connect(self):
            return False, "Too many connections"

        async def disconnect(self):
            Unreachable.disconnected = True

    monkeypatch.setattr(db_explorer, "DatabaseConnection", Unreachable)
    runner = _MySQLRunner(Saved())

    async def go():
        await runner.open()
        return await _interrupt_after(runner, "ALTER TABLE t ADD c int")

    stopped, late = asyncio.run(go())
    assert stopped is False and late < 2
    assert First.closed and Unreachable.disconnected


class _Pg:
    """asyncpg's connection, as far as the runner uses it."""

    def __init__(self, rollback_answers=True):
        self.log, self.terminated = [], False
        self._rollback_answers = rollback_answers

    async def execute(self, sql):
        self.log.append(sql)
        if sql == "ROLLBACK" and self._rollback_answers:
            return
        await asyncio.Event().wait()

    def terminate(self):
        self.terminated = True

    def is_closed(self):
        return self.terminated


def _pg_runner(pg):
    class Pool:
        released = []

        async def acquire(self):
            return pg

        async def release(self, conn):
            Pool.released.append(conn)

    class Saved:
        _pg_pool = Pool()

    return _PostgresRunner(Saved()), Pool


def test_postgres_cancels_the_statement_and_rolls_back():
    pg = _Pg()
    runner, pool = _pg_runner(pg)

    async def go():
        await runner.open()
        result = await _interrupt_after(runner, "INSERT INTO t SELECT * FROM locked")
        await runner.failed()
        await runner.close()
        return result

    stopped, late = asyncio.run(go())
    assert stopped is True and late < 2
    assert pg.log == ["INSERT INTO t SELECT * FROM locked", "ROLLBACK", "ROLLBACK"]
    assert not pg.terminated and pool.released == [pg]


def test_postgres_drops_the_connection_when_the_cancel_is_not_answered(monkeypatch):
    monkeypatch.setattr(db_restore, "INTERRUPT_GRACE_SECONDS", 0.3)
    pg = _Pg(rollback_answers=False)
    runner, _pool = _pg_runner(pg)

    async def go():
        await runner.open()
        result = await _interrupt_after(runner, "INSERT INTO t SELECT * FROM locked")
        await runner.failed()  # must not wait on a connection that is gone
        await runner.close()
        return result

    stopped, late = asyncio.run(go())
    # Without a COMMIT the transaction can't survive the connection.
    assert stopped is True and pg.terminated and late < 2
    assert pg.log.count("ROLLBACK") == 1


def test_cancel_through_the_real_sql_server_runner(tmp_path, monkeypatch):
    """run_restore end to end, with only the driver's connection replaced."""
    from polyglot_ai.core import db_explorer

    text = (
        EXPORT.replace("BEGIN;\n", "SET NOCOUNT ON;\nGO\n")
        .replace(";\nINSERT", ";\nGO\nINSERT")
        .replace("COMMIT;\n", "GO\n")
    )
    raw = _BlockingRaw(block_on='INSERT INTO "t" (a) VALUES (1);')

    class Connection:
        _pg_pool = _mysql_pool = None
        disconnected = False

        def __init__(self, *args, **kwargs):
            self._driver = _connection_with(raw)._driver

        async def connect(self):
            return True, "ok"

        async def disconnect(self):
            Connection.disconnected = True

    monkeypatch.setattr(db_explorer, "DatabaseConnection", Connection)
    message, late = _timed(
        BackupSource("ms", "mssql", "mssql://a:b@h:1433/d"),
        _export(tmp_path, "SQL Server", text=text),
    )
    assert message == "Restore cancelled after 2 statements; those were applied."
    assert late < 2
    assert raw.log == ["SET NOCOUNT ON;", 'CREATE TABLE "t" (a int);']
    assert raw.calls == ["cancel"] and Connection.disconnected


# ── SQLite ──────────────────────────────────────────────────────────


def test_sqlite_restore_replaces_the_database_and_keeps_the_old_one(tmp_path):
    live = _sqlite(tmp_path / "shop.db", names=("old",))
    backup = _sqlite(tmp_path / "backup.db", names=("x", "y", "z"))
    source = BackupSource("shop", "sqlite", str(live))

    result = run_restore(source, backup)

    assert _names(live) == ["x", "y", "z"]
    assert result.how == "sqlite"
    assert result.saved_previous.name.startswith("shop.db.before-restore-")
    assert _names(result.saved_previous) == ["old"]
    assert _names(backup) == ["x", "y", "z"]  # the backup itself is untouched


def test_sqlite_restore_round_trips_a_backup_made_here(tmp_path):
    live = _sqlite(tmp_path / "shop.db", names=("one", "two"))
    source = BackupSource("shop", "sqlite", str(live))
    made = run_backup(source, tmp_path / "saved.db", method="copy").path
    connection = sqlite3.connect(live)
    connection.execute("DELETE FROM items")
    connection.commit()
    connection.close()

    run_restore(source, made)
    assert _names(live) == ["one", "two"]


def test_sqlite_restore_into_a_new_file_saves_nothing_aside(tmp_path):
    backup = _sqlite(tmp_path / "backup.db")
    result = run_restore(BackupSource("new", "sqlite", str(tmp_path / "new.db")), backup)
    assert result.saved_previous is None
    assert _names(tmp_path / "new.db") == ["a", "b"]


def test_sqlite_restore_refuses_the_databases_own_file_and_memory(tmp_path):
    live = _sqlite(tmp_path / "shop.db")
    with pytest.raises(BackupError, match="own database"):
        run_restore(BackupSource("shop", "sqlite", str(live)), live)
    with pytest.raises(BackupError, match="database file"):
        run_restore(BackupSource("mem", "sqlite", ":memory:"), live)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shop.db"]


def test_sqlite_restore_checks_the_backup_before_touching_anything(tmp_path):
    live = _sqlite(tmp_path / "shop.db", names=("keep",))
    damaged = tmp_path / "damaged.db"
    good = _sqlite(tmp_path / "good.db", names=tuple(str(n) * 50 for n in range(400))).read_bytes()
    damaged.write_bytes(good[:4096] + b"\xff" * (len(good) - 4096))
    with pytest.raises(BackupError):
        run_restore(BackupSource("shop", "sqlite", str(live)), damaged)
    assert _names(live) == ["keep"]
    assert not list(tmp_path.glob("shop.db.before-restore-*"))


# ── the vendor tools ────────────────────────────────────────────────

PSQL = NativeTool("psql", "/usr/bin/psql", "postgres", 16)
PG_RESTORE = NativeTool("pg_restore", "/usr/bin/pg_restore", "postgres", 16)
MYSQL = NativeTool("mysql", "/usr/bin/mysql", "mysql", 8)
TARGET = DbTarget(host="db.example.com", port=5432, user="app", password="s3cret", database="shop")


def test_psql_loads_in_one_transaction_and_stops_at_the_first_error():
    argv, env = restore_command(PSQL, TARGET)
    assert env["PGPASSWORD"] == "s3cret" and "s3cret" not in " ".join(argv)
    assert argv[-7:] == [
        "--dbname",
        "shop",
        "--single-transaction",
        "--set",
        "ON_ERROR_STOP=1",
        "--quiet",
        "--no-psqlrc",
    ]


def test_pg_restore_loads_as_whoever_is_connected():
    argv, _env = restore_command(PG_RESTORE, TARGET)
    for flag in ("--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges"):
        assert flag in argv


def test_mysql_restore_names_the_database_last(tmp_path):
    argv, _env = restore_command(MYSQL, TARGET, secrets_dir=tmp_path)
    assert argv[-1] == "shop" and argv[1].startswith("--defaults-extra-file=")


@pytest.mark.parametrize("tool", [PSQL, MYSQL])
def test_native_restore_needs_a_database(tool, tmp_path):
    with pytest.raises(BackupError, match="no database selected"):
        restore_command(
            tool, DbTarget(host="h", port=1, user="u", password=""), secrets_dir=tmp_path
        )


PG_SOURCE = BackupSource("orders", "postgresql", "postgresql://app:s3cret@db.example.com:5432/shop")


def _install_psql(monkeypatch, tmp_path, body: str) -> None:
    script = 'if [ "$1" = "--version" ]; then echo "psql (PostgreSQL) 16.4"; exit 0; fi\n' + body
    _fake_tool(tmp_path / "bin", "psql", script)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")


@pytest.mark.parametrize("name", ["dump.sql", "dump.sql.gz"])
def test_native_restore_feeds_the_file_unpacked_on_standard_input(monkeypatch, tmp_path, name):
    received = tmp_path / "received.sql"
    _install_psql(monkeypatch, tmp_path, f"cat > {received}\n")
    content = "-- PostgreSQL database dump\nCREATE TABLE t (a int);\n" * 2000
    path = tmp_path / name
    path.write_bytes(gzip.compress(content.encode()) if name.endswith(".gz") else content.encode())
    stages = []
    result = run_restore(PG_SOURCE, path, progress=lambda u: stages.append(u.stage))
    assert received.read_text() == content
    assert result.how == "native" and result.statements is None
    assert stages[0] == "connecting" and stages[-1] == "loading"


def test_native_restore_failure_quotes_the_tool_and_says_nothing_changed(monkeypatch, tmp_path):
    _install_psql(
        monkeypatch,
        tmp_path,
        'cat > /dev/null\necho "ERROR:  schema \\"sales\\" already exists" >&2\nexit 3\n',
    )
    path = tmp_path / "dump.sql"
    path.write_text("-- PostgreSQL database dump\nCREATE SCHEMA sales;\n")
    with pytest.raises(BackupError) as caught:
        run_restore(PG_SOURCE, path)
    message = str(caught.value)
    assert message.startswith('psql failed: ERROR:  schema "sales" already exists')
    assert message.endswith("Nothing was changed.")


def test_native_restore_survives_a_tool_that_exits_before_reading(monkeypatch, tmp_path):
    _install_psql(monkeypatch, tmp_path, 'echo "psql: error: connection refused" >&2\nexit 2\n')
    path = tmp_path / "dump.sql"
    path.write_text("-- PostgreSQL database dump\n" + "SELECT 1;\n" * 200_000)
    with pytest.raises(BackupError, match="psql failed: psql: error: connection refused"):
        run_restore(PG_SOURCE, path)


def _cancel_after(seconds: float) -> threading.Event:
    cancel = threading.Event()
    threading.Timer(seconds, cancel.set).start()
    return cancel


BIG_DUMP = "-- PostgreSQL database dump\n" + "INSERT INTO t VALUES (1);\n" * 200_000  # ~5 MB


@pytest.mark.parametrize(
    ("behaviour", "content"),
    [
        # Never reads: our write() blocks once the pipe is full.
        ("exec sleep 30\n", BIG_DUMP),
        # Reads everything, then waits (as on a database lock): we block in wait().
        ("cat > /dev/null\nexec sleep 30\n", "-- PostgreSQL database dump\nSELECT 1;\n"),
    ],
    ids=["blocked-writing", "blocked-waiting"],
)
def test_cancel_stops_a_blocked_native_restore(monkeypatch, tmp_path, behaviour, content):
    _install_psql(monkeypatch, tmp_path, behaviour)
    path = tmp_path / "dump.sql"
    path.write_text(content)
    started = time.monotonic()
    with pytest.raises(BackupCancelled, match="Restore cancelled. Nothing was changed."):
        run_restore(PG_SOURCE, path, cancel=_cancel_after(0.3))
    assert time.monotonic() - started < 5


def test_cancel_kills_a_restore_tool_that_ignores_being_asked(monkeypatch, tmp_path):
    from polyglot_ai.core import db_backup

    monkeypatch.setattr(db_backup, "STOP_GRACE_SECONDS", 0.3)
    _install_psql(monkeypatch, tmp_path, "trap '' TERM\nwhile :; do sleep 0.1; done\n")
    path = tmp_path / "dump.sql"
    path.write_text(BIG_DUMP)
    started = time.monotonic()
    with pytest.raises(BackupCancelled):
        run_restore(PG_SOURCE, path, cancel=_cancel_after(0.2))
    assert time.monotonic() - started < 5


def test_cancel_of_a_non_atomic_restore_says_part_of_it_stays(monkeypatch, tmp_path):
    script = (
        'if [ "$1" = "--version" ]; then echo "mysql  Ver 8.0.39 for Linux"; exit 0; fi\n'
        "cat > /dev/null\nexec sleep 30\n"
    )
    _fake_tool(tmp_path / "bin", "mysql", script)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    path = tmp_path / "dump.sql"
    path.write_text("-- MySQL dump 10.13\nSELECT 1;\n")
    source = BackupSource("shop", "mysql", "mysql://app:s3cret@db.example.com:3306/shop")
    with pytest.raises(BackupCancelled, match="What mysql had loaded by then stays"):
        run_restore(source, path, cancel=_cancel_after(0.3))


def test_a_damaged_archive_stops_the_tool_before_it_can_commit(monkeypatch, tmp_path):
    """psql commits when its input ends — so it must not see a damaged file end."""
    marker = tmp_path / "reached-end-of-input"
    _install_psql(monkeypatch, tmp_path, f"cat > /dev/null\necho yes > {marker}\n")
    import random

    rng = random.Random(7)
    noise = "".join(f"INSERT INTO t VALUES ({rng.random()});\n" for _ in range(20_000))
    whole = gzip.compress(("-- PostgreSQL database dump\n" + noise).encode())
    path = tmp_path / "dump.sql.gz"
    path.write_bytes(whole[: len(whole) // 2])  # cut short, as a failed copy would leave it

    with pytest.raises(BackupError) as caught:
        run_restore(PG_SOURCE, path)
    message = str(caught.value)
    assert "damaged" in message and message.endswith("Nothing was changed.")
    assert not isinstance(caught.value, BackupCancelled)
    time.sleep(0.2)
    assert not marker.exists()


def test_an_archive_damaged_from_its_first_lines_is_explained(tmp_path):
    whole = gzip.compress(b"-- PostgreSQL database dump\n" + os.urandom(4000).hex().encode())
    path = tmp_path / "dump.sql.gz"
    path.write_bytes(whole[:200])
    with pytest.raises(BackupError, match="can't be read"):
        inspect_file(path)


def test_a_damaged_built_in_export_is_refused_as_damaged(tmp_path):
    whole = gzip.compress((EXPORT.replace("{engine}", "PostgreSQL") + "-- x\n" * 50_000).encode())
    path = tmp_path / "out.sql.gz"
    path.write_bytes(whole[: len(whole) - 40])
    source = BackupSource("pg", "postgresql", "postgresql://a:b@127.0.0.1:1/d")
    with pytest.raises(BackupError, match="damaged .* Nothing was loaded"):
        run_restore(source, path)


def test_plan_fields_default_to_cannot():
    assert RestorePlan().how == "" and BackupFile(Path("x"), "sql").description == "SQL script"
