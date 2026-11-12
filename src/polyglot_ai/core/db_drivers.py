"""SQL Server and Oracle connections for the Database panel.

Both drivers are synchronous (``pymssql`` wraps FreeTDS; ``oracledb`` in
its "thin" mode is pure protocol code and needs no Oracle client), so
:class:`~polyglot_ai.core.db_explorer.DatabaseConnection` runs every call
here on a worker thread. One connection per panel connection, used one
call at a time under that class's lock.
"""

from __future__ import annotations

import logging
import time

from polyglot_ai.core.db_dialects import DbTarget, make_ssl_context
from polyglot_ai.core.db_explorer import ColumnInfo, QueryResult, TableInfo

logger = logging.getLogger(__name__)


class DriverMissing(Exception):
    """The Python package for this engine isn't installed."""


class UnsupportedOption(Exception):
    """The connection asks for something this engine's driver can't do."""


def describe_error(exc: Exception) -> str:
    """One readable line from a driver exception.

    pymssql raises ``(number, b"message…DB-Lib error message …")`` — as
    one tuple when a login fails, as two arguments when a query does —
    and either way its ``str()`` is a tuple's repr; oracledb's messages
    come with a help URL on a second line.
    """
    detail = exc.args[0] if exc.args else exc
    if len(exc.args) >= 2 and isinstance(exc.args[0], int):
        detail = exc.args
    if isinstance(detail, tuple) and len(detail) >= 2:
        code, message = detail[0], detail[1]
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        text = str(message).split("DB-Lib error message")[0].strip()
        return f"{text} (error {code})" if text else f"SQL Server error {code}"
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _plain(value):
    """Something the results table (and JSON) can hold."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return "0x" + data[:32].hex() + ("…" if len(data) > 32 else "")
    return str(value)


def _fetch(cursor, max_rows: int, elapsed: float) -> QueryResult:
    """Rows from a DB-API cursor that has just executed something."""
    if cursor.description is None:
        affected = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else None
        return QueryResult(
            columns=[], rows=[], row_count=0, execution_time=elapsed, affected_rows=affected
        )
    columns = [d[0] for d in cursor.description]
    rows = [[_plain(v) for v in row] for row in cursor.fetchmany(max_rows)]
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), execution_time=elapsed)


def _group(rows) -> list[TableInfo]:
    """``(table, column, type, nullable, is_pk)`` rows → tables, in order."""
    tables: dict[str, list[ColumnInfo]] = {}
    for table, column, data_type, nullable, is_pk in rows:
        tables.setdefault(table, []).append(
            ColumnInfo(
                name=column,
                data_type=str(data_type or ""),
                nullable=bool(nullable),
                primary_key=bool(is_pk),
            )
        )
    return [TableInfo(name=name, columns=cols) for name, cols in tables.items()]


class MssqlDriver:
    """Microsoft SQL Server (RDS ``sqlserver-ex/web/se/ee``) via pymssql."""

    #: No "verify": pymssql takes no CA file and has no switch to validate
    #: the certificate, so that mode is refused in ``connect`` rather than
    #: mapped to "require" and reported as verified.
    _ENCRYPTION = {"disable": "off", "prefer": "request", "require": "require"}

    _SCHEMA_SQL = """
        SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
               CASE WHEN c.IS_NULLABLE = 'YES' THEN 1 ELSE 0 END,
               CASE WHEN pk.COLUMN_NAME IS NULL THEN 0 ELSE 1 END
        FROM {db}INFORMATION_SCHEMA.COLUMNS c
        JOIN {db}INFORMATION_SCHEMA.TABLES t
          ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
         AND t.TABLE_TYPE = 'BASE TABLE'
        LEFT JOIN (
            SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
            FROM {db}INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN {db}INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
              ON ku.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             AND ku.TABLE_SCHEMA = tc.TABLE_SCHEMA
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ) pk ON pk.TABLE_SCHEMA = c.TABLE_SCHEMA AND pk.TABLE_NAME = c.TABLE_NAME
            AND pk.COLUMN_NAME = c.COLUMN_NAME
        ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
    """

    def __init__(self, target: DbTarget, *, ssl_mode: str = "prefer", **_ignored) -> None:
        self._target = target
        self._ssl_mode = ssl_mode
        self._conn = None

    def connect(self) -> str:
        try:
            import pymssql
        except ImportError as exc:
            raise DriverMissing(
                "SQL Server support needs the “pymssql” package, which isn't installed."
            ) from exc
        if self._ssl_mode == "verify":
            raise UnsupportedOption(
                "SQL Server connections can't verify the server's certificate yet. "
                "Choose “Required” to encrypt the connection without that check."
            )
        t = self._target
        self._conn = pymssql.connect(
            server=t.host,
            port=str(t.port),
            user=t.user,
            password=t.password,
            database=t.database or "master",
            login_timeout=10,
            timeout=120,
            appname="Polyglot AI",
            autocommit=True,
            encryption=self._ENCRYPTION.get(self._ssl_mode, "request"),
        )
        cursor = self._conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchall()
        cursor.close()
        return "Connected"

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    #: Online databases this login may open. ``rdsadmin`` is Amazon's own.
    _DATABASES_SQL = """
        SELECT name, CASE WHEN name = DB_NAME() THEN 1 ELSE 0 END,
               CASE WHEN database_id <= 4 OR name = 'rdsadmin' THEN 1 ELSE 0 END
        FROM sys.databases
        WHERE state = 0 AND HAS_DBACCESS(name) = 1
        ORDER BY name
    """

    def databases(self) -> list[tuple[str, bool, bool]]:
        """``(name, is the connected one, is a system database)`` for each database."""
        cursor = self._conn.cursor()
        try:
            cursor.execute(self._DATABASES_SQL)
            rows = cursor.fetchall()
            return [(name, bool(current), bool(system)) for name, current, system in rows]
        finally:
            cursor.close()

    def schema(self, database: str | None = None) -> list[TableInfo]:
        """The connected database's tables, or those of another one on the server."""
        prefix = "[" + database.replace("]", "]]") + "]." if database else ""
        cursor = self._conn.cursor()
        try:
            cursor.execute(self._SCHEMA_SQL.format(db=prefix))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        # ``dbo`` is everyone's default schema; only qualify the others.
        return _group(
            (name if schema == "dbo" else f"{schema}.{name}", column, dtype, nullable, is_pk)
            for schema, name, column, dtype, nullable, is_pk in rows
        )

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        cursor = self._conn.cursor()
        try:
            start = time.monotonic()
            cursor.execute(sql)
            return _fetch(cursor, max_rows, time.monotonic() - start)
        finally:
            cursor.close()


class OracleDriver:
    """Oracle Database (RDS ``oracle-ee/se2``) via python-oracledb, thin mode."""

    _SCHEMA_SQL = """
        SELECT c.table_name, c.column_name, c.data_type,
               CASE WHEN c.nullable = 'Y' THEN 1 ELSE 0 END,
               CASE WHEN pk.column_name IS NULL THEN 0 ELSE 1 END
        FROM user_tab_columns c
        JOIN user_tables t ON t.table_name = c.table_name
        LEFT JOIN (
            SELECT cc.table_name, cc.column_name
            FROM user_constraints uc
            JOIN user_cons_columns cc ON cc.constraint_name = uc.constraint_name
            WHERE uc.constraint_type = 'P'
        ) pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
        ORDER BY c.table_name, c.column_id
    """

    def __init__(
        self,
        target: DbTarget,
        *,
        ssl_mode: str = "prefer",
        ca_file: str = "",
        tunnelled: bool = False,
    ) -> None:
        self._target = target
        self._ssl_mode = ssl_mode
        self._ca_file = ca_file
        self._tunnelled = tunnelled
        self._conn = None

    def connect(self) -> str:
        try:
            import oracledb
        except ImportError as exc:
            raise DriverMissing(
                "Oracle support needs the “oracledb” package, which isn't installed."
            ) from exc
        t = self._target
        params: dict = {
            "user": t.user,
            "password": t.password,
            "host": t.host,
            "port": t.port,
            "service_name": t.database or "ORCL",
            "tcp_connect_timeout": 10,
        }
        if self._ssl_mode in ("require", "verify"):
            # Oracle has no "upgrade if you can": TLS is a different
            # listener (``tcps``, usually port 2484 on RDS).
            verify = self._ssl_mode == "verify" and not self._tunnelled
            params.update(
                protocol="tcps",
                ssl_context=make_ssl_context(self._ssl_mode, self._ca_file, check_hostname=verify),
                ssl_server_dn_match=verify,
            )
        # CLOB/BLOB columns as plain values, not locator objects.
        oracledb.defaults.fetch_lobs = False
        self._conn = oracledb.connect(**params)
        self._conn.autocommit = True
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM dual")
            cursor.fetchall()
        return "Connected"

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def databases(self) -> list[tuple[str, bool, bool]]:
        """An Oracle session sees one database: the one it signed in to."""
        with self._conn.cursor() as cursor:
            # The pluggable database's name; the database's own before 12c.
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CON_NAME') FROM dual")
            name = cursor.fetchone()[0]
        return [(name or self._target.database or "database", True, False)]

    def schema(self, database: str | None = None) -> list[TableInfo]:
        with self._conn.cursor() as cursor:
            cursor.execute(self._SCHEMA_SQL)
            return _group(cursor.fetchall())

    @staticmethod
    def _statement(sql: str) -> str:
        """Oracle rejects a trailing ``;`` on plain SQL, but a PL/SQL block needs its own."""
        text = sql.strip()
        if text.endswith(";") and not text.rstrip(";").rstrip().upper().endswith("END"):
            text = text.rstrip(";").rstrip()
        return text

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        with self._conn.cursor() as cursor:
            start = time.monotonic()
            cursor.execute(self._statement(sql))
            return _fetch(cursor, max_rows, time.monotonic() - start)


DRIVERS = {"mssql": MssqlDriver, "oracle": OracleDriver}
