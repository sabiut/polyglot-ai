"""Backing up a database: the file copy, the vendor tools, and what is left on disk."""

from __future__ import annotations

import gzip
import os
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from polyglot_ai.core import db_backup
from polyglot_ai.core.db_backup import (
    BackupCancelled,
    BackupError,
    BackupSource,
    Method,
    NativeTool,
    client_arguments,
    default_filename,
    find_tool,
    install_hint,
    methods_for,
    native_command,
    run_backup,
)
from polyglot_ai.core.db_dialects import DbTarget

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and scripts")

PG = NativeTool("pg_dump", "/usr/bin/pg_dump", "postgres", 16)
MYSQL = NativeTool("mysqldump", "/usr/bin/mysqldump", "mysql", 8)
MARIA = NativeTool("mysqldump", "/usr/bin/mysqldump", "mariadb", 11)


def _target(password="s3cret", database="shop", port=5432):
    return DbTarget(
        host="db.example.com", port=port, user="app", password=password, database=database
    )


def _sqlite(path: Path, rows: int = 3) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    connection.executemany("INSERT INTO items (name) VALUES (?)", [(f"n{i}",) for i in range(rows)])
    connection.commit()
    connection.close()
    return path


def _fake_tool(directory: Path, name: str, script: str) -> Path:
    directory.mkdir(exist_ok=True)
    tool = directory / name
    tool.write_text("#!/bin/sh\n" + script)
    tool.chmod(0o755)
    return tool


@pytest.fixture
def no_tools(monkeypatch, tmp_path):
    """A PATH with no database client programs on it."""
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


# ── what is offered ─────────────────────────────────────────────────


def test_sqlite_is_backed_up_as_a_copy():
    (method,) = methods_for("sqlite")
    assert (method.key, method.extension, method.compressible) == ("copy", ".db", False)


@pytest.mark.parametrize("db_type", ["postgresql", "mysql", "mssql", "oracle"])
def test_every_server_engine_has_the_built_in_export(db_type, no_tools):
    methods = methods_for(db_type)
    assert [m.key for m in methods] == ["builtin"]
    assert "Not included" in methods[0].summary


def test_unknown_engine_has_no_methods():
    assert methods_for("dbase") == []


def test_the_vendor_tool_is_offered_first_when_installed(monkeypatch, tmp_path):
    _fake_tool(tmp_path / "bin", "pg_dump", 'echo "pg_dump (PostgreSQL) 16.4 (Debian 16.4-1)"\n')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    methods = methods_for("postgresql")
    assert [m.key for m in methods] == ["native", "builtin"]
    assert "pg_dump" in methods[0].label
    assert install_hint("postgresql") == ""
    assert [m.key for m in methods_for("mssql")] == ["builtin"]


def test_install_hint_only_where_a_tool_exists_and_is_missing(no_tools):
    assert "pg_dump" in install_hint("postgresql")
    assert "mysqldump" in install_hint("mysql")
    assert install_hint("mssql") == install_hint("oracle") == install_hint("sqlite") == ""


@pytest.mark.parametrize(
    ("name", "banner", "flavour", "major"),
    [
        ("pg_dump", "pg_dump (PostgreSQL) 16.4 (Debian 16.4-1.pgdg120+1)", "postgres", 16),
        ("mysqldump", "mysqldump  Ver 8.0.39 for Linux on x86_64 (MySQL Community)", "mysql", 8),
        (
            "mysqldump",
            "mysqldump from 11.8.3-MariaDB, client 10.19 for debian-linux-gnu (x86_64)",
            "mariadb",
            11,
        ),
        ("mariadb-dump", "", "mariadb", 0),
    ],
)
def test_find_tool_reads_flavour_and_version(monkeypatch, tmp_path, name, banner, flavour, major):
    _fake_tool(tmp_path / "bin", name, f'echo "{banner}"\n')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    tool = find_tool((name,))
    assert (tool.name, tool.flavour, tool.major) == (name, flavour, major)


def test_default_filename_is_safe_and_dated():
    method = Method("builtin", "", "", ".sql")
    name = default_filename("prod / orders (eu)", method, when=0)
    assert name.startswith("prod---orders--eu-19") or name.startswith("prod---orders--eu-1970")
    assert name.endswith(".sql") and "/" not in name and " " not in name
    assert default_filename("", method, when=0).startswith("database-")


# ── SQLite ──────────────────────────────────────────────────────────


def test_sqlite_copy_is_complete_private_and_leaves_no_part_file(tmp_path):
    source = BackupSource("shop", "sqlite", str(_sqlite(tmp_path / "shop.db")))
    updates = []
    result = run_backup(source, tmp_path / "copy.db", method="copy", progress=updates.append)

    assert result.path == tmp_path / "copy.db"
    assert (result.method, result.tables) == ("copy", 1)
    assert result.size == result.path.stat().st_size > 0
    copy = sqlite3.connect(result.path)
    assert copy.execute("SELECT count(*) FROM items").fetchone() == (3,)
    copy.close()
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    assert not (tmp_path / "copy.db.part").exists()
    assert {u.stage for u in updates} == {"copying", "checking"}


def test_sqlite_copy_sees_rows_another_connection_has_committed_in_wal_mode(tmp_path):
    path = _sqlite(tmp_path / "shop.db")
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO items (name) VALUES ('late')")
    writer.commit()
    try:
        result = run_backup(
            BackupSource("shop", "sqlite", str(path)), tmp_path / "c.db", method="copy"
        )
    finally:
        writer.close()
    copy = sqlite3.connect(result.path)
    assert copy.execute("SELECT count(*) FROM items").fetchone() == (4,)
    copy.close()


def test_sqlite_copy_never_writes_to_the_source(tmp_path):
    path = _sqlite(tmp_path / "shop.db")
    path.chmod(0o400)
    before = path.read_bytes()
    run_backup(BackupSource("shop", "sqlite", str(path)), tmp_path / "c.db", method="copy")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("location", "message"),
    [(":memory:", "in-memory"), ("{tmp}/missing.db", "isn't there")],
)
def test_sqlite_copy_explains_a_source_it_cannot_read(tmp_path, location, message):
    source = BackupSource("shop", "sqlite", location.replace("{tmp}", str(tmp_path)))
    with pytest.raises(BackupError, match=message):
        run_backup(source, tmp_path / "copy.db", method="copy")
    assert list(tmp_path.iterdir()) == []


def test_a_file_that_is_not_a_database_leaves_nothing_behind(tmp_path):
    (tmp_path / "notes.db").write_text("these are not the pages you are looking for" * 100)
    source = BackupSource("shop", "sqlite", str(tmp_path / "notes.db"))
    with pytest.raises(BackupError, match="Couldn't copy"):
        run_backup(source, tmp_path / "copy.db", method="copy")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.db"]


def test_a_failed_backup_keeps_the_previous_backup_file(tmp_path):
    (tmp_path / "copy.db").write_text("last week's backup")
    source = BackupSource("shop", "sqlite", str(tmp_path / "missing.db"))
    with pytest.raises(BackupError):
        run_backup(source, tmp_path / "copy.db", method="copy")
    assert (tmp_path / "copy.db").read_text() == "last week's backup"


def test_cancel_removes_the_part_file(tmp_path):
    source = BackupSource("shop", "sqlite", str(_sqlite(tmp_path / "shop.db")))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BackupCancelled):
        run_backup(source, tmp_path / "copy.db", method="copy", cancel=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shop.db"]


@pytest.mark.parametrize(
    ("destination", "method", "message"),
    [
        ("{tmp}", "copy", "is a folder"),
        ("{tmp}/nowhere/copy.db", "copy", "doesn't exist"),
        ("{tmp}/copy.db", "teleport", "Unknown backup method"),
        ("{tmp}/copy.db", "builtin", "no built-in export"),
    ],
)
def test_run_backup_refuses_what_it_cannot_do(tmp_path, destination, method, message):
    source = BackupSource("shop", "sqlite", str(_sqlite(tmp_path / "shop.db")))
    with pytest.raises(BackupError, match=message):
        run_backup(source, destination.replace("{tmp}", str(tmp_path)), method=method)


def test_file_copy_is_only_for_sqlite(tmp_path):
    source = BackupSource("pg", "postgresql", "postgresql://a:b@h:5432/d")
    with pytest.raises(BackupError, match="Only a SQLite"):
        run_backup(source, tmp_path / "copy.db", method="copy")


def test_where_never_shows_the_password():
    source = BackupSource("pg", "postgresql", "postgresql://app:hunter2@db.example.com:5432/shop")
    assert source.where == "app@db.example.com:5432/shop"


# ── the vendor tools' command lines ─────────────────────────────────


def test_postgres_password_goes_in_the_environment_not_the_command():
    argv, env = native_command(PG, _target(), ssl_mode="require")
    assert "s3cret" not in " ".join(argv)
    assert env["PGPASSWORD"] == "s3cret"
    assert env["PGSSLMODE"] == "require"
    assert argv[:6] == [
        "/usr/bin/pg_dump",
        "--host",
        "db.example.com",
        "--port",
        "5432",
        "--no-password",
    ]
    assert argv[-5:] == ["--dbname", "shop", "--format=plain", "--no-owner", "--no-privileges"]


def test_postgres_custom_format():
    argv, _env = native_command(PG, _target(), custom_format=True)
    assert "--format=custom" in argv and "--format=plain" not in argv


@pytest.mark.parametrize(("tunnelled", "expected"), [(False, "verify-full"), (True, "verify-ca")])
def test_postgres_verification_checks_the_host_name_only_when_dialled_directly(tunnelled, expected):
    _argv, env = native_command(
        PG, _target(), ssl_mode="verify", ca_file="~/rds.pem", tunnelled=tunnelled
    )
    assert env["PGSSLMODE"] == expected
    assert env["PGSSLROOTCERT"] == str(Path("~/rds.pem").expanduser())


def test_postgres_without_a_password_sets_none():
    _argv, env = native_command(PG, _target(password=""))
    assert "PGPASSWORD" not in env


def test_mysql_password_goes_in_a_private_options_file(tmp_path):
    argv, env = native_command(
        MYSQL, _target(password='pa"ss\\word', port=3306), ssl_mode="require", secrets_dir=tmp_path
    )
    assert env == {}
    assert argv[1] == f"--defaults-extra-file={tmp_path / 'client.cnf'}"  # must be the first option
    assert not any("pa" in part and "ss" in part and "word" in part for part in argv[2:])
    options = tmp_path / "client.cnf"
    assert stat.S_IMODE(options.stat().st_mode) == 0o600
    assert options.read_text() == '[client]\npassword="pa\\"ss\\\\word"\n'
    assert argv[-1] == "shop"
    for flag in (
        "--single-transaction",
        "--routines",
        "--triggers",
        "--hex-blob",
        "--set-gtid-purged=OFF",
        "--no-tablespaces",
        "--column-statistics=0",
        "--ssl-mode=REQUIRED",
        "--protocol=TCP",
    ):
        assert flag in argv


def test_mysql_password_needs_somewhere_private():
    with pytest.raises(BackupError, match="private directory"):
        native_command(MYSQL, _target())


def test_mariadb_tool_gets_mariadb_options(tmp_path):
    argv, _env = native_command(
        MARIA, _target(), ssl_mode="verify", ca_file="/etc/ca.pem", secrets_dir=tmp_path
    )
    assert "--ssl" in argv and "--ssl-ca=/etc/ca.pem" in argv and "--ssl-verify-server-cert" in argv
    for mysql_only in ("--set-gtid-purged=OFF", "--column-statistics=0", "--no-tablespaces"):
        assert mysql_only not in argv
    assert not any(part.startswith("--ssl-mode") for part in argv)


def test_mariadb_through_a_tunnel_does_not_check_the_host_name(tmp_path):
    argv, _env = native_command(
        MARIA, _target(), ssl_mode="verify", tunnelled=True, secrets_dir=tmp_path
    )
    assert "--ssl" in argv and "--ssl-verify-server-cert" not in argv


def test_mysql_iam_token_is_sent_as_cleartext_over_tls(tmp_path):
    argv, _env = client_arguments(
        MYSQL, _target(), ssl_mode="verify", tunnelled=True, iam=True, secrets_dir=tmp_path
    )
    assert "--enable-cleartext-plugin" in argv and "--ssl-mode=VERIFY_CA" in argv


def test_old_mysqldump_is_not_given_an_option_it_lacks(tmp_path):
    old = NativeTool("mysqldump", "/usr/bin/mysqldump", "mysql", 5)
    argv, _env = native_command(old, _target(), secrets_dir=tmp_path)
    assert "--column-statistics=0" not in argv


def test_mysqldump_needs_a_database(tmp_path):
    with pytest.raises(BackupError, match="no database selected"):
        native_command(MYSQL, _target(database=""), secrets_dir=tmp_path)


# ── running a vendor tool ───────────────────────────────────────────

PG_SOURCE = BackupSource("orders", "postgresql", "postgresql://app:s3cret@db.example.com:5432/shop")


def _install_pg_dump(monkeypatch, tmp_path, body: str) -> Path:
    script = 'if [ "$1" = "--version" ]; then echo "pg_dump (PostgreSQL) 16.4"; exit 0; fi\n' + body
    _fake_tool(tmp_path / "bin", "pg_dump", script)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    return tmp_path / "bin"


def test_native_backup_writes_what_the_tool_prints(monkeypatch, tmp_path):
    _install_pg_dump(
        monkeypatch,
        tmp_path,
        'echo "-- PostgreSQL database dump"\necho "args: $*"\necho "pw: $PGPASSWORD"\n',
    )
    updates = []
    result = run_backup(PG_SOURCE, tmp_path / "out.sql", method="native", progress=updates.append)
    text = (tmp_path / "out.sql").read_text()
    assert text.startswith("-- PostgreSQL database dump\n")
    assert "--host db.example.com --port 5432" in text and "--format=plain" in text
    assert "s3cret" not in text.split("pw:")[0]  # not on the command line
    assert "pw: s3cret" in text  # in the tool's environment
    assert (result.method, result.tables, result.rows) == ("native", None, None)
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    stages = [u.stage for u in updates]  # the output may arrive in several pieces
    assert stages[0] == "connecting" and set(stages[1:]) == {"dumping"}
    assert updates[-1].size == len(text.encode())
    assert not (tmp_path / "out.sql.part").exists()


def test_native_backup_compresses_a_gz_name(monkeypatch, tmp_path):
    _install_pg_dump(monkeypatch, tmp_path, 'echo "CREATE TABLE t ();"\n')
    run_backup(PG_SOURCE, tmp_path / "out.sql.gz", method="native")
    assert gzip.decompress((tmp_path / "out.sql.gz").read_bytes()) == b"CREATE TABLE t ();\n"


def test_native_backup_named_dump_asks_for_the_custom_format(monkeypatch, tmp_path):
    _install_pg_dump(monkeypatch, tmp_path, 'echo "$*"\n')
    run_backup(PG_SOURCE, tmp_path / "out.dump", method="native")
    assert "--format=custom" in (tmp_path / "out.dump").read_text()


def test_native_backup_failure_reports_the_tool_and_leaves_nothing(monkeypatch, tmp_path):
    _install_pg_dump(
        monkeypatch,
        tmp_path,
        'echo "partial output"\n'
        'echo "pg_dump: error: connection to server failed: FATAL: no pg_hba.conf entry" >&2\n'
        "exit 1\n",
    )
    with pytest.raises(BackupError, match="pg_dump failed: .*no pg_hba.conf entry"):
        run_backup(PG_SOURCE, tmp_path / "out.sql", method="native")
    assert not (tmp_path / "out.sql").exists() and not (tmp_path / "out.sql.part").exists()


def test_native_backup_passes_on_the_tools_warnings(monkeypatch, tmp_path):
    _install_pg_dump(
        monkeypatch, tmp_path, 'echo "data"\necho "pg_dump: warning: something odd" >&2\n'
    )
    result = run_backup(PG_SOURCE, tmp_path / "out.sql", method="native")
    assert result.warnings == ["pg_dump: warning: something odd"]


def test_native_backup_can_be_cancelled(monkeypatch, tmp_path):
    _install_pg_dump(monkeypatch, tmp_path, 'echo "started"\nexec sleep 30\n')
    cancel = threading.Event()

    def on_progress(update):
        if update.stage == "dumping":
            cancel.set()

    with pytest.raises(BackupCancelled):
        run_backup(
            PG_SOURCE, tmp_path / "out.sql", method="native", progress=on_progress, cancel=cancel
        )
    assert not (tmp_path / "out.sql").exists() and not (tmp_path / "out.sql.part").exists()


def test_cancel_kills_a_dump_tool_that_ignores_being_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(db_backup, "STOP_GRACE_SECONDS", 0.3)
    _install_pg_dump(
        monkeypatch, tmp_path, "trap '' TERM\necho started\nwhile :; do sleep 0.1; done\n"
    )
    cancel = threading.Event()

    def on_progress(update):
        if update.stage == "dumping":
            cancel.set()

    started = time.monotonic()
    with pytest.raises(BackupCancelled):
        run_backup(
            PG_SOURCE, tmp_path / "out.sql", method="native", progress=on_progress, cancel=cancel
        )
    assert time.monotonic() - started < 5
    assert not (tmp_path / "out.sql").exists() and not (tmp_path / "out.sql.part").exists()


def test_a_dump_that_cannot_be_written_stops_the_tool(monkeypatch, tmp_path):
    """An error on our side (a full disk) must not leave the tool running."""
    pid_file = tmp_path / "pid"
    _install_pg_dump(monkeypatch, tmp_path, f"echo $$ > {pid_file}\necho started\nexec sleep 30\n")

    def on_progress(update):
        if update.stage == "dumping":
            raise OSError(28, "No space left on device")

    started = time.monotonic()
    with pytest.raises(OSError, match="No space left"):
        run_backup(PG_SOURCE, tmp_path / "out.sql", method="native", progress=on_progress)
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert not (tmp_path / "out.sql.part").exists()


def test_native_backup_without_the_tool_says_so(no_tools, tmp_path):
    with pytest.raises(BackupError, match="isn't installed"):
        run_backup(PG_SOURCE, tmp_path / "out.sql", method="native")


def test_the_password_file_is_removed_afterwards(monkeypatch, tmp_path):
    seen = tmp_path / "seen"
    script = (
        'if [ "$1" = "--version" ]; then echo "mysqldump  Ver 8.0.39 for Linux"; exit 0; fi\n'
        f'echo "$1" > {seen}\n'
        'echo "-- MySQL dump 10.13"\n'
    )
    _fake_tool(tmp_path / "bin", "mysqldump", script)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    source = BackupSource("shop", "mysql", "mysql://app:s3cret@db.example.com:3306/shop")
    run_backup(source, tmp_path / "out.sql", method="native")
    option = seen.read_text().strip()
    assert option.startswith("--defaults-extra-file=")
    assert not os.path.exists(option.split("=", 1)[1])


def test_part_file_name_does_not_hide_the_compression_choice(tmp_path):
    """The .part suffix is added after .gz, so the compressed flag still comes from the name."""
    source = BackupSource("shop", "sqlite", str(_sqlite(tmp_path / "shop.db")))
    result = run_backup(source, tmp_path / "copy.db", method="copy")
    assert result.path.read_bytes().startswith(b"SQLite format 3\x00")
    assert db_backup._pg_sslmode(None, False) == ""
