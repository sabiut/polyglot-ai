"""Load a backup back into a database.

What a file is decides how it is loaded:

* a **built-in export** (:mod:`polyglot_ai.core.db_backup`) — read here
  and run through the connection's own driver, statement by statement.
  Works for every server engine, with nothing else installed;
* a **pg_dump / mysqldump file** — handed to ``psql``, ``pg_restore`` or
  ``mysql``, which are the only things that read everything those
  formats contain;
* a **SQLite database file** — copied over the connection's database
  with SQLite's backup API, after the current one is saved aside;
* any other ``.sql`` script, for **SQL Server** only — run in ``GO``
  batches, as ``sqlcmd`` would.

Nothing is dropped first, by any of these: a built-in export loaded into
a database that already has its tables stops at the first ``CREATE
TABLE``. PostgreSQL loads are one transaction — all or nothing. The
other engines commit as they go (their ``CREATE TABLE`` can't be rolled
back), so a load that fails part-way leaves what it had done; the error
says so.

:func:`run_restore` blocks; call it off the GUI thread. No Qt here.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import logging
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from polyglot_ai.core.db_backup import (
    stop_on_cancel,
    stop_process,
    BackupCancelled,
    BackupError,
    BackupSource,
    NativeTool,
    Progress,
    ProgressFn,
    _cancelled,
    _create_private,
    client_arguments,
    find_tool,
    no_database_error,
)
from polyglot_ai.core.db_dialects import ENGINES

logger = logging.getLogger(__name__)

EXPORT_MARKER = "-- Polyglot AI built-in export"
EXPORT_END = "-- End of export:"

_ENGINE_BY_LABEL = {
    "PostgreSQL": "postgresql",
    "MySQL / MariaDB": "mysql",
    "SQL Server": "mssql",
    "Oracle": "oracle",
}
_DUMP_SIGNS = {
    "postgresql database dump": "postgresql",
    "postgresql database cluster dump": "postgresql",
    "mysql dump": "mysql",
    "mariadb dump": "mysql",
}
_RESTORE_TOOLS = {
    "postgresql": ("psql",),
    "pg_custom": ("pg_restore",),
    "mysql": ("mysql", "mariadb"),
}


# ── What a file is ──────────────────────────────────────────────────


@dataclass
class BackupFile:
    path: Path
    kind: str  # "builtin" | "sqlite" | "pg_custom" | "sql"
    engine: str = ""  # db_type it was made from, when the file says
    compressed: bool = False
    connection: str = ""  # built-in exports: the "Connection:" line
    taken: str = ""  # built-in exports: the "Taken:" line
    size: int = 0

    @property
    def description(self) -> str:
        if self.kind == "builtin":
            label = ENGINES[self.engine].label if self.engine in ENGINES else "database"
            text = f"Built-in export of a {label} database"
            if self.connection:
                text += f" — {self.connection}"
            return text + (f", taken {self.taken}" if self.taken else "")
        if self.kind == "sqlite":
            return "SQLite database file"
        if self.kind == "pg_custom":
            return "pg_dump archive (custom format)"
        if self.engine in ENGINES:
            return f"{ENGINES[self.engine].label} dump (SQL)"
        return "SQL script"


def _open_text(path: Path, compressed: bool):
    if compressed:
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return open(path, encoding="utf-8", errors="replace", newline="")


def inspect_file(path: str | Path) -> BackupFile:
    """Work out what kind of backup a file is, from its first bytes."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise BackupError(f"There is no file at {path}.")
    with open(path, "rb") as raw:
        magic = raw.read(16)
    compressed = magic[:2] == b"\x1f\x8b"
    if compressed:
        try:
            with gzip.open(path, "rb") as packed:
                magic = packed.read(16)
        except (OSError, EOFError, zlib.error) as exc:
            raise BackupError(f"{path.name} looks compressed but can't be read: {exc}") from exc
    info = BackupFile(path, "sql", compressed=compressed, size=path.stat().st_size)
    if magic.startswith(b"SQLite format 3\x00"):
        info.kind = "sqlite"
        return info
    if magic.startswith(b"PGDMP"):
        info.kind, info.engine = "pg_custom", "postgresql"
        return info
    try:
        with _open_text(path, compressed) as text:
            head = [line.rstrip("\r\n") for _, line in zip(range(40), text)]
    except (OSError, EOFError, zlib.error) as exc:
        raise BackupError(f"{path.name} can't be read: {exc}") from exc
    if head and head[0].startswith(EXPORT_MARKER):
        info.kind = "builtin"
        for line in head:
            if line.startswith("-- Connection:"):
                info.connection = line.split(":", 1)[1].strip()
                for label, db_type in _ENGINE_BY_LABEL.items():
                    if f"({label})" in info.connection:
                        info.engine = db_type
            elif line.startswith("-- Taken:"):
                info.taken = line.split(":", 1)[1].strip()
        return info
    for line in head:
        lowered = line.lower()
        for sign, db_type in _DUMP_SIGNS.items():
            if lowered.startswith("--") and sign in lowered:
                info.engine = db_type
                return info
    return info


@dataclass
class RestorePlan:
    how: str = ""  # "driver" | "native" | "sqlite"; "" when it can't be done
    summary: str = ""  # what will happen
    problem: str = ""  # why it can't, when it can't
    tool: NativeTool | None = None
    atomic: bool = False  # all-or-nothing?


def plan_restore(file: BackupFile, db_type: str) -> RestorePlan:
    """How ``file`` would be loaded into a ``db_type`` connection — or why it can't be."""
    target = ENGINES[db_type].label if db_type in ENGINES else "SQLite"

    if db_type == "sqlite":
        if file.kind != "sqlite":
            return RestorePlan(
                problem="A SQLite connection is restored from a SQLite database file "
                f"(a copy made by Back up). This is a {file.description.lower()}."
            )
        if file.compressed:
            return RestorePlan(problem="Decompress the file first; SQLite reads it directly.")
        return RestorePlan(
            "sqlite",
            "The connection's database file is replaced with this copy. The current "
            "file is saved next to it first, so the restore can be undone.",
            atomic=True,
        )
    if file.kind == "sqlite":
        return RestorePlan(problem=f"This is a SQLite database file; it can't go into {target}.")
    if file.engine and file.engine != db_type:
        made_from = ENGINES[file.engine].label
        return RestorePlan(
            problem=f"This backup was made from {made_from}; its SQL won't load into {target}."
        )

    if file.kind == "builtin":
        if not file.engine:
            return RestorePlan(problem="The export doesn't say which engine it was made from.")
        atomic = db_type == "postgresql"
        return RestorePlan(
            "driver",
            "Its statements are run on this connection, in order. "
            + (
                "The load is one transaction: if anything fails, nothing is changed."
                if atomic
                else "Each statement is committed as it runs: if one fails, the ones "
                "before it stay."
            ),
            atomic=atomic,
        )

    wanted = _RESTORE_TOOLS.get("pg_custom" if file.kind == "pg_custom" else db_type)
    if wanted:
        tool = find_tool(wanted)
        if tool is None:
            package = "postgresql-client" if db_type == "postgresql" else "mariadb-client"
            return RestorePlan(
                problem=f"This file needs {wanted[0]} to load it, which isn't installed "
                f"(package “{package}”). Files made by the built-in export load without it."
            )
        atomic = tool.flavour == "postgres"
        return RestorePlan(
            "native",
            f"It is loaded with {tool.name}. "
            + (
                "The load is one transaction: if anything fails, nothing is changed."
                if atomic
                else "If a statement fails, the ones before it stay."
            ),
            tool=tool,
            atomic=atomic,
        )
    if db_type == "mssql":
        return RestorePlan(
            "driver",
            "The script is run on this connection in GO batches, as sqlcmd would. "
            "If a batch fails, the ones before it stay.",
        )
    return RestorePlan(
        problem=f"Only files made by the built-in export can be loaded into {target} here. "
        "For other dumps use the engine's own tools."
    )


# ── Splitting a script into statements ──────────────────────────────

_NEXT = re.compile(r"'|\"|`|\[|--|/\*|;")
_NEXT_PLAIN = re.compile(r"'|\"|`|--|/\*|;")


class StatementSplitter:
    """Cuts SQL text into the statements to run, a line at a time.

    Knows enough to not be fooled by a ``;``, a ``--`` or a ``GO`` inside
    a string, a quoted name or a comment. Comments are left out of what
    it returns (MySQL refuses a statement that is only a comment).

    With ``batch_separator`` (SQL Server's ``GO``) it returns whole
    batches, cut at the separator lines, and ``;`` is left alone.
    """

    def __init__(self, *, backslash_escapes: bool = False, batch_separator: str = "") -> None:
        self._backslash = backslash_escapes
        self._separator = batch_separator.upper()
        self._brackets = bool(batch_separator)
        self._buffer: list[str] = []
        self._inside = ""  # the closing delimiter being looked for, or ""

    def _take(self) -> str | None:
        text = "".join(self._buffer).strip()
        self._buffer = []
        return text or None

    def feed(self, line: str) -> Iterator[str]:
        if (
            self._separator
            and not self._inside
            and line.strip().rstrip(";").strip().upper() == self._separator
        ):
            statement = self._take()
            if statement:
                yield statement
            return
        position = 0
        pattern = _NEXT if self._brackets else _NEXT_PLAIN
        while position < len(line):
            if self._inside:
                position = self._skip_quoted(line, position)
                continue
            match = pattern.search(line, position)
            if match is None:
                self._buffer.append(line[position:])
                return
            self._buffer.append(line[position : match.start()])
            token, position = match.group(), match.end()
            if token == "--":
                self._buffer.append("\n")
                return  # the rest of the line is a comment
            if token == "/*":
                self._inside = "*/"
            elif token == ";":
                if self._separator:
                    self._buffer.append(";")
                    continue
                statement = self._take()
                if statement:
                    yield statement
            else:
                self._buffer.append(token)
                self._inside = "]" if token == "[" else token

    def _skip_quoted(self, line: str, position: int) -> int:
        closing = self._inside
        if closing == "*/":
            end = line.find("*/", position)
            if end < 0:
                return len(line)
            self._inside = ""
            self._buffer.append(" ")
            return end + 2
        index = position
        while index < len(line):
            char = line[index]
            if char == "\\" and self._backslash and closing == "'":
                index += 2
                continue
            if char == closing:
                if index + 1 < len(line) and line[index + 1] == closing:
                    index += 2  # a doubled delimiter is the character itself
                    continue
                self._buffer.append(line[position : index + 1])
                self._inside = ""
                return index + 1
            index += 1
        self._buffer.append(line[position:])
        return len(line)

    def finish(self) -> Iterator[str]:
        statement = self._take()
        if statement:
            yield statement


def split_statements(
    lines: Iterable[str], *, backslash_escapes: bool = False, batch_separator: str = ""
) -> Iterator[str]:
    splitter = StatementSplitter(
        backslash_escapes=backslash_escapes, batch_separator=batch_separator
    )
    for line in lines:
        yield from splitter.feed(line)
    yield from splitter.finish()


# ── Running statements through the driver ───────────────────────────


@dataclass
class RestoreResult:
    path: Path
    how: str
    statements: int | None = None
    seconds: float = 0.0
    #: SQLite: where the database as it was before the restore was saved.
    saved_previous: Path | None = None
    warnings: list[str] = field(default_factory=list)


#: How long a statement that was told to stop gets to do so before the
#: restore gives up waiting for it.
INTERRUPT_GRACE_SECONDS = 10.0


class _Runner:
    """Runs one statement at a time on a connected DatabaseConnection."""

    def __init__(self, connection) -> None:
        self._connection = connection

    async def open(self) -> None: ...

    async def run(self, statement: str) -> None:
        raise NotImplementedError

    async def interrupt(self, work: asyncio.Future) -> bool:
        """Make the statement running as ``work`` give up, because Cancel was pressed.

        Returns once it has, or once :data:`INTERRUPT_GRACE_SECONDS` have
        passed — never later. True if the statement stopped; False if the
        server may still be running it.
        """
        work.cancel()
        await asyncio.wait({work}, timeout=INTERRUPT_GRACE_SECONDS)
        return work.done()

    async def failed(self) -> None:
        """Undo what can be undone."""

    async def close(self) -> None: ...


class _PostgresRunner(_Runner):
    async def open(self) -> None:
        self._pg = await self._connection._pg_pool.acquire()

    async def run(self, statement: str) -> None:
        await self._pg.execute(statement)

    async def interrupt(self, work: asyncio.Future) -> bool:
        # Cancelling the call makes asyncpg send the server a cancel
        # request for it. The ROLLBACK then waits for the server to have
        # let go of the statement — but not for ever: a cancel request
        # can get lost (a proxy in between), and then the connection is
        # dropped instead. Either way nothing of the load can be
        # committed any more.
        work.cancel()
        await asyncio.wait({work}, timeout=INTERRUPT_GRACE_SECONDS)
        try:
            await asyncio.wait_for(self._pg.execute("ROLLBACK"), INTERRUPT_GRACE_SECONDS)
        except Exception:
            logger.debug("Restore: dropping the connection after a cancel", exc_info=True)
            self._pg.terminate()
        return True

    async def failed(self) -> None:
        if self._pg.is_closed():
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._pg.execute("ROLLBACK"), INTERRUPT_GRACE_SECONDS)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self._connection._pg_pool.release(self._pg), INTERRUPT_GRACE_SECONDS
            )


class _MySQLRunner(_Runner):
    async def open(self) -> None:
        self._my = self._connection._mysql_pool._connection
        await self._my.ping(reconnect=True)

    async def run(self, statement: str) -> None:
        async with self._my.cursor() as cursor:
            await cursor.execute(statement)

    async def _kill(self, thread_id: int) -> None:
        """KILL QUERY from a second login: the first is busy with the statement."""
        from polyglot_ai.core.db_explorer import DatabaseConnection

        first = self._connection
        other = DatabaseConnection(
            f"{first.name} (cancel)",
            first.db_type,
            first._connection_string,
            read_only=False,
            options=first.options,
        )
        try:
            ok, message = await other.connect()
            if not ok:
                raise BackupError(f"Couldn't connect to stop the statement: {message}")
            async with other._mysql_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"KILL QUERY {int(thread_id)}")
        finally:
            await other.disconnect()

    async def interrupt(self, work: asyncio.Future) -> bool:
        try:
            await asyncio.wait_for(self._kill(self._my.thread_id()), INTERRUPT_GRACE_SECONDS)
            await asyncio.wait({work}, timeout=INTERRUPT_GRACE_SECONDS)
        except Exception:
            logger.debug("Restore: KILL QUERY didn't get through", exc_info=True)
        if work.done():
            return True
        # Hang up instead. The server only notices when it next writes
        # to this connection, so the statement may still run to its end.
        with contextlib.suppress(Exception):
            self._my.close()
        work.cancel()
        await asyncio.wait({work}, timeout=1.0)
        return False


class _Lane:
    """One background thread that makes a synchronous driver's calls, in order.

    pymssql and python-oracledb block while the server works. On their
    own thread that doesn't stop the restore from noticing Cancel, and
    because the thread is a daemon, a call the server never answers
    can't keep the application from exiting either.
    """

    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._work, name="db-restore-driver", daemon=True).start()

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            call, loop, future = job
            result = error = None
            try:
                result = call()
            except BaseException as exc:  # handed to whoever is waiting
                error = exc
            if future is None:
                continue
            with contextlib.suppress(RuntimeError):  # the loop has gone: nobody is waiting
                loop.call_soon_threadsafe(self._settle, future, result, error)

    @staticmethod
    def _settle(future: asyncio.Future, result, error) -> None:
        if future.done():
            return
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)

    async def call(self, call):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._jobs.put((call, loop, future))
        return await future

    def finish_with(self, call=None) -> None:
        """End the thread, after ``call`` — and after whatever it is still doing."""
        if call is not None:
            self._jobs.put((call, None, None))
        self._jobs.put(None)


class _ThreadRunner(_Runner):
    """A synchronous driver, kept off the restore's own thread."""

    def __init__(self, connection) -> None:
        super().__init__(connection)
        self._lane: _Lane | None = None
        self._abandoned = False

    # What a driver provides, all blocking:
    def _open(self) -> None: ...

    def _run(self, statement: str) -> None:
        raise NotImplementedError

    def _break(self) -> None:
        """Ask the server to stop the running statement. Called from another thread."""
        raise NotImplementedError

    def _undo(self) -> None: ...

    def _close(self) -> None: ...

    async def open(self) -> None:
        self._lane = _Lane()
        try:
            await self._lane.call(self._open)
        except BaseException:
            self._lane.finish_with()
            raise

    async def run(self, statement: str) -> None:
        await self._lane.call(lambda: self._run(statement))

    async def interrupt(self, work: asyncio.Future) -> bool:
        def ask() -> None:
            try:
                self._break()
            except Exception:
                logger.debug("Restore: the driver's cancel failed", exc_info=True)

        # Its own thread: the driver's cancel is network traffic too, and
        # must not be able to hold the restore up either.
        threading.Thread(target=ask, name="db-restore-cancel", daemon=True).start()
        await asyncio.wait({work}, timeout=INTERRUPT_GRACE_SECONDS)
        if work.done():
            return True

        # The statement is still running and the driver's connection is
        # inside it, so it can't be closed from here. Leave both to the
        # lane's thread: when the server answers, it undoes what it can
        # and closes. If there is a tunnel, stopping it (which the caller
        # does next) cuts the statement short.
        self._abandoned = True
        driver, self._connection._driver = self._connection._driver, None

        def give_up() -> None:
            for step in (self._undo, self._close, getattr(driver, "close", None)):
                if step is not None:
                    with contextlib.suppress(Exception):
                        step()

        self._lane.finish_with(give_up)
        return False

    async def failed(self) -> None:
        if not self._abandoned:
            with contextlib.suppress(Exception):
                await self._lane.call(self._undo)

    async def close(self) -> None:
        if self._lane is None or self._abandoned:
            return
        try:
            await self._lane.call(self._close)
        finally:
            self._lane.finish_with()


class _MssqlRunner(_ThreadRunner):
    def _open(self) -> None:
        self._ms = self._connection._driver._conn

    def _run(self, statement: str) -> None:
        cursor = self._ms.cursor()
        try:
            cursor.execute(statement)
            # An error in a later statement of the batch only surfaces
            # when its result is reached.
            while cursor.nextset():
                pass
        finally:
            cursor.close()

    def _break(self) -> None:
        # pymssql's DB-API connection wraps the FreeTDS one, whose cancel
        # sends the server an attention signal.
        self._ms._conn.cancel()


class _OracleRunner(_ThreadRunner):
    _SQLPLUS = re.compile(r"^SET\s+(DEFINE|SQLBLANKLINES)\b", re.IGNORECASE)

    def _open(self) -> None:
        self._ora = self._connection._driver._conn
        self._ora.autocommit = False

    def _run(self, statement: str) -> None:
        if self._SQLPLUS.match(statement):
            return  # settings for SQL*Plus, not SQL
        if statement.upper() == "COMMIT":
            self._ora.commit()
            return
        with self._ora.cursor() as cursor:
            cursor.execute(statement)

    def _break(self) -> None:
        self._ora.cancel()

    def _undo(self) -> None:
        self._ora.rollback()

    def _close(self) -> None:
        self._ora.autocommit = True


_RUNNERS = {
    "postgresql": _PostgresRunner,
    "mysql": _MySQLRunner,
    "mssql": _MssqlRunner,
    "oracle": _OracleRunner,
}


def _check_complete(file: BackupFile, cancel) -> None:
    """A built-in export ends with a closing line; one that doesn't was cut short."""
    last = ""
    try:
        with _open_text(file.path, file.compressed) as text:
            for count, line in enumerate(text):
                if line.strip():
                    last = line
                if count % 5000 == 0:
                    _cancelled(cancel)
    except (EOFError, OSError, zlib.error) as exc:
        raise BackupError(
            f"The backup file is damaged and couldn't be read to its end: {exc}.  "
            "Nothing was loaded."
        ) from exc
    if not last.startswith(EXPORT_END):
        raise BackupError(
            "This export is incomplete — it doesn't end with its closing line, so the "
            "backup that made it didn't finish. Nothing was loaded."
        )


class _Interrupted(BackupCancelled):
    """Cancel was pressed while a statement was running."""

    def __init__(self, stopped: bool, finished: bool = False) -> None:
        super().__init__("Cancelled.")
        #: False if the server may still be running the statement.
        self.stopped = stopped
        #: True if the statement got to its end just as it was being stopped.
        self.finished = finished


async def _until_set(cancel: threading.Event) -> None:
    while not cancel.is_set():
        await asyncio.sleep(0.1)


async def _run_or_interrupt(runner, statement: str, cancelled: asyncio.Future | None) -> None:
    """Run ``statement``; if Cancel is pressed meanwhile, stop it and raise.

    Cancel used to be looked at between statements only, so one that was
    waiting for a lock — or just large — held the restore until the
    server was done with it.
    """
    if cancelled is None:
        await runner.run(statement)
        return
    work = asyncio.ensure_future(runner.run(statement))
    try:
        await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        work.cancel()
        raise
    if work.done():
        work.result()  # raises what the statement raised
        return
    stopped = await runner.interrupt(work)
    # What it raised on being stopped is expected, and read here so that
    # asyncio doesn't report it as an exception nobody looked at.
    finished = work.done() and not work.cancelled() and work.exception() is None
    raise _Interrupted(stopped, finished)


async def _restore_with_driver(
    source: BackupSource, file: BackupFile, plan: RestorePlan, report: ProgressFn, cancel
) -> RestoreResult:
    from polyglot_ai.core.db_drivers import describe_error
    from polyglot_ai.core.db_explorer import DatabaseConnection

    if file.kind == "builtin":
        report(Progress("checking"))
        _check_complete(file, cancel)
    connection = DatabaseConnection(
        f"{source.name} (restore)",
        source.db_type,
        source.connection_string,
        read_only=False,
        options=source.options,
    )
    report(Progress("connecting"))
    ok, message = await connection.connect()
    if not ok:
        await connection.disconnect()
        raise BackupError(f"Couldn't connect: {message}")
    try:
        direct = {
            "postgresql": connection._pg_pool,
            "mysql": connection._mysql_pool,
        }.get(source.db_type, connection._driver)
        if direct is None:
            raise BackupError(
                f"The {ENGINES[source.db_type].label} driver isn't installed, "
                "so the backup can't be loaded."
            )
        runner = _RUNNERS[source.db_type](connection)
        await runner.open()
        done = 0
        cancelled = asyncio.ensure_future(_until_set(cancel)) if cancel is not None else None
        try:
            with open(file.path, "rb") as raw:
                stream = gzip.GzipFile(fileobj=raw) if file.compressed else raw
                lines = (line.decode("utf-8", errors="replace") for line in stream)
                for statement in split_statements(
                    lines,
                    backslash_escapes=source.db_type == "mysql",
                    batch_separator="GO" if source.db_type == "mssql" else "",
                ):
                    _cancelled(cancel)
                    try:
                        await _run_or_interrupt(runner, statement, cancelled)
                    except BackupCancelled:
                        raise
                    except Exception as exc:
                        await runner.failed()
                        applied = (
                            "Nothing was changed."
                            if plan.atomic or done == 0
                            else f"The {done:,} statement{'' if done == 1 else 's'} before it "
                            "were applied, so the database now holds part of the backup."
                        )
                        preview = " ".join(statement.split())[:120]
                        raise BackupError(
                            f"Statement {done + 1} failed: {describe_error(exc)}  "
                            f"[{preview}]  {applied}"
                        ) from exc
                    done += 1
                    if done % 20 == 0:
                        report(Progress("loading", index=done, size=raw.tell(), total=file.size))
        except BackupCancelled as stop:
            if isinstance(stop, _Interrupted) and stop.finished:
                done += 1
            await runner.failed()
            if plan.atomic:
                # The transaction was rolled back, or its connection is
                # gone before a COMMIT was sent.
                raise BackupCancelled("Restore cancelled. Nothing was changed.") from None
            message = (
                f"Restore cancelled after {done:,} statement{'' if done == 1 else 's'}; "
                f"{'that was' if done == 1 else 'those were'} applied."
                if done
                else "Restore cancelled before any statement was applied."
            )
            if isinstance(stop, _Interrupted) and not stop.stopped:
                message += (
                    "  The statement that was running hadn't stopped after "
                    f"{INTERRUPT_GRACE_SECONDS:.0f} seconds and may still complete."
                )
            raise BackupCancelled(message) from None
        finally:
            if cancelled is not None:
                cancelled.cancel()
            await runner.close()
        return RestoreResult(file.path, "driver", statements=done)
    finally:
        await connection.disconnect()


# ── Native tools ────────────────────────────────────────────────────


def restore_command(tool: NativeTool, target, **connection) -> tuple[list[str], dict[str, str]]:
    """The command that loads a dump from its standard input into ``target``."""
    argv, env = client_arguments(tool, target, **connection)
    if tool.flavour == "postgres":
        if not target.database:
            raise no_database_error()
        argv += ["--dbname", target.database, "--single-transaction"]
        if tool.name == "pg_restore":
            # As whoever is connected, whatever roles the dump was made with.
            argv += ["--exit-on-error", "--no-owner", "--no-privileges"]
        else:
            argv += ["--set", "ON_ERROR_STOP=1", "--quiet", "--no-psqlrc"]
        return argv, env
    if not target.database:
        raise no_database_error()
    argv.append(target.database)
    return argv, env


def _restore_native(
    source: BackupSource, file: BackupFile, plan: RestorePlan, report: ProgressFn, cancel
) -> RestoreResult:
    from polyglot_ai.core.db_explorer import DatabaseConnection
    from polyglot_ai.core.db_tunnel import ConnectionSetupError

    tool = plan.tool
    connection = DatabaseConnection(
        f"{source.name} (restore)", source.db_type, source.connection_string, options=source.options
    )
    report(Progress("connecting"))
    secrets = Path(tempfile.mkdtemp(prefix="polyglot_restore_"))
    try:
        try:
            target = connection._prepare_target()
        except ConnectionSetupError as exc:
            raise BackupError(str(exc)) from exc
        argv, extra_env = restore_command(
            tool,
            target,
            ssl_mode=connection._ssl_mode(),
            ca_file=source.options.get("ca_file", ""),
            tunnelled=connection._tunnel is not None,
            iam=bool(source.options.get("iam")),
            secrets_dir=secrets,
        )
        _cancelled(cancel)
        unreadable: BaseException | None = None
        with open(secrets / "output.log", "w+b") as output, open(file.path, "rb") as raw:
            # pg_restore reads its own compression; a gzipped SQL file is unpacked here.
            unpack = file.compressed and file.kind != "pg_custom"
            stream = gzip.GzipFile(fileobj=raw) if unpack else raw
            process = subprocess.Popen(  # noqa: S603 — argv built above, no shell
                argv,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                env={**os.environ, **extra_env},
            )
            # Cancel has to reach the tool while this thread is blocked:
            # in write() when the tool has stopped reading, or in wait()
            # when it has read everything and sits on a database lock.
            with stop_on_cancel(process, cancel):
                try:
                    while chunk := stream.read(1 << 16):
                        if cancel is not None and cancel.is_set():
                            break
                        try:
                            process.stdin.write(chunk)
                        except (BrokenPipeError, OSError):
                            break  # the tool stopped: its exit code and output say why
                        report(Progress("loading", size=raw.tell(), total=file.size))
                except BaseException as exc:
                    # Stopped before its input ends: a client that reaches
                    # the end of its input commits what it was given.
                    stop_process(process)
                    if isinstance(exc, (EOFError, OSError, zlib.error)):
                        unreadable = exc
                    else:
                        raise
                finally:
                    if cancel is not None and cancel.is_set():
                        stop_process(process)  # likewise: before the input is closed
                    with contextlib.suppress(OSError):
                        process.stdin.close()
                    code = process.wait()
            output.seek(0)
            said = output.read().decode("utf-8", errors="replace").strip()
        if unreadable is not None:
            raise BackupError(
                f"The backup file is damaged and couldn't be read to its end: {unreadable}.  "
                + (
                    "Nothing was changed."
                    if plan.atomic
                    else f"What {tool.name} had loaded by then stays in the database."
                )
            ) from unreadable
        # A tool that finished cleanly has loaded the file, even if Cancel
        # was pressed in its last moment: say what happened, not what was asked.
        if code != 0 and cancel is not None and cancel.is_set():
            raise BackupCancelled(
                "Restore cancelled. Nothing was changed."
                if plan.atomic
                else f"Restore cancelled. What {tool.name} had loaded by then stays in the database."
            )
        if code != 0:
            lines = [line for line in said.splitlines() if line.strip()]
            detail = " ".join(lines[-3:]) or f"exit code {code}"
            applied = (
                "Nothing was changed."
                if plan.atomic
                else "What it had loaded before the error stays in the database."
            )
            raise BackupError(f"{tool.name} failed: {detail}  {applied}")
        return RestoreResult(file.path, "native")
    finally:
        try:
            connection._stop_tunnel()
        finally:
            shutil.rmtree(secrets, ignore_errors=True)


# ── SQLite ──────────────────────────────────────────────────────────


def _restore_sqlite(
    source: BackupSource, file: BackupFile, report: ProgressFn, cancel
) -> RestoreResult:
    location = source.connection_string
    if location in ("", ":memory:") or location.startswith("file:"):
        raise BackupError("Only a connection to a database file can be restored into.")
    destination = Path(location).expanduser()
    if not destination.parent.is_dir():
        raise BackupError(f"The folder {destination.parent} doesn't exist.")
    if destination.exists() and destination.resolve() == file.path.resolve():
        raise BackupError("That file is the connection's own database.")

    report(Progress("checking"))
    try:
        backup = sqlite3.connect(f"file:{file.path.resolve()}?mode=ro", uri=True, timeout=30)
        verdict = backup.execute("PRAGMA quick_check(1)").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"The backup file can't be read: {exc}") from exc
    try:
        if not verdict or verdict[0] != "ok":
            raise BackupError(f"The backup file fails SQLite's integrity check: {verdict}")

        saved: Path | None = None
        if destination.exists() and destination.stat().st_size > 0:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            saved = destination.with_name(f"{destination.name}.before-restore-{stamp}")
            _create_private(saved).close()
            current = sqlite3.connect(f"file:{destination.resolve()}?mode=ro", uri=True, timeout=30)
            keep = sqlite3.connect(saved)
            try:
                current.backup(keep)
            except sqlite3.Error as exc:
                keep.close()
                saved.unlink(missing_ok=True)
                raise BackupError(
                    f"Couldn't save the current database aside, so nothing was replaced: {exc}"
                ) from exc
            finally:
                keep.close()
                current.close()

        target = sqlite3.connect(destination, timeout=30)
        try:

            def on_pages(_status: int, remaining: int, total: int) -> None:
                report(Progress("copying", index=total - remaining, total=total))

            # Not cancellable once started: a half-replaced database is
            # worse than waiting for the copy to finish.
            backup.backup(target, pages=2048, progress=on_pages)
        except sqlite3.Error as exc:
            where = f" The database as it was is in {saved}." if saved else ""
            raise BackupError(f"Couldn't write the database: {exc}.{where}") from exc
        finally:
            target.close()
        return RestoreResult(file.path, "sqlite", saved_previous=saved)
    finally:
        backup.close()


# ── Entry point ─────────────────────────────────────────────────────


def run_restore(
    source: BackupSource,
    path: str | Path,
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> RestoreResult:
    """Load the backup at ``path`` into ``source``'s database. Blocks until done."""
    report = progress or (lambda _update: None)
    file = inspect_file(path)
    plan = plan_restore(file, source.db_type)
    if not plan.how:
        raise BackupError(plan.problem)
    started = time.monotonic()
    _cancelled(cancel)
    if plan.how == "sqlite":
        result = _restore_sqlite(source, file, report, cancel)
    elif plan.how == "native":
        result = _restore_native(source, file, plan, report, cancel)
    else:
        loop = asyncio.SelectorEventLoop()
        try:
            result = loop.run_until_complete(
                _restore_with_driver(source, file, plan, report, cancel)
            )
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()
    result.seconds = time.monotonic() - started
    return result
