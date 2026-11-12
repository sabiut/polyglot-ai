"""Listing a server's databases, and reading the tables of one that isn't connected."""

from __future__ import annotations

import sqlite3

import pytest

from polyglot_ai.core import db_catalog
from polyglot_ai.core.db_catalog import can_query_across, sorted_for_display
from polyglot_ai.core.db_dialects import DbTarget, preview_query, quote_table
from polyglot_ai.core.db_drivers import MssqlDriver, OracleDriver
from polyglot_ai.core.db_explorer import DatabaseConnection, DatabaseInfo

# ── naming a table in another database ──────────────────────────────


@pytest.mark.parametrize(
    ("db_type", "table", "database", "expected"),
    [
        ("mssql", "cases", "", "[cases]"),
        ("mssql", "hr.people", "", "[hr].[people]"),
        ("mssql", "cases", "archive", "[archive].[dbo].[cases]"),
        ("mssql", "hr.people", "odd]name", "[odd]]name].[hr].[people]"),
        ("mysql", "orders", "", "`orders`"),
        ("mysql", "orders", "shop`2", "`shop``2`.`orders`"),
        # These engines can't reach another database: the name stays as it is.
        ("postgresql", "orders", "other", '"orders"'),
        ("oracle", "ORDERS", "other", '"ORDERS"'),
        ("sqlite", "orders", "other", '"orders"'),
    ],
)
def test_quote_table(db_type, table, database, expected):
    assert quote_table(db_type, table, database) == expected


def test_preview_query_reaches_into_another_database():
    assert preview_query("mssql", "cases", database="archive") == (
        "SELECT TOP 100 * FROM [archive].[dbo].[cases]"
    )
    assert preview_query("mysql", "orders", 5, database="shop") == (
        "SELECT * FROM `shop`.`orders` LIMIT 5"
    )
    assert preview_query("mysql", "orders") == "SELECT * FROM `orders` LIMIT 100"


def test_only_mysql_and_sql_server_query_across_databases():
    assert [
        t for t in ("sqlite", "postgresql", "mysql", "mssql", "oracle") if can_query_across(t)
    ] == [
        "mysql",
        "mssql",
    ]


def test_connected_database_first_then_the_users_then_the_engines_own():
    listed = sorted_for_display(
        [
            DatabaseInfo("master", system=True),
            DatabaseInfo("Zeta"),
            DatabaseInfo("shop", current=True),
            DatabaseInfo("alpha"),
            DatabaseInfo("msdb", system=True),
        ]
    )
    assert [d.name for d in listed] == ["shop", "alpha", "Zeta", "master", "msdb"]


# ── SQLite ──────────────────────────────────────────────────────────


async def test_sqlite_is_one_database_named_after_its_file(tmp_path):
    path = tmp_path / "shop.db"
    sqlite3.connect(path).close()
    conn = DatabaseConnection("local", "sqlite", str(path))
    assert await conn.list_databases() == []  # not connected yet
    await conn.connect()
    try:
        assert await conn.list_databases() == [DatabaseInfo("shop.db", current=True)]
    finally:
        await conn.disconnect()


async def test_sqlite_in_memory_has_a_name_too():
    conn = DatabaseConnection("scratch", "sqlite", ":memory:")
    await conn.connect()
    try:
        assert [d.name for d in await conn.list_databases()] == ["in memory"]
    finally:
        await conn.disconnect()


# ── SQL Server and Oracle (synchronous drivers) ─────────────────────


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self._conn.executed.append(sql)

    def fetchall(self):
        return list(self._conn.rows)

    def fetchone(self):
        return self._conn.rows[0]

    def close(self):
        pass


class _Raw:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def cursor(self):
        return _Cursor(self)


TARGET = DbTarget(host="db.example.com", port=1433, user="app", password="pw", database="XEPDB1")


def test_sql_server_lists_databases_the_login_can_open():
    driver = MssqlDriver(TARGET)
    driver._conn = _Raw([("master", 0, 1), ("shop", 1, 0), ("rdsadmin", 0, 1)])
    assert driver.databases() == [
        ("master", False, True),
        ("shop", True, False),
        ("rdsadmin", False, True),
    ]
    sql = driver._conn.executed[0]
    assert "sys.databases" in sql and "HAS_DBACCESS(name) = 1" in sql and "state = 0" in sql


def test_sql_server_reads_another_databases_catalogue_by_three_part_name():
    driver = MssqlDriver(TARGET)
    driver._conn = _Raw(
        [("dbo", "cases", "id", "int", 0, 1), ("sales", "invoice", "id", "int", 0, 1)]
    )
    tables = driver.schema("arch]ive")
    assert [t.name for t in tables] == ["cases", "sales.invoice"]
    sql = driver._conn.executed[0]
    assert sql.count("[arch]]ive].INFORMATION_SCHEMA.") == 4
    assert "{db}" not in sql

    driver.schema()
    own = driver._conn.executed[1]
    assert "FROM INFORMATION_SCHEMA.COLUMNS" in own and "[" not in own


def test_oracle_has_the_one_database_it_signed_in_to():
    driver = OracleDriver(TARGET)
    driver._conn = _Raw([("ORCLPDB1",)])
    assert driver.databases() == [("ORCLPDB1", True, False)]
    driver._conn = _Raw([(None,)])
    assert driver.databases() == [("XEPDB1", True, False)]  # falls back to the service name


async def test_driver_connections_list_and_read_through_the_driver():
    conn = DatabaseConnection("ms", "mssql", "mssql://app:pw@h:1433/shop")
    asked = []

    class Driver:
        def databases(self):
            return [("tempdb", False, True), ("shop", True, False), ("archive", False, False)]

        def schema(self, database=None):
            asked.append(database)
            return []

        def close(self):
            pass

    conn._driver = Driver()
    assert [(d.name, d.current, d.system) for d in await conn.list_databases()] == [
        ("shop", True, False),
        ("archive", False, False),
        ("tempdb", False, True),
    ]
    await conn.get_schema("archive")
    await conn.get_schema()
    assert asked == ["archive", None]


async def test_a_database_that_cannot_be_read_raises_its_reason():
    conn = DatabaseConnection("ms", "mssql", "mssql://app:pw@h:1433/shop")

    class Driver:
        def schema(self, database=None):
            raise PermissionError("The server principal is not able to access the database")

    conn._driver = Driver()
    with pytest.raises(PermissionError, match="not able to access"):
        await conn.get_schema("payroll")


async def test_a_catalogue_that_cannot_be_listed_is_just_empty():
    conn = DatabaseConnection("ms", "mssql", "mssql://app:pw@h:1433/shop")

    class Driver:
        def databases(self):
            raise RuntimeError("VIEW ANY DATABASE permission denied")

    conn._driver = Driver()
    assert await conn.list_databases() == []


async def test_nothing_is_listed_when_not_connected():
    for db_type in ("postgresql", "mysql", "mssql", "oracle"):
        conn = DatabaseConnection("x", db_type, f"{db_type}://a:b@h:1/d")
        assert await conn.list_databases() == []
        assert await conn.get_schema("other") == []


# ── PostgreSQL ──────────────────────────────────────────────────────


class _PgPool:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return Ctx()

    async def fetch(self, sql):
        self.queries.append(sql)
        return self.rows


async def test_postgres_lists_databases_it_may_connect_to():
    conn = DatabaseConnection("pg", "postgresql", "postgresql://a:b@h:5432/shop")
    conn._pg_pool = _PgPool(
        [
            {"datname": "postgres", "current": False},
            {"datname": "rdsadmin", "current": False},
            {"datname": "shop", "current": True},
        ]
    )
    listed = await conn.list_databases()
    assert [(d.name, d.current, d.system) for d in listed] == [
        ("shop", True, False),
        ("postgres", False, False),
        ("rdsadmin", False, True),
    ]
    sql = conn._pg_pool.queries[0]
    assert "NOT datistemplate" in sql and "has_database_privilege(datname, 'CONNECT')" in sql


async def test_postgres_reads_another_database_over_a_second_short_connection(monkeypatch):
    """Same server, login, TLS and tunnel as the pool — only the database differs."""
    import asyncpg

    opened = []

    class Other:
        closed = False

        async def fetch(self, sql):
            assert "information_schema.columns" in sql
            return [
                {
                    "table_name": "invoices",
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": "NO",
                    "is_pk": 1,
                },
                {
                    "table_name": "invoices",
                    "column_name": "note",
                    "data_type": "text",
                    "is_nullable": "YES",
                    "is_pk": 0,
                },
            ]

        async def close(self):
            Other.closed = True

    async def connect(*args, **kwargs):
        opened.append((args, kwargs))
        return Other()

    monkeypatch.setattr(asyncpg, "connect", connect)
    conn = DatabaseConnection("pg", "postgresql", "postgresql://a:b@h:5432/shop")
    conn._pg_pool = _PgPool([])
    token = object()  # stands in for the callable that fetches a fresh IAM token
    conn._pg_connect = (
        (),
        {"host": "127.0.0.1", "port": 15999, "user": "app", "password": token, "ssl": "require"},
    )

    tables = await conn.get_schema("billing")

    assert [(t.name, [c.name for c in t.columns]) for t in tables] == [("invoices", ["id", "note"])]
    assert tables[0].columns[0].primary_key and not tables[0].columns[0].nullable
    ((args, kwargs),) = opened
    assert args == ()
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 15999,
        "user": "app",
        "password": token,
        "ssl": "require",
        "database": "billing",
        "timeout": 10,
    }
    assert Other.closed


async def test_postgres_saved_as_a_dsn_overrides_only_the_database(monkeypatch):
    import asyncpg

    opened = []

    class Other:
        async def fetch(self, sql):
            return []

        async def close(self):
            pass

    async def connect(*args, **kwargs):
        opened.append((args, kwargs))
        return Other()

    monkeypatch.setattr(asyncpg, "connect", connect)
    dsn = "postgresql://u:p@localhost:5432/app?sslmode=require"
    conn = DatabaseConnection("old", "postgresql", dsn)
    conn._pg_pool = _PgPool([])
    conn._pg_connect = ((dsn,), {})
    assert await conn.get_schema("other") == []
    assert opened == [((dsn,), {"database": "other", "timeout": 10})]


async def test_the_second_connection_is_closed_even_when_reading_fails(monkeypatch):
    import asyncpg

    class Other:
        closed = False

        async def fetch(self, sql):
            raise RuntimeError("permission denied for schema public")

        async def close(self):
            Other.closed = True

    async def connect(*args, **kwargs):
        return Other()

    monkeypatch.setattr(asyncpg, "connect", connect)
    conn = DatabaseConnection("pg", "postgresql", "postgresql://a:b@h:5432/shop")
    conn._pg_pool = _PgPool([])
    conn._pg_connect = ((), {"host": "h"})
    with pytest.raises(RuntimeError, match="permission denied"):
        await conn.get_schema("billing")
    assert Other.closed


async def test_connecting_remembers_how_to_dial_and_disconnecting_forgets(monkeypatch):
    import asyncpg

    class Pool(_PgPool):
        async def fetchval(self, sql):
            return 1

        async def close(self):
            pass

    async def create_pool(*args, **kwargs):
        return Pool([])

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    conn = DatabaseConnection(
        "pg",
        "postgresql",
        "postgresql://app:s3cret@db.example.com:5432/shop",
        options={"url_encoded": True, "ssl": "require"},
    )
    assert (await conn.connect())[0]
    args, kwargs = conn._pg_connect
    assert args == () and "database" not in kwargs
    assert (kwargs["host"], kwargs["port"], kwargs["user"], kwargs["ssl"]) == (
        "db.example.com",
        5432,
        "app",
        "require",
    )
    await conn.disconnect()
    assert conn._pg_connect is None


# ── MySQL ───────────────────────────────────────────────────────────


class _MyPool:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return Ctx()

    def cursor(self, *args):
        return self.acquire()

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        return self.rows


async def test_mysql_lists_schemas_and_marks_the_selected_one():
    conn = DatabaseConnection("my", "mysql", "mysql://a:b@h:3306/shop")
    conn._mysql_pool = _MyPool(
        [("information_schema", 0), ("shop", 1), ("analytics", 0), ("sys", 0)]
    )
    listed = await conn.list_databases()
    assert [(d.name, d.current, d.system) for d in listed] == [
        ("shop", True, False),
        ("analytics", False, False),
        ("information_schema", False, True),
        ("sys", False, True),
    ]


async def test_mysql_with_no_database_selected_marks_none():
    conn = DatabaseConnection("my", "mysql", "mysql://a:b@h:3306/")
    conn._mysql_pool = _MyPool([("shop", 0), ("mysql", 0)])
    assert [d.current for d in await conn.list_databases()] == [False, False]


async def test_mysql_reads_any_database_from_the_session_it_has():
    conn = DatabaseConnection("my", "mysql", "mysql://a:b@h:3306/shop")
    conn._mysql_pool = _MyPool([("orders", "id", "int", "NO", "PRI")])
    tables = await conn.get_schema("analytics")
    assert [t.name for t in tables] == ["orders"]
    sql, params = conn._mysql_pool.executed[0]
    assert params == ("analytics",)  # a parameter, never pasted into the SQL
    assert "analytics" not in sql

    await conn.get_schema()
    assert conn._mysql_pool.executed[1][1] == (None,)  # the selected database


def test_catalog_module_is_what_the_connection_uses():
    assert db_catalog.list_databases and db_catalog.tables_of
