"""The built-in export: how each engine's values and tables are written as SQL."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import gzip
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from polyglot_ai.core import db_backup, db_export
from polyglot_ai.core.db_backup import (
    BackupCancelled,
    BackupError,
    BackupSource,
    _write_export,
    run_backup,
)
from polyglot_ai.core.db_export import (
    EXPORTERS,
    Exporter,
    MssqlExporter,
    MySQLExporter,
    OracleExporter,
    PostgresExporter,
    TableSpec,
    mssql_type,
    oracle_type,
    quote_string,
)

UTC = dt.timezone.utc


def test_every_server_engine_has_an_exporter():
    from polyglot_ai.core.db_dialects import ENGINES

    assert set(EXPORTERS) == set(ENGINES)


def test_quote_string_doubles_quotes():
    assert quote_string("O'Brien") == "'O''Brien'"


# ── literals ────────────────────────────────────────────────────────


class TestPostgresLiterals:
    def test_everything_is_quoted_text_or_null(self):
        exporter = PostgresExporter(None)
        assert exporter.literal(None) == "NULL"
        assert exporter.literal("{a,b}") == "'{a,b}'"
        assert exporter.literal("it's") == "'it''s'"
        # A backslash is itself: the file sets standard_conforming_strings.
        assert exporter.literal("a\\b") == "'a\\b'"


class _Pg:
    def __init__(self, extensions):
        self.extensions, self.released = extensions, False

    def transaction(self, **options):
        self.options = options

        class Transaction:
            async def start(self):
                pass

            async def rollback(self):
                pass

        return Transaction()

    async def fetch(self, sql):
        assert "pg_extension" in sql
        return [(name,) for name in self.extensions]


async def test_postgres_creates_extensions_inside_the_transaction():
    """Before BEGIN, a load that failed later left the extensions behind."""
    pg = _Pg(["citext", 'odd"name'])

    class Pool:
        async def acquire(self):
            return pg

        async def release(self, connection):
            pg.released = True

    class Connection:
        _pg_pool = Pool()

    exporter = PostgresExporter(Connection())
    opening = await exporter.begin()
    begin = opening.index("BEGIN")
    assert opening[begin + 1 :] == [
        'CREATE EXTENSION IF NOT EXISTS "citext"',
        'CREATE EXTENSION IF NOT EXISTS "odd""name"',
    ]
    # Only session settings come before it — nothing that changes the database.
    assert all(statement.startswith("SET ") for statement in opening[:begin])
    assert exporter.closing() == ["COMMIT"]
    assert pg.options == {"isolation": "repeatable_read", "readonly": True}
    await exporter.end()
    assert pg.released


class TestMySQLLiterals:
    exporter = MySQLExporter(None)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "NULL"),
            (True, "1"),
            (7, "7"),
            (decimal.Decimal("10.50"), "10.50"),
            (decimal.Decimal("1E+3"), "1000"),
            (float("nan"), "NULL"),
            (b"\x00\xff", "0x00ff"),
            (b"", "''"),
            (dt.date(2026, 2, 3), "'2026-02-03'"),
            (dt.datetime(2026, 2, 3, 4, 5, 6, 700), "'2026-02-03 04:05:06.000700'"),
            (dt.timedelta(hours=-30, minutes=-5), "'-30:05:00'"),
            (dt.timedelta(hours=1, microseconds=5), "'01:00:00.000005'"),
            ({"b", "a"}, "'a,b'"),
        ],
    )
    def test_values(self, value, expected):
        assert self.exporter.literal(value) == expected

    def test_strings_use_backslash_escapes(self):
        assert self.exporter.literal("a'b\\c\nd\0") == "'a\\'b\\\\c\\nd\\0'"

    def test_generated_columns_are_recognised(self):
        assert MySQLExporter._generated("STORED GENERATED")
        assert MySQLExporter._generated("VIRTUAL GENERATED")
        assert MySQLExporter._generated("PERSISTENT")  # MariaDB
        assert not MySQLExporter._generated("auto_increment")
        assert not MySQLExporter._generated(
            "DEFAULT_GENERATED"
        )  # a default, not a generated column
        assert not MySQLExporter._generated(None)


class TestMssqlLiterals:
    exporter = MssqlExporter(None)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "NULL"),
            (False, "0"),
            (decimal.Decimal("-0.10"), "-0.10"),
            (b"\x01\x02", "0x0102"),
            (b"", "0x"),
            (dt.date(2026, 2, 3), "CAST('2026-02-03' AS date)"),
            (dt.datetime(2026, 2, 3, 4, 5, 6), "CAST('2026-02-03T04:05:06' AS datetime2)"),
            (
                dt.datetime(2026, 2, 3, 4, 5, 6, tzinfo=dt.timezone(dt.timedelta(hours=-5))),
                "CAST('2026-02-03T04:05:06-05:00' AS datetimeoffset)",
            ),
            (dt.time(4, 5, 6), "CAST('04:05:06' AS time)"),
            (uuid.UUID(int=1), "'00000000-0000-0000-0000-000000000001'"),
            ("naïve", "N'naïve'"),
        ],
    )
    def test_values(self, value, expected):
        assert self.exporter.literal(value) == expected

    def test_a_precise_time_read_as_text_is_written_as_it_came(self):
        assert self.exporter.literal("2026-02-03T04:05:06.1234567", "iso") == (
            "'2026-02-03T04:05:06.1234567'"
        )

    def test_a_line_break_never_reaches_the_file(self):
        """sqlcmd ends a batch at a line that says GO, even inside a string."""
        literal = self.exporter.literal("first\nGO\r\nlast")
        assert "\n" not in literal and "\r" not in literal
        assert literal == (
            "CAST(N'first' AS nvarchar(max)) + NCHAR(10) + N'GO' + NCHAR(13) + NCHAR(10) + N'last'"
        )

    def test_a_trailing_line_break_adds_no_empty_piece(self):
        assert self.exporter.literal("x\n") == "CAST(N'x' AS nvarchar(max)) + NCHAR(10)"


class TestOracleLiterals:
    def setup_method(self):
        self.exporter = OracleExporter(None)

    @pytest.mark.parametrize(
        ("value", "kind", "expected"),
        [
            (None, "", "NULL"),
            ("", "", "NULL"),
            ("12.50", "number", "12.50"),
            ("-.5", "number", "-.5"),
            ("1E+40", "number", "'1E+40'"),
            (3, "", "3"),
            (b"\xca\xfe", "", "HEXTORAW('cafe')"),
            (dt.date(2026, 2, 3), "", "DATE '2026-02-03'"),
            (dt.datetime(2026, 2, 3, 4, 5, 6), "", "TIMESTAMP '2026-02-03 04:05:06'"),
            (
                dt.datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
                "",
                "TIMESTAMP '2026-02-03 04:05:06 +00:00'",
            ),
            ("it's", "", "'it''s'"),
            ("a\nb", "", "'a' || CHR(10) || 'b'"),
        ],
    )
    def test_values(self, value, kind, expected):
        assert self.exporter.literal(value, kind) == expected
        assert self.exporter.warnings == []

    def test_every_kind_of_line_break_comes_back_as_it_was(self):
        """CR LF used to be written as LF: two different values, one backup."""
        literal = self.exporter.literal
        assert literal("a\r\nb") == "'a' || CHR(13) || CHR(10) || 'b'"
        assert literal("a\nb") == "'a' || CHR(10) || 'b'"
        assert literal("a\rb") == "'a' || CHR(13) || 'b'"
        assert literal("a\n\r\n\rb") == "'a' || CHR(10) || CHR(13) || CHR(10) || CHR(13) || 'b'"
        assert literal("\r\n") == "CHR(13) || CHR(10)"
        assert literal("x\r") == "'x' || CHR(13)"
        values = ["a\r\nb", "a\nb", "a\rb", "a\n\rb", "ab"]
        assert len({literal(v) for v in values}) == len(values)
        assert not any("\r" in literal(v) or "\n" in literal(v) for v in values)

    def test_long_text_keeps_its_line_breaks_too(self):
        literal = self.exporter.literal("x" * 1500 + "\r\n" + "y")
        assert literal == " || ".join(
            [
                "TO_CLOB('" + "x" * 1000 + "')",
                "TO_CLOB('" + "x" * 500 + "')",
                "TO_CLOB(CHR(13) || CHR(10))",
                "TO_CLOB('y')",
            ]
        )

    def test_long_text_is_cut_into_pieces_oracle_accepts(self):
        literal = self.exporter.literal("x" * 2500)
        pieces = literal.split(" || ")
        assert len(pieces) == 3
        assert all(piece.startswith("TO_CLOB('") for piece in pieces)
        assert max(len(piece) for piece in pieces) < 4000

    def test_big_binary_is_null_with_one_warning(self):
        self.exporter._table = "DOCS"
        assert self.exporter.literal(b"x" * 2001) == "NULL"
        assert self.exporter.literal(b"y" * 5000) == "NULL"
        assert len(self.exporter.warnings) == 1
        assert "DOCS" in self.exporter.warnings[0]

    def test_an_unknown_type_is_null_with_a_warning(self):
        assert self.exporter.literal(object()) == "NULL"
        assert "object" in self.exporter.warnings[0]


# ── types ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("varchar", 50, 0, 0), "varchar(50)"),
        (("varchar", -1, 0, 0), "varchar(max)"),
        (("nvarchar", 100, 0, 0), "nvarchar(50)"),
        (("nvarchar", -1, 0, 0), "nvarchar(max)"),
        (("decimal", 9, 12, 4), "decimal(12, 4)"),
        (("datetime2", 8, 27, 7), "datetime2(7)"),
        (("float", 4, 24, 0), "real"),
        (("float", 8, 53, 0), "float"),
        (("timestamp", 8, 0, 0), "rowversion"),
        (("int", 4, 10, 0), "int"),
        (("geography", -1, 0, 0), "geography"),
    ],
)
def test_mssql_type(args, expected):
    assert mssql_type(*args) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("VARCHAR2", 200, 50, "C", None, None), "VARCHAR2(50 CHAR)"),
        (("VARCHAR2", 200, 200, "B", None, None), "VARCHAR2(200 BYTE)"),
        (("NVARCHAR2", 80, 40, "C", None, None), "NVARCHAR2(40)"),
        (("NUMBER", 22, None, None, 10, 2), "NUMBER(10, 2)"),
        (("NUMBER", 22, None, None, 10, None), "NUMBER(10, 0)"),
        (("NUMBER", 22, None, None, None, None), "NUMBER"),
        (("FLOAT", 22, None, None, 126, None), "FLOAT(126)"),
        (("RAW", 16, None, None, None, None), "RAW(16)"),
        (("TIMESTAMP(6) WITH TIME ZONE", 13, None, None, None, 6), "TIMESTAMP(6) WITH TIME ZONE"),
        (("CLOB", 4000, None, None, None, None), "CLOB"),
    ],
)
def test_oracle_type(args, expected):
    assert oracle_type(*args) == expected


# ── the file ────────────────────────────────────────────────────────


class _FakeExporter(Exporter):
    ENGINE = "PostgreSQL"
    NOT_INCLUDED = "views."
    RESTORE = "psql"

    def __init__(self, tables, rows, *, separator="", per_insert=2):
        super().__init__(None)
        self._tables, self._rows = tables, rows
        self.BATCH_SEPARATOR = separator
        self.ROWS_PER_INSERT = per_insert
        self.ended = False

    async def begin(self):
        return ["BEGIN"]

    async def tables(self):
        return self._tables

    async def rows(self, table):
        for batch in self._rows.get(table.label, []):
            yield batch

    def literal(self, value, kind=""):
        return "NULL" if value is None else quote_string(str(value))

    def closing(self):
        return ["COMMIT"]

    async def end(self):
        self.ended = True


def _table(label, columns=("a", "b")):
    return TableSpec(
        label=label,
        target=f'"{label}"',
        create=[f'CREATE TABLE "{label}" (a int, b text)'],
        columns=list(columns),
        select="",
    )


SOURCE = BackupSource("shop", "postgresql", "postgresql://app:secret@db.example.com:5432/shop")


def _write(tmp_path, exporter, *, compress=False, cancel=None, updates=None):
    part = tmp_path / ("out.sql.gz.part" if compress else "out.sql.part")
    report = (updates.append) if updates is not None else (lambda _u: None)
    result = asyncio.run(_write_export(SOURCE, exporter, part, compress, report, cancel))
    data = gzip.decompress(part.read_bytes()) if compress else part.read_bytes()
    return result, data.decode()


def test_export_file_has_header_tables_rows_and_closing_line(tmp_path):
    exporter = _FakeExporter(
        [_table("orders"), _table("empty")],
        {"orders": [[(1, "x"), (2, None)], [(3, "it's")]]},
    )
    updates = []
    result, text = _write(tmp_path, exporter, updates=updates)

    assert text.startswith("-- Polyglot AI built-in export\n")
    assert "-- Connection: shop (PostgreSQL)  app@db.example.com:5432/shop" in text
    assert "secret" not in text
    assert "-- Not included: views." in text
    body = text.split("\n\n", 1)[1]
    assert body.startswith("BEGIN;\n")
    assert 'CREATE TABLE "orders" (a int, b text);' in text
    # Two rows per INSERT, the odd one alone.
    assert "INSERT INTO \"orders\" (a, b) VALUES\n('1', 'x'),\n('2', NULL);\n" in text
    assert "INSERT INTO \"orders\" (a, b) VALUES ('3', 'it''s');\n" in text
    assert "-- 3 rows\n" in text and "-- 0 rows\n" in text
    assert text.endswith("COMMIT;\n-- End of export: 2 tables, 3 rows\n")
    assert (result.tables, result.rows, result.method) == (2, 3, "builtin")
    assert exporter.ended
    assert [u.stage for u in updates][:2] == ["reading", "table"]
    assert updates[-1].rows == 3 and updates[-1].total == 2


def test_export_is_readable_gzipped(tmp_path):
    exporter = _FakeExporter([_table("t")], {"t": [[(1, "é")]]})
    _result, text = _write(tmp_path, exporter, compress=True)
    assert "('1', 'é')" in text
    assert text.endswith("-- End of export: 1 table, 1 row\n")


def test_batch_separator_follows_ddl_and_groups_of_inserts(tmp_path):
    rows = [[(n, "v")] for n in range(45)]
    exporter = _FakeExporter([_table("t")], {"t": rows}, separator="GO", per_insert=1)
    _result, text = _write(tmp_path, exporter)
    lines = text.splitlines()
    create = lines.index('CREATE TABLE "t" (a int, b text);')
    assert lines[create + 1] == "GO"
    # 45 single-row INSERTs: a GO after the 20th and 40th, and one to finish.
    inserts_between = []
    count = 0
    for line in lines[create + 2 :]:
        if line.startswith("INSERT"):
            count += 1
        elif line == "GO":
            inserts_between.append(count)
            count = 0
    assert inserts_between[:3] == [20, 20, 5]


def test_a_statement_is_cut_when_it_grows_large(tmp_path, monkeypatch):
    monkeypatch.setattr(db_backup, "_STATEMENT_BYTES", 30)
    exporter = _FakeExporter([_table("t")], {"t": [[(1, "x" * 40), (2, "y")]]}, per_insert=500)
    _result, text = _write(tmp_path, exporter)
    assert text.count("INSERT INTO") == 2


def test_warnings_are_written_into_the_file(tmp_path):
    exporter = _FakeExporter([_table("t")], {})
    exporter.warn("t: something was left out")
    result, text = _write(tmp_path, exporter)
    assert "-- WARNING: t: something was left out\n" in text
    assert result.warnings == ["t: something was left out"]


def test_cancel_stops_between_tables_and_releases_the_connection(tmp_path):
    import threading

    cancel = threading.Event()
    exporter = _FakeExporter([_table("a"), _table("b")], {})
    original = exporter.rows

    async def rows(table):
        cancel.set()
        async for batch in original(table):
            yield batch

    exporter.rows = rows
    with pytest.raises(BackupCancelled):
        _write(tmp_path, exporter, cancel=cancel)
    assert exporter.ended


# ── MySQL and Oracle against scripted drivers ───────────────────────


class _MyCursor:
    def __init__(self, connection, streaming=False):
        self._c = connection
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql):
        self._c.executed.append(sql)
        for prefix, rows in self._c.answers.items():
            if sql.startswith(prefix):
                self._rows = list(rows)
                return
        self._rows = []

    async def fetchall(self):
        return self._rows

    async def fetchmany(self, size):
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch


class _MyConnection:
    def __init__(self, answers):
        self.answers = answers
        self.executed = []

    async def ping(self, reconnect=True):
        pass

    def cursor(self, kind=None):
        return _MyCursor(self)


def _mysql_exporter(answers):
    class Pool:
        _connection = _MyConnection(answers)

    class Connection:
        _mysql_pool = Pool()

    return MySQLExporter(Connection()), Pool._connection


def test_mysql_export_reads_a_snapshot_and_skips_generated_columns(tmp_path):
    pytest.importorskip("aiomysql")
    ddl = "CREATE TABLE `orders` (\n  `id` int NOT NULL AUTO_INCREMENT,\n  PRIMARY KEY (`id`)\n)"
    exporter, driver = _mysql_exporter(
        {
            "SELECT DATABASE()": [("shop",)],
            "SELECT TABLE_NAME FROM information_schema.TABLES": [("orders",)],
            "SELECT TABLE_NAME, COLUMN_NAME, EXTRA": [
                ("orders", "id", "auto_increment"),
                ("orders", "total", ""),
                ("orders", "total_with_tax", "STORED GENERATED"),
                ("a_view", "id", ""),
            ],
            "SHOW CREATE TABLE `orders`": [("orders", ddl)],
            "SELECT `id`, `total` FROM `orders`": [(0, decimal.Decimal("9.99")), (2, None)],
        }
    )
    source = BackupSource("shop", "mysql", "mysql://app:pw@db:3306/shop")
    part = tmp_path / "out.sql.part"
    result = asyncio.run(_write_export(source, exporter, part, False, lambda _u: None, None))
    text = part.read_text()

    assert driver.executed.index(
        "START TRANSACTION WITH CONSISTENT SNAPSHOT"
    ) < driver.executed.index("SELECT `id`, `total` FROM `orders`")
    assert driver.executed[-1] == "COMMIT"
    assert ddl + ";\n" in text
    assert "INSERT INTO `orders` (`id`, `total`) VALUES\n(0, 9.99),\n(2, NULL);\n" in text
    assert "total_with_tax" not in text.replace(ddl, "")
    assert "SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO'" in text  # the row with id 0 keeps it
    assert "FOREIGN_KEY_CHECKS = 0" in text
    assert text.index("FOREIGN_KEY_CHECKS = 0") < text.index("CREATE TABLE")
    assert "SET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;" in text.split("-- 2 rows")[1]
    assert (result.tables, result.rows) == (1, 2)


def test_mysql_export_refuses_a_connection_with_no_database(tmp_path):
    exporter, _driver = _mysql_exporter({"SELECT DATABASE()": [(None,)]})
    with pytest.raises(ValueError, match="no database selected"):
        asyncio.run(exporter.begin())


class _OraCursor:
    def __init__(self, connection):
        self._c = connection
        self._rows = []
        self.arraysize = 100
        self.outputtypehandler = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self._c.executed.append(sql)
        self._rows = []
        for prefix, rows in self._c.answers.items():
            if sql.startswith(prefix):
                self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchmany(self, size):
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch


class _OraConnection:
    def __init__(self, answers):
        self.answers = answers
        self.executed = []
        self.autocommit = True
        self.rollbacks = 0

    def cursor(self):
        return _OraCursor(self)

    def rollback(self):
        self.rollbacks += 1


def test_oracle_export_writes_tables_keys_and_one_insert_per_row(tmp_path):
    driver = _OraConnection(
        {
            "SELECT table_name FROM user_tables": [("CASES",)],
            "SELECT table_name, column_name, data_type": [
                ("CASES", "ID", "NUMBER", 22, None, None, 10, 0, "N"),
                ("CASES", "TITLE", "VARCHAR2", 400, 100, "C", None, None, "Y"),
                ("CASES", "OPENED", "DATE", 7, None, None, None, None, "Y"),
                ("OTHER_OWNER_VIEW", "X", "NUMBER", 22, None, None, None, None, "Y"),
            ],
            "SELECT uc.table_name, uc.constraint_name": [("CASES", "SYS_C0012", "ID")],
            'SELECT "ID", "TITLE", "OPENED" FROM "CASES"': [
                ("1", "A & B", dt.datetime(2026, 1, 2)),
                ("2", "", None),
            ],
        }
    )

    class Driver:
        _conn = driver

    class Connection:
        _driver = Driver()

    exporter = OracleExporter(Connection())
    source = BackupSource("legal", "oracle", "oracle://app:pw@db:1521/XEPDB1")
    part = tmp_path / "out.sql.part"
    result = asyncio.run(_write_export(source, exporter, part, False, lambda _u: None, None))
    text = part.read_text()

    assert driver.executed[0] == "SET TRANSACTION READ ONLY"
    assert "SET DEFINE OFF;\nSET SQLBLANKLINES ON;\n" in text
    assert (
        'CREATE TABLE "CASES" (\n'
        '    "ID" NUMBER(10, 0) NOT NULL,\n'
        '    "TITLE" VARCHAR2(100 CHAR),\n'
        '    "OPENED" DATE,\n'
        '    PRIMARY KEY ("ID")\n'
        ");\n"
    ) in text
    assert "SYS_C0012" not in text  # a generated constraint name can't be reused
    insert = 'INSERT INTO "CASES" ("ID", "TITLE", "OPENED") VALUES'
    assert f"{insert} (1, 'A & B', TIMESTAMP '2026-01-02 00:00:00');\n" in text
    assert f"{insert} (2, NULL, NULL);\n" in text
    assert text.endswith("COMMIT;\n-- End of export: 1 table, 2 rows\n")
    assert result.rows == 2
    # The read-only transaction is ended and the connection handed back as it was.
    assert driver.rollbacks == 2 and driver.autocommit is True


# ── Cancel while the server is working ──────────────────────────────


class _Connection:
    """Stands in for DatabaseConnection under run_backup."""

    instances: list = []
    _pg_pool = _mysql_pool = _driver = object()
    connect_blocks = False
    disconnect_takes = 0.0

    def __init__(self, name, db_type, connection_string, read_only=True, options=None):
        self.disconnected = threading.Event()
        self.tunnel_stopped = False
        _Connection.instances.append(self)

    async def connect(self):
        if _Connection.connect_blocks:
            await asyncio.Event().wait()
        return True, "ok"

    async def disconnect(self):
        if _Connection.disconnect_takes:
            await asyncio.sleep(_Connection.disconnect_takes)
        self.disconnected.set()

    def _stop_tunnel(self):
        self.tunnel_stopped = True


@pytest.fixture
def engine(monkeypatch):
    """run_backup's built-in export with the connection and the exporter replaced."""
    from polyglot_ai.core import db_explorer

    _Connection.instances, _Connection.connect_blocks = [], False
    _Connection.disconnect_takes = 0.0
    monkeypatch.setattr(db_explorer, "DatabaseConnection", _Connection)

    def use(exporter):
        monkeypatch.setitem(db_export.EXPORTERS, "postgresql", lambda _connection: exporter)
        return exporter

    return use


def _cancelled_backup(tmp_path, after=0.3, updates=None):
    """A backup with Cancel pressed ``after`` seconds in → seconds it took past Cancel."""
    cancel = threading.Event()
    timer = threading.Timer(after, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(BackupCancelled):
            run_backup(
                SOURCE,
                tmp_path / "out.sql",
                method="builtin",
                cancel=cancel,
                progress=updates.append if updates is not None else None,
            )
    finally:
        timer.cancel()
    return time.monotonic() - started - after


def _open_part_files() -> list[str]:
    """This process's open files that are a backup being written (Linux only)."""
    fds = Path("/proc/self/fd")
    if not fds.is_dir():
        return []
    found = []
    for entry in fds.iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if ".sql.part" in target:
            found.append(target)
    return found


class _StuckExporter(_FakeExporter):
    """Its second table never answers: the query is waiting for a lock."""

    def __init__(self, *, blocking=False, honours_interrupt=True):
        super().__init__([_table("a"), _table("b")], {"a": [[(1, "x")]]})
        self.BLOCKING = blocking
        self._honours = honours_interrupt
        self.released = threading.Event()
        self.interrupts = 0
        self.thread_names = set()

    async def rows(self, table):
        self.thread_names.add(threading.current_thread().name)
        if table.label == "b":
            if self.BLOCKING:  # pymssql, oracledb: the whole thread waits
                self.released.wait(30)
                raise RuntimeError("Operation cancelled by user")
            await asyncio.Event().wait()
        async for batch in super().rows(table):
            yield batch

    def interrupt(self):
        self.interrupts += 1
        if self._honours:
            self.released.set()


def test_a_built_in_backup_still_returns_its_result_and_its_errors(tmp_path, engine):
    exporter = engine(_FakeExporter([_table("a")], {"a": [[(1, "x"), (2, "y")]]}))
    result = run_backup(SOURCE, tmp_path / "out.sql", method="builtin", cancel=threading.Event())
    assert (result.method, result.tables, result.rows) == ("builtin", 1, 2)
    assert (tmp_path / "out.sql").read_text().rstrip().endswith("-- End of export: 1 table, 2 rows")
    assert exporter.ended and _Connection.instances[0].disconnected.is_set()

    class Broken(_FakeExporter):
        async def tables(self):
            raise RuntimeError("permission denied for table secrets")

    engine(Broken([], {}))
    with pytest.raises(BackupError, match="The export stopped: permission denied for table"):
        run_backup(SOURCE, tmp_path / "other.sql", method="builtin")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.sql"]


def test_cancel_reaches_an_export_whose_query_is_waiting_for_a_lock(tmp_path, engine):
    """Cancel was only looked at between batches of rows: this export never came back."""
    exporter = engine(_StuckExporter())
    late = _cancelled_backup(tmp_path)

    assert late < 2
    assert exporter.ended and exporter.interrupts == 0  # an awaited call is simply cancelled
    assert exporter.thread_names == {"db-backup-export"}
    assert _Connection.instances[0].disconnected.is_set()
    assert list(tmp_path.iterdir()) == []


def test_a_blocking_driver_is_interrupted_once_it_has_had_its_moment(
    tmp_path, engine, monkeypatch, caplog
):
    monkeypatch.setattr(db_backup, "EXPORT_NOTICE_SECONDS", 0.3)
    exporter = engine(_StuckExporter(blocking=True))
    with caplog.at_level("ERROR"):
        late = _cancelled_backup(tmp_path)

    # What the driver raised on being interrupted is the cancel, not a failure to log.
    assert [r.getMessage() for r in caplog.records] == []

    assert 0.25 < late < 2
    assert exporter.interrupts == 1 and exporter.ended
    assert _Connection.instances[0].disconnected.is_set()
    assert not _Connection.instances[0].tunnel_stopped
    assert list(tmp_path.iterdir()) == []


def test_a_blocking_driver_that_is_reading_rows_is_not_interrupted(tmp_path, engine):
    """Cancelling across threads is for a driver stuck in a call, not one busy reading."""

    class Flowing(_StuckExporter):
        async def rows(self, table):
            for _ in range(2000):
                time.sleep(0.01)  # the driver, fetching a batch
                yield [(1, "x")]

    exporter = engine(Flowing(blocking=True))
    late = _cancelled_backup(tmp_path)

    assert late < 0.9  # noticed by itself, well inside its moment
    assert exporter.interrupts == 0 and exporter.ended
    assert list(tmp_path.iterdir()) == []


def test_an_export_that_will_not_stop_is_left_behind_and_cleans_up_later(
    tmp_path, engine, monkeypatch
):
    monkeypatch.setattr(db_backup, "EXPORT_NOTICE_SECONDS", 0.1)
    monkeypatch.setattr(db_backup, "EXPORT_STOP_SECONDS", 0.3)
    exporter = engine(_StuckExporter(blocking=True, honours_interrupt=False))
    updates: list = []
    late = _cancelled_backup(tmp_path, updates=updates)

    assert 0.35 < late < 2  # bounded: its moment, the wait, and no longer
    connection = _Connection.instances[0]
    assert exporter.interrupts == 1
    assert connection.tunnel_stopped  # the last thing that can end the call
    assert not connection.disconnected.is_set()
    assert list(tmp_path.iterdir()) == []
    # Its file is closed as well, not only unlinked: Windows can't delete an open file.
    assert _open_part_files() == []
    reported = len(updates)

    exporter.released.set()  # the server answers at last
    assert connection.disconnected.wait(5)
    assert exporter.ended
    assert len(updates) == reported  # nobody is told about progress any more
    assert list(tmp_path.iterdir()) == []


def test_an_export_left_behind_reports_no_more_progress(tmp_path, engine, monkeypatch):
    """The dialog that was listening has moved on, perhaps to another backup."""
    monkeypatch.setattr(db_backup, "EXPORT_NOTICE_SECONDS", 0.1)
    monkeypatch.setattr(db_backup, "EXPORT_STOP_SECONDS", 0.3)

    class StuckAtTheStart(_StuckExporter):
        async def begin(self):
            self.released.wait(30)  # and then carries on as if nothing had happened
            return ["BEGIN"]

    exporter = engine(StuckAtTheStart(blocking=True, honours_interrupt=False))
    updates: list = []
    _cancelled_backup(tmp_path, updates=updates)
    assert [u.stage for u in updates] == ["connecting"]

    exporter.released.set()
    assert _Connection.instances[0].disconnected.wait(5)
    assert [u.stage for u in updates] == ["connecting"]  # not "reading"
    assert list(tmp_path.iterdir()) == []


def test_cancel_while_connecting_closes_what_was_opened(tmp_path, engine):
    engine(_FakeExporter([], {}))
    _Connection.connect_blocks = True
    late = _cancelled_backup(tmp_path)

    assert late < 2
    assert _Connection.instances[0].disconnected.is_set()
    assert list(tmp_path.iterdir()) == []


def test_cancel_during_the_closing_down_does_not_cut_it_short(tmp_path, engine):
    """The connection (and its tunnel) is closed to the end even if Cancel lands just then."""

    class Broken(_FakeExporter):
        async def tables(self):
            raise RuntimeError("permission denied")

    engine(Broken([], {}))
    _Connection.disconnect_takes = 0.6  # Cancel at 0.3 s arrives in the middle of it
    _cancelled_backup(tmp_path)

    assert _Connection.instances[0].disconnected.is_set()


@pytest.mark.parametrize("exporter_type", [MssqlExporter, OracleExporter])
def test_the_blocking_exporters_interrupt_through_their_driver(exporter_type):
    calls = []

    class Low:
        def cancel(self):
            calls.append("cancel")

    class Raw(Low):
        _conn = Low()  # pymssql keeps the FreeTDS connection here
        autocommit = True

        def rollback(self): ...

        def cursor(self):
            class Cursor:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def execute(self, sql): ...

            return Cursor()

    class Driver:
        _conn = Raw()

    class Saved:
        _driver = Driver()

    exporter = exporter_type(Saved())
    assert exporter.BLOCKING
    exporter.interrupt()  # before it has begun: nothing to stop
    assert calls == []
    asyncio.run(exporter.begin())
    exporter.interrupt()
    assert calls == ["cancel"]


def test_the_awaiting_exporters_need_no_interrupt():
    assert not PostgresExporter.BLOCKING and not MySQLExporter.BLOCKING


def test_postgres_drops_its_connection_when_the_rollback_is_not_answered(monkeypatch):
    monkeypatch.setattr(db_export, "END_SECONDS", 0.2)
    seen = []

    class Transaction:
        async def rollback(self):
            await asyncio.Event().wait()  # the cancel request never arrived

    class Pg:
        def terminate(self):
            seen.append("terminate")

    class Pool:
        async def release(self, conn):
            seen.append("release")

    class Saved:
        _pg_pool = Pool()

    exporter = PostgresExporter(Saved())
    exporter._pg, exporter._transaction = Pg(), Transaction()
    started = time.monotonic()
    asyncio.run(exporter.end())
    assert seen == ["terminate", "release"] and time.monotonic() - started < 2
