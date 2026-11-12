"""Back up a database the Database panel can connect to.

Three ways, depending on the engine and on what is installed:

* **copy** — SQLite: an exact, consistent copy of the database file,
  made with SQLite's own online-backup API (safe while it is in use).
* **native** — PostgreSQL and MySQL/MariaDB, when ``pg_dump`` or
  ``mysqldump`` is installed: the vendor's own dump, which is complete.
* **builtin** — every server engine, nothing else needed: tables and
  their rows as a ``.sql`` file (:mod:`polyglot_ai.core.db_export`).
  The only choice for SQL Server and Oracle, whose native backups are
  written on the server, not here.

All of them go through the connection's own settings — encryption, SSH
or Session Manager tunnel, IAM sign-in — on a connection opened for the
purpose, so the panel's queries carry on meanwhile.

:func:`run_backup` blocks until the file is written; call it off the GUI
thread. It writes to ``<name>.part`` and renames at the end, so a file
with the name that was asked for is always a finished backup. Files are
created readable by the user only: they hold the whole database.

No Qt here.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import io
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from polyglot_ai.core.db_dialects import ENGINES, DbTarget, describe_target

logger = logging.getLogger(__name__)


class BackupError(Exception):
    """The backup couldn't be made; the message is for the user."""


class BackupCancelled(BackupError):
    """The user stopped it. Nothing is left behind."""


@dataclass(frozen=True)
class BackupSource:
    """What a backup needs to know about a saved connection."""

    name: str
    db_type: str
    connection_string: str
    options: dict = field(default_factory=dict)

    @classmethod
    def of(cls, connection) -> BackupSource:
        return cls(
            connection.name,
            connection.db_type,
            connection._connection_string,
            dict(connection.options),
        )

    @property
    def where(self) -> str:
        try:
            return describe_target(self.db_type, self.connection_string, self.options)
        except Exception:
            return ""


@dataclass(frozen=True)
class Method:
    key: str  # "copy" | "native" | "builtin"
    label: str
    summary: str  # what the file will and won't contain
    extension: str
    compressible: bool = True


@dataclass
class Progress:
    stage: str  # "connecting" | "reading" | "table" | "dumping" | "copying" | "checking"
    table: str = ""
    index: int = 0  # 1-based position of ``table``
    total: int = 0  # tables, or pages for a file copy
    rows: int = 0  # rows written so far, all tables
    size: int = 0  # bytes written so far


@dataclass
class BackupResult:
    path: Path
    method: str
    tables: int | None = None
    rows: int | None = None
    size: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


ProgressFn = Callable[[Progress], None]


# ── What can be done for an engine ──────────────────────────────────


@dataclass(frozen=True)
class NativeTool:
    name: str  # as the user would type it
    path: str
    flavour: str  # "postgres" | "mysql" | "mariadb"
    major: int = 0


_NATIVE = {"postgresql": ("pg_dump",), "mysql": ("mysqldump", "mariadb-dump")}

_INSTALL_HINT = {
    "postgresql": (
        "For a complete backup — views, functions, sequences, indexes and all — install "
        "pg_dump (package “postgresql-client” on Debian and Ubuntu, “postgresql” on Fedora), "
        "at least as new as the server."
    ),
    "mysql": (
        "For a complete backup — views, routines and triggers as well — install mysqldump "
        "(package “mariadb-client” or “mysql-client”)."
    ),
}


def find_tool(names: tuple[str, ...]) -> NativeTool | None:
    """The first of these client programs that is on the PATH."""
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        banner = ""
        try:
            banner = subprocess.run(  # noqa: S603 — a fixed argv, no shell
                [path, "--version"], capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.SubprocessError):
            logger.debug("Couldn't read %s --version", name, exc_info=True)
        if name.startswith(("pg_", "psql")):
            flavour = "postgres"
        else:
            flavour = "mariadb" if "mariadb" in (banner + name).lower() else "mysql"
        major = 0
        for word in banner.replace(",", " ").split():
            head = word.split(".")[0].split("-")[0]
            if head.isdigit() and "." in word:
                major = int(head)
                break
        return NativeTool(name, path, flavour, major)
    return None


def find_native(db_type: str) -> NativeTool | None:
    """The vendor dump tool for this engine, if one is installed."""
    return find_tool(_NATIVE.get(db_type, ()))


def methods_for(db_type: str) -> list[Method]:
    """The ways this engine can be backed up here, best first."""
    if db_type == "sqlite":
        return [
            Method(
                "copy",
                "Exact copy of the database file",
                "Everything in the database, as a SQLite file you can open directly. "
                "Safe to take while the database is in use; the copy is checked afterwards.",
                ".db",
                compressible=False,
            )
        ]
    if db_type not in ENGINES:
        return []
    from polyglot_ai.core.db_export import EXPORTERS

    methods = []
    tool = find_native(db_type)
    if tool is not None:
        methods.append(
            Method(
                "native",
                f"Complete backup with {tool.name}",
                f"Everything {tool.name} writes: tables and data, views, routines, triggers, "
                "indexes and constraints"
                + (
                    ", without ownership and grants so it loads into any server. "
                    if db_type == "postgresql"
                    else ". "
                )
                + "Restores with the engine's own tools. "
                + (
                    "Name the file “.dump” for pg_dump's compressed custom format "
                    "(restored with pg_restore)."
                    if db_type == "postgresql"
                    else ""
                ),
                ".sql",
            )
        )
    exporter = EXPORTERS[db_type]
    methods.append(
        Method(
            "builtin",
            "Built-in export (tables and data)",
            f"Every table and its rows as SQL. Not included: {exporter.NOT_INCLUDED}",
            ".sql",
        )
    )
    return methods


def install_hint(db_type: str) -> str:
    """How to get the complete-backup tool, when it isn't installed."""
    if db_type in _INSTALL_HINT and find_native(db_type) is None:
        return _INSTALL_HINT[db_type]
    return ""


def default_filename(name: str, method: Method, when: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(when))
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-.")
    return f"{safe or 'database'}-{stamp}{method.extension}"


# ── Files ───────────────────────────────────────────────────────────


def _create_private(path: Path) -> io.BufferedWriter:
    """A new file only the user can read — it will hold the whole database."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    return os.fdopen(fd, "wb")


@contextlib.contextmanager
def _open_binary(path: Path, compress: bool):
    raw = _create_private(path)
    try:
        if compress:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as packed:
                yield packed
        else:
            yield raw
    finally:
        raw.close()


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise BackupCancelled("Cancelled.")


# ── SQLite: a copy ──────────────────────────────────────────────────


def _copy_sqlite(source: BackupSource, part: Path, report: ProgressFn, cancel) -> BackupResult:
    location = source.connection_string
    if location in ("", ":memory:"):
        raise BackupError("An in-memory database has no file to back up.")
    if location.startswith("file:"):
        uri = location + ("&" if "?" in location else "?") + "mode=ro"
    else:
        path = Path(location).expanduser()
        if not path.is_file():
            raise BackupError(f"The database file isn't there: {path}")
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"

    _create_private(part).close()  # so SQLite opens a file that is already private
    try:
        origin = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise BackupError(f"Couldn't open the database: {exc}") from exc
    copy = sqlite3.connect(part)
    try:

        def on_pages(_status: int, remaining: int, total: int) -> None:
            _cancelled(cancel)
            report(Progress("copying", index=total - remaining, total=total))

        try:
            origin.backup(copy, pages=2048, progress=on_pages)
        except sqlite3.Error as exc:
            raise BackupError(f"Couldn't copy the database: {exc}") from exc
        report(Progress("checking"))
        verdict = copy.execute("PRAGMA quick_check(1)").fetchone()
        if not verdict or verdict[0] != "ok":
            raise BackupError(f"The copy failed SQLite's integrity check: {verdict}")
        tables = copy.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        copy.close()
        origin.close()
    return BackupResult(part, "copy", tables=tables)


# ── Native tools ────────────────────────────────────────────────────

#: How long a client program gets to exit after being asked to, before it is killed.
STOP_GRACE_SECONDS = 3.0


def stop_process(process: subprocess.Popen) -> None:
    """Ask a client program to stop; kill it if it hasn't within the grace period."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()


@contextlib.contextmanager
def stop_on_cancel(process: subprocess.Popen, cancel: threading.Event | None):
    """While inside, setting ``cancel`` stops ``process`` — whatever the caller is doing.

    The caller may be blocked writing to the program, reading from it or
    waiting for it to exit (a restore waiting on a database lock sits in
    the last of those indefinitely), so the watching is done by a thread
    of its own. The program is asked to stop, and killed if it hasn't
    within :data:`STOP_GRACE_SECONDS`; the blocked call then returns.
    """
    if cancel is None:
        yield
        return
    finished = threading.Event()

    def watch() -> None:
        while not finished.is_set():
            if cancel.wait(0.1):
                stop_process(process)
                return

    watcher = threading.Thread(target=watch, name="db-backup-cancel", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        finished.set()
        watcher.join(timeout=STOP_GRACE_SECONDS + 2)


def _pg_sslmode(mode: str | None, tunnelled: bool) -> str:
    if mode == "verify":
        # Through a tunnel the address dialled is 127.0.0.1, which no
        # certificate names: check the chain, not the host name.
        return "verify-ca" if tunnelled else "verify-full"
    return mode if mode in ("disable", "prefer", "require") else ""


def client_arguments(
    tool: NativeTool,
    target: DbTarget,
    *,
    ssl_mode: str | None = None,
    ca_file: str = "",
    tunnelled: bool = False,
    iam: bool = False,
    secrets_dir: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """How one of the vendor's client programs reaches ``target``.

    Returns the program with its connection options, and the environment
    that goes with them — the same for a dump as for a restore. The
    password never goes on the command line, where any user on the
    machine could read it: PostgreSQL's tools take it from
    ``PGPASSWORD``, MySQL's from an options file in ``secrets_dir``.
    The database name is not included: each program wants it in its own
    place.
    """
    env: dict[str, str] = {}
    if tool.flavour == "postgres":
        argv = [tool.path, "--host", target.host, "--port", str(target.port), "--no-password"]
        if target.user:
            argv += ["--username", target.user]
        if target.password:
            env["PGPASSWORD"] = target.password
        sslmode = _pg_sslmode(ssl_mode, tunnelled)
        if sslmode:
            env["PGSSLMODE"] = sslmode
        if ssl_mode == "verify" and ca_file:
            env["PGSSLROOTCERT"] = str(Path(ca_file).expanduser())
        env["PGCONNECT_TIMEOUT"] = "15"
        env["PGAPPNAME"] = "Polyglot AI backup"
        return argv, env

    argv = [tool.path]
    if target.password:
        if secrets_dir is None:
            raise BackupError("No private directory to pass the password in.")
        options = secrets_dir / "client.cnf"
        escaped = target.password.replace("\\", "\\\\").replace('"', '\\"')
        with _create_private(options) as handle:
            handle.write(f'[client]\npassword="{escaped}"\n'.encode())
        argv.append(f"--defaults-extra-file={options}")  # must come first
    argv += ["--host", target.host, "--port", str(target.port), "--protocol=TCP"]
    if target.user:
        argv += ["--user", target.user]
    argv.append("--default-character-set=utf8mb4")
    ca = str(Path(ca_file).expanduser()) if ca_file else ""
    if tool.flavour == "mariadb":
        if ssl_mode == "disable":
            argv.append("--skip-ssl")
        elif ssl_mode in ("require", "verify"):
            argv.append("--ssl")
            if ssl_mode == "verify":
                if ca:
                    argv.append(f"--ssl-ca={ca}")
                if not tunnelled:
                    argv.append("--ssl-verify-server-cert")
    else:
        modes = {
            "disable": "DISABLED",
            "prefer": "PREFERRED",
            "require": "REQUIRED",
            "verify": "VERIFY_CA" if tunnelled else "VERIFY_IDENTITY",
        }
        if ssl_mode in modes:
            argv.append(f"--ssl-mode={modes[ssl_mode]}")
        if ssl_mode == "verify" and ca:
            argv.append(f"--ssl-ca={ca}")
        if iam:
            argv.append("--enable-cleartext-plugin")  # how an IAM token is sent
    return argv, env


def no_database_error() -> BackupError:
    return BackupError(
        "This connection has no database selected. "
        "Edit the connection and fill in the Database field."
    )


def native_command(
    tool: NativeTool, target: DbTarget, *, custom_format: bool = False, **connection
) -> tuple[list[str], dict[str, str]]:
    """The dump command for ``target``, and the environment it needs."""
    argv, env = client_arguments(tool, target, **connection)
    if tool.flavour == "postgres":
        if target.database:
            argv += ["--dbname", target.database]
        # Without ownership and grants: the file then loads into any
        # server, whatever roles it has (an RDS dump into a local one).
        argv += [
            "--format=custom" if custom_format else "--format=plain",
            "--no-owner",
            "--no-privileges",
        ]
        return argv, env
    if not target.database:
        raise no_database_error()
    argv += [
        "--single-transaction",  # a consistent InnoDB snapshot, without locking
        "--quick",
        "--routines",
        "--triggers",
        "--hex-blob",
    ]
    if tool.flavour == "mysql":
        # Restorable without SUPER, and by a user without PROCESS (RDS).
        argv += ["--set-gtid-purged=OFF", "--no-tablespaces"]
        if tool.major >= 8:
            argv.append("--column-statistics=0")  # absent on MariaDB and MySQL 5.7 servers
    argv.append(target.database)
    return argv, env


def _run_native(
    source: BackupSource, part: Path, compress: bool, report: ProgressFn, cancel
) -> BackupResult:
    from polyglot_ai.core.db_explorer import DatabaseConnection
    from polyglot_ai.core.db_tunnel import ConnectionSetupError

    tool = find_native(source.db_type)
    if tool is None:
        raise BackupError("The dump tool for this engine isn't installed.")
    custom = tool.flavour == "postgres" and part.name.lower().endswith(
        (".dump.part", ".backup.part")
    )
    connection = DatabaseConnection(
        f"{source.name} (backup)", source.db_type, source.connection_string, options=source.options
    )
    report(Progress("connecting"))
    secrets = Path(tempfile.mkdtemp(prefix="polyglot_backup_"))
    try:
        try:
            # Opens the tunnel and fetches an IAM token, if the connection uses them.
            target = connection._prepare_target()
        except ConnectionSetupError as exc:
            raise BackupError(str(exc)) from exc
        argv, extra_env = native_command(
            tool,
            target,
            ssl_mode=connection._ssl_mode(),
            ca_file=source.options.get("ca_file", ""),
            tunnelled=connection._tunnel is not None,
            iam=bool(source.options.get("iam")),
            custom_format=custom,
            secrets_dir=secrets,
        )
        _cancelled(cancel)
        written = 0
        with (
            open(secrets / "stderr.log", "w+b") as errors,
            _open_binary(part, compress and not custom) as out,
        ):
            process = subprocess.Popen(  # noqa: S603 — argv built above, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errors,
                env={**os.environ, **extra_env},
            )
            with stop_on_cancel(process, cancel):
                try:
                    while chunk := process.stdout.read1(1 << 16):
                        out.write(chunk)
                        written += len(chunk)
                        report(Progress("dumping", size=written))
                except BaseException:
                    stop_process(process)  # a full disk, say: don't leave it dumping
                    raise
                finally:
                    code = process.wait()
            errors.seek(0)
            complaint = errors.read().decode("utf-8", errors="replace").strip()
        _cancelled(cancel)
        if code != 0:
            lines = [line for line in complaint.splitlines() if line.strip()]
            detail = " ".join(lines[-3:]) or f"exit code {code}"
            raise BackupError(f"{tool.name} failed: {detail}")
        warnings = [line.strip() for line in complaint.splitlines() if "warning" in line.lower()][
            :5
        ]
        return BackupResult(part, "native", warnings=warnings)
    finally:
        try:
            connection._stop_tunnel()
        finally:
            shutil.rmtree(secrets, ignore_errors=True)


# ── Built-in export ─────────────────────────────────────────────────

#: Start a new INSERT once this much has been collected, whatever the row
#: count: MySQL refuses a statement larger than max_allowed_packet.
_STATEMENT_BYTES = 512 * 1024
#: SQL Server: end the batch (``GO``) after this many INSERT statements.
_STATEMENTS_PER_BATCH = 20


def _header(source: BackupSource, exporter) -> str:
    lines = [
        "Polyglot AI built-in export",
        f"Connection: {source.name} ({exporter.ENGINE})  {source.where}",
        f"Taken:      {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "",
        "Contains every table and its rows.",
        f"Not included: {exporter.NOT_INCLUDED}",
        "",
        f"Restore into an empty database:  {exporter.RESTORE}",
        "Nothing here drops or replaces anything: loading it into a database that",
        "already has these tables stops with an error.",
    ]
    return "".join(f"-- {line}".rstrip() + "\n" for line in lines) + "\n"


async def _export(
    source: BackupSource, part: Path, compress: bool, report: ProgressFn, cancel
) -> BackupResult:
    from polyglot_ai.core.db_drivers import describe_error
    from polyglot_ai.core.db_explorer import DatabaseConnection
    from polyglot_ai.core.db_export import EXPORTERS

    connection = DatabaseConnection(
        f"{source.name} (backup)",
        source.db_type,
        source.connection_string,
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
                "so the built-in export can't read this database."
            )
        exporter = EXPORTERS[source.db_type](connection)
        try:
            return await _write_export(source, exporter, part, compress, report, cancel)
        except BackupError:
            raise
        except Exception as exc:
            logger.exception("Built-in export of %s failed", source.name)
            raise BackupError(f"The export stopped: {describe_error(exc)}") from exc
    finally:
        await connection.disconnect()


async def _write_export(source, exporter, part, compress, report, cancel) -> BackupResult:
    separator = exporter.BATCH_SEPARATOR
    opening = await exporter.begin()
    try:
        report(Progress("reading"))
        tables = await exporter.tables()
        total_rows = 0
        with _open_binary(part, compress) as binary:
            out = io.TextIOWrapper(binary, encoding="utf-8", newline="\n")

            def statement(sql: str) -> None:
                out.write(sql + ";\n")

            def end_batch() -> None:
                if separator:
                    out.write(separator + "\n")

            out.write(_header(source, exporter))
            for sql in opening:
                statement(sql)
            end_batch()

            for index, table in enumerate(tables, start=1):
                _cancelled(cancel)
                report(Progress("table", table.label, index, len(tables), total_rows))
                out.write(f"\n-- {table.label}\n")
                for sql in table.create:
                    statement(sql)
                    end_batch()
                for sql in table.before_rows:
                    statement(sql)

                head = f"INSERT INTO {table.target} ({', '.join(table.columns)}) VALUES"
                kinds = table.kinds or [""] * len(table.columns)
                pending: list[str] = []
                pending_size = statements = table_rows = 0

                def flush() -> None:
                    nonlocal pending, pending_size, statements
                    if not pending:
                        return
                    if len(pending) == 1:
                        statement(f"{head} {pending[0]}")
                    else:
                        statement(head + "\n" + ",\n".join(pending))
                    pending, pending_size = [], 0
                    statements += 1
                    if separator and statements % _STATEMENTS_PER_BATCH == 0:
                        end_batch()

                async for batch in exporter.rows(table):
                    _cancelled(cancel)
                    for row in batch:
                        values = ", ".join(
                            exporter.literal(value, kind) for value, kind in zip(row, kinds)
                        )
                        pending.append(f"({values})")
                        pending_size += len(values)
                        if (
                            len(pending) >= exporter.ROWS_PER_INSERT
                            or pending_size >= _STATEMENT_BYTES
                        ):
                            flush()
                    table_rows += len(batch)
                    report(
                        Progress("table", table.label, index, len(tables), total_rows + table_rows)
                    )
                flush()
                for sql in table.after_rows:
                    statement(sql)
                end_batch()
                out.write(f"-- {table_rows:,} row{'' if table_rows == 1 else 's'}\n")
                total_rows += table_rows

            out.write("\n")
            for sql in exporter.closing():
                statement(sql)
            end_batch()
            for warning in exporter.warnings:
                out.write(f"-- WARNING: {warning}\n")
            # A file without this line was cut short.
            out.write(
                f"-- End of export: {len(tables)} table{'' if len(tables) == 1 else 's'}, "
                f"{total_rows:,} row{'' if total_rows == 1 else 's'}\n"
            )
            out.flush()
            out.detach()
        return BackupResult(
            part, "builtin", tables=len(tables), rows=total_rows, warnings=list(exporter.warnings)
        )
    finally:
        await exporter.end()


def _run_export(source, part, compress, report, cancel) -> BackupResult:
    # A loop of this thread's own: the app's loop belongs to the GUI
    # thread (and is Qt's), and the drivers' connections are per loop.
    loop = asyncio.SelectorEventLoop()
    try:
        return loop.run_until_complete(_export(source, part, compress, report, cancel))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


# ── Entry point ─────────────────────────────────────────────────────


def run_backup(
    source: BackupSource,
    destination: str | Path,
    *,
    method: str,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> BackupResult:
    """Write a backup of ``source`` to ``destination``. Blocks until done.

    A name ending in ``.gz`` is compressed. Raises :class:`BackupError`
    (or :class:`BackupCancelled`) with nothing left on disk.
    """
    destination = Path(destination).expanduser()
    if destination.is_dir():
        raise BackupError(f"{destination} is a folder. Give the backup a file name.")
    if not destination.parent.is_dir():
        raise BackupError(f"The folder {destination.parent} doesn't exist.")
    report = progress or (lambda _update: None)
    compress = destination.name.lower().endswith(".gz")
    part = destination.with_name(destination.name + ".part")
    started = time.monotonic()
    try:
        if method == "copy":
            if source.db_type != "sqlite":
                raise BackupError("Only a SQLite database can be backed up as a file copy.")
            result = _copy_sqlite(source, part, report, cancel)
        elif method == "native":
            result = _run_native(source, part, compress, report, cancel)
        elif method == "builtin":
            if source.db_type not in ENGINES:
                raise BackupError(f"There is no built-in export for {source.db_type}.")
            result = _run_export(source, part, compress, report, cancel)
        else:
            raise BackupError(f"Unknown backup method: {method}")
        _cancelled(cancel)
        os.replace(part, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    result.path = destination
    result.size = destination.stat().st_size
    result.seconds = time.monotonic() - started
    return result
