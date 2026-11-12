"""The built-in database export: tables and their rows, as SQL.

One exporter per server engine. Each knows three things about its
engine: how to list tables with a ``CREATE TABLE`` that will rebuild
them, how to read a table's rows in batches without loading it whole,
and how to write a value as a literal that engine reads back unchanged.
:mod:`polyglot_ai.core.db_backup` drives them and writes the file.

This is not a replacement for ``pg_dump`` or ``mysqldump`` — it carries
tables and data, not views, routines, triggers or permissions (each
exporter's ``NOT_INCLUDED`` says exactly what it leaves out, and that
text goes at the top of every file). It exists so that every engine the
panel can connect to can be backed up with nothing else installed,
including SQL Server and Oracle, which have no client-side dump tool.

No Qt here. The exporters reach into a connected
:class:`~polyglot_ai.core.db_explorer.DatabaseConnection` for its
driver-level connection; the backup opens one of its own for the
purpose, so nothing here competes with the panel's queries.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import decimal
import math
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

BATCH_ROWS = 1000


@dataclass
class TableSpec:
    label: str  # for progress and comments: ``public.cases``
    target: str  # quoted and qualified, ready for SQL
    create: list[str]  # statements that create it, no terminator
    columns: list[str]  # quoted names of the columns that are inserted
    select: str  # reads those columns, in that order
    before_rows: list[str] = field(default_factory=list)
    after_rows: list[str] = field(default_factory=list)
    #: Per inserted column, a hint for ``literal`` ("" = go by the value).
    kinds: list[str] = field(default_factory=list)


def quote_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _iso_time(value: dt.time) -> str:
    return value.isoformat(timespec="microseconds" if value.microsecond else "seconds")


def _iso_datetime(value: dt.datetime, sep: str = " ") -> str:
    text = value.replace(tzinfo=None).isoformat(
        sep=sep, timespec="microseconds" if value.microsecond else "seconds"
    )
    if value.tzinfo is not None:
        offset = value.utcoffset() or dt.timedelta()
        minutes = int(offset.total_seconds() // 60)
        sign = "-" if minutes < 0 else "+"
        text += f"{' ' if sep == ' ' else ''}{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    return text


def _number(value) -> str | None:
    """A numeric literal, or None for the floats SQL has no literal for."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, decimal.Decimal) and not value.is_finite():
        return None
    return format(value, "f") if isinstance(value, decimal.Decimal) else repr(value)


#: How long an exporter waits for the server while it is letting go.
END_SECONDS = 10.0


class Exporter:
    """What the backup needs from an engine. Subclasses fill it in."""

    ENGINE = ""
    #: What a file from this exporter does not contain, for its header.
    NOT_INCLUDED = ""
    #: How to load the file back, for its header.
    RESTORE = ""
    #: A line that ends a batch of statements (SQL Server's ``GO``).
    BATCH_SEPARATOR = ""
    ROWS_PER_INSERT = 200
    #: True where the driver blocks its thread while the server works
    #: (pymssql, python-oracledb). Cancel then can't reach the export by
    #: cancelling its task; it goes through :meth:`interrupt`.
    BLOCKING = False

    def __init__(self, connection) -> None:
        self._connection = connection
        self.warnings: list[str] = []

    async def begin(self) -> list[str]:
        """Start reading (a snapshot where the engine has one); the file's opening statements."""
        return []

    async def tables(self) -> list[TableSpec]:
        raise NotImplementedError

    def rows(self, table: TableSpec) -> AsyncIterator[list[Sequence]]:
        raise NotImplementedError

    def literal(self, value, kind: str = "") -> str:
        raise NotImplementedError

    def closing(self) -> list[str]:
        """The file's last statements."""
        return []

    async def end(self) -> None:
        """Release whatever ``begin`` took."""

    def interrupt(self) -> None:
        """Make the call the server is working on return now.

        Called from another thread, when Cancel was pressed and the
        export didn't notice by itself — it is waiting for a lock, or for
        a slow query. Only the blocking drivers need it.
        """

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


# ── PostgreSQL ──────────────────────────────────────────────────────


class PostgresExporter(Exporter):
    """Every value is read as text and written back as a quoted literal.

    PostgreSQL's text form of a value is, by design, what its input
    function accepts — it is what ``pg_dump`` relies on too — so arrays,
    JSON, ranges, ``bytea``, intervals and the rest need no handling of
    their own.
    """

    ENGINE = "PostgreSQL"
    NOT_INCLUDED = (
        "views, functions, triggers, sequences and column defaults, indexes and "
        "constraints other than primary keys, foreign keys, roles and privileges. "
        "A partitioned table is written as one ordinary table."
    )
    RESTORE = "psql -v ON_ERROR_STOP=1 -d <empty database> -f <this file>"

    _SYSTEM = "('pg_catalog', 'information_schema')"

    async def begin(self) -> list[str]:
        self._pg = await self._connection._pg_pool.acquire()
        # One snapshot for the whole export: tables that refer to each
        # other are read as of the same moment.
        self._transaction = self._pg.transaction(isolation="repeatable_read", readonly=True)
        await self._transaction.start()
        extensions = await self._pg.fetch(
            "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1"
        )
        # Everything that changes the database comes after BEGIN — the
        # extensions too — so a load that fails leaves nothing behind.
        # The two SETs before it only configure the session doing the load.
        return [
            "SET standard_conforming_strings = on",
            "SET client_encoding = 'UTF8'",
            "BEGIN",
            *(f"CREATE EXTENSION IF NOT EXISTS {self.name(r[0])}" for r in extensions),
        ]

    @staticmethod
    def name(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    async def tables(self) -> list[TableSpec]:
        found = await self._pg.fetch(
            f"""
            SELECT c.oid, n.nspname, c.relname, c.relkind::text
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
              AND c.relpersistence <> 't'
              AND n.nspname NOT IN {self._SYSTEM} AND n.nspname NOT LIKE 'pg\\_%'
              AND NOT EXISTS (SELECT 1 FROM pg_depend d
                              WHERE d.objid = c.oid AND d.deptype = 'e')
            ORDER BY n.nspname, c.relname
            """
        )
        oids = [r[0] for r in found]
        columns: dict[int, list] = {oid: [] for oid in oids}
        for r in await self._pg.fetch(
            """
            SELECT a.attrelid, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
            FROM pg_attribute a
            WHERE a.attrelid = ANY($1::oid[]) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attrelid, a.attnum
            """,
            oids,
        ):
            columns[r[0]].append(r)
        keys = {
            r[0]: (r[1], r[2])
            for r in await self._pg.fetch(
                "SELECT conrelid, conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE contype = 'p' AND conrelid = ANY($1::oid[])",
                oids,
            )
        }
        enums = await self._pg.fetch(
            f"""
            SELECT n.nspname, t.typname, array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
            FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE n.nspname NOT IN {self._SYSTEM}
              AND NOT EXISTS (SELECT 1 FROM pg_depend d
                              WHERE d.objid = t.oid AND d.deptype = 'e')
            GROUP BY 1, 2 ORDER BY 1, 2
            """
        )

        # Schemas and enum types first, attached to the first table: the
        # tables' column types refer to them.
        schemas = sorted({r[1] for r in found} | {r[0] for r in enums})
        prelude = [f"CREATE SCHEMA IF NOT EXISTS {self.name(s)}" for s in schemas if s != "public"]
        for schema, type_name, labels in enums:
            values = ", ".join(quote_string(label) for label in labels)
            prelude.append(
                f"CREATE TYPE {self.name(schema)}.{self.name(type_name)} AS ENUM ({values})"
            )

        specs = []
        for oid, schema, table, kind in found:
            target = f"{self.name(schema)}.{self.name(table)}"
            lines = [
                f"    {self.name(c[1])} {c[2]}{' NOT NULL' if c[3] else ''}" for c in columns[oid]
            ]
            if oid in keys:
                lines.append(f"    CONSTRAINT {self.name(keys[oid][0])} {keys[oid][1]}")
            names = [self.name(c[1]) for c in columns[oid]]
            # ONLY: rows of inheritance children belong to the children,
            # which are tables in their own right. A partitioned table has
            # no rows of its own, so there it must be left off.
            source = target if kind == "p" else f"ONLY {target}"
            casts = ", ".join(f"{n}::text" for n in names) or "NULL"
            specs.append(
                TableSpec(
                    label=table if schema == "public" else f"{schema}.{table}",
                    target=target,
                    create=prelude + [f"CREATE TABLE {target} (\n" + ",\n".join(lines) + "\n)"],
                    columns=names,
                    select=f"SELECT {casts} FROM {source}",
                )
            )
            prelude = []
        if prelude and not specs:
            self.warn("The database has no tables; only its schemas and types were written.")
        return specs

    async def rows(self, table: TableSpec) -> AsyncIterator[list[Sequence]]:
        if not table.columns:
            self.warn(f"{table.label} has no columns; its rows were not written.")
            return
        cursor = await self._pg.cursor(table.select)
        while batch := await cursor.fetch(BATCH_ROWS):
            yield batch

    def literal(self, value, kind: str = "") -> str:
        return "NULL" if value is None else quote_string(value)

    def closing(self) -> list[str]:
        return ["COMMIT"]

    async def end(self) -> None:
        # After a cancel the server may not answer (the cancel request
        # got lost on the way): don't wait for it for ever. A dropped
        # connection ends the read-only transaction just as well.
        try:
            await asyncio.wait_for(self._transaction.rollback(), END_SECONDS)
        except Exception:
            self._pg.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._connection._pg_pool.release(self._pg), END_SECONDS)


# ── MySQL / MariaDB ─────────────────────────────────────────────────


class MySQLExporter(Exporter):
    ENGINE = "MySQL / MariaDB"
    NOT_INCLUDED = (
        "views, stored procedures and functions, triggers, events, users and grants. "
        "Tables are complete: columns, defaults, indexes and foreign keys."
    )
    RESTORE = "mysql --default-character-set=utf8mb4 <empty database> < <this file>"

    _ESCAPES = str.maketrans(
        {"\\": "\\\\", "'": "\\'", "\0": "\\0", "\n": "\\n", "\r": "\\r", "\x1a": "\\Z"}
    )

    @staticmethod
    def name(identifier: str) -> str:
        return "`" + identifier.replace("`", "``") + "`"

    async def _query(self, sql: str) -> list:
        async with self._my.cursor() as cursor:
            await cursor.execute(sql)
            return list(await cursor.fetchall())

    async def begin(self) -> list[str]:
        self._my = self._connection._mysql_pool._connection
        await self._my.ping(reconnect=True)
        if not (await self._query("SELECT DATABASE()"))[0][0]:
            raise ValueError(
                "This connection has no database selected, so there is nothing to export. "
                "Edit the connection and fill in the Database field."
            )
        # InnoDB tables are read as of this moment, whatever is written meanwhile.
        await self._query("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        return [
            "SET NAMES utf8mb4",
            # Backslash escapes on, explicit zeros kept in AUTO_INCREMENT columns.
            "SET @OLD_SQL_MODE = @@SQL_MODE, SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO'",
            "SET @OLD_FOREIGN_KEY_CHECKS = @@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS = 0",
            "SET @OLD_UNIQUE_CHECKS = @@UNIQUE_CHECKS, UNIQUE_CHECKS = 0",
        ]

    @staticmethod
    def _generated(extra: str) -> bool:
        """Is this a generated column? (Its value can't be inserted.)"""
        extra = (extra or "").upper()
        return (
            "VIRTUAL GENERATED" in extra
            or "STORED GENERATED" in extra
            or extra in ("VIRTUAL", "PERSISTENT", "STORED")
        )

    async def tables(self) -> list[TableSpec]:
        names = [
            r[0]
            for r in await self._query(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_NAME"
            )
        ]
        columns: dict[str, list[str]] = {name: [] for name in names}
        for table, column, extra in await self._query(
            "SELECT TABLE_NAME, COLUMN_NAME, EXTRA FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ):
            if table in columns and not self._generated(extra):
                columns[table].append(self.name(column))
        specs = []
        for table in names:
            target = self.name(table)
            ddl = (await self._query(f"SHOW CREATE TABLE {target}"))[0][1]
            specs.append(
                TableSpec(
                    label=table,
                    target=target,
                    create=[ddl],
                    columns=columns[table],
                    select=f"SELECT {', '.join(columns[table]) or 'NULL'} FROM {target}",
                )
            )
        return specs

    async def rows(self, table: TableSpec) -> AsyncIterator[list[Sequence]]:
        import aiomysql

        if not table.columns:
            return
        # Unbuffered: the default cursor would load the whole table first.
        async with self._my.cursor(aiomysql.SSCursor) as cursor:
            await cursor.execute(table.select)
            while batch := await cursor.fetchmany(BATCH_ROWS):
                yield batch

    def literal(self, value, kind: str = "") -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return _number(value) or "NULL"
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            return "0x" + data.hex() if data else "''"
        if isinstance(value, dt.datetime):
            return quote_string(_iso_datetime(value.replace(tzinfo=None)))
        if isinstance(value, dt.date):
            return quote_string(value.isoformat())
        if isinstance(value, dt.time):
            return quote_string(_iso_time(value))
        if isinstance(value, dt.timedelta):  # TIME: can be negative, or past 24 hours
            micros = round(value.total_seconds() * 1_000_000)
            sign, micros = ("-" if micros < 0 else ""), abs(micros)
            seconds, fraction = divmod(micros, 1_000_000)
            text = f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
            return quote_string(text + (f".{fraction:06d}" if fraction else ""))
        if isinstance(value, (set, frozenset)):
            value = ",".join(sorted(value))
        return "'" + str(value).translate(self._ESCAPES) + "'"

    def closing(self) -> list[str]:
        return [
            "SET UNIQUE_CHECKS = @OLD_UNIQUE_CHECKS",
            "SET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS",
            "SET SQL_MODE = @OLD_SQL_MODE",
        ]

    async def end(self) -> None:
        await self._query("COMMIT")


# ── SQL Server ──────────────────────────────────────────────────────


def mssql_type(name: str, max_length: int, precision: int, scale: int) -> str:
    """A column type as ``CREATE TABLE`` wants it, from the ``sys.columns`` numbers."""
    name = name.lower()
    if name in ("varchar", "char", "varbinary", "binary"):
        return f"{name}({'max' if max_length == -1 else max_length})"
    if name in ("nvarchar", "nchar"):  # stored length is in bytes, two per character
        return f"{name}({'max' if max_length == -1 else max_length // 2})"
    if name in ("decimal", "numeric"):
        return f"{name}({precision}, {scale})"
    if name in ("datetime2", "time", "datetimeoffset"):
        return f"{name}({scale})"
    if name == "float" and precision == 24:
        return "real"
    if name == "timestamp":
        return "rowversion"
    return name


class MssqlExporter(Exporter):
    ENGINE = "SQL Server"
    NOT_INCLUDED = (
        "views, stored procedures, functions, triggers, indexes and constraints other "
        "than primary keys, foreign keys, column defaults, users and permissions. "
        "Tables were read one after another, not as of a single moment."
    )
    RESTORE = "sqlcmd -S <server> -d <empty database> -b -I -i <this file>   (or open it in SSMS)"
    BATCH_SEPARATOR = "GO"
    BLOCKING = True
    #: Read as ISO text rather than as Python values, which would round them.
    _AS_TEXT = frozenset({"datetime2", "time", "datetimeoffset"})
    _LINE_BREAK = re.compile(r"(\r\n|\r|\n)")
    _BREAKS = {"\r\n": "NCHAR(13) + NCHAR(10)", "\r": "NCHAR(13)", "\n": "NCHAR(10)"}

    @staticmethod
    def name(identifier: str) -> str:
        return "[" + identifier.replace("]", "]]") + "]"

    def _query(self, sql: str) -> list:
        cursor = self._ms.cursor()
        try:
            cursor.execute(sql)
            return list(cursor.fetchall())
        finally:
            cursor.close()

    async def begin(self) -> list[str]:
        self._ms = self._connection._driver._conn
        return ["SET NOCOUNT ON", "SET XACT_ABORT ON", "SET QUOTED_IDENTIFIER ON"]

    def interrupt(self) -> None:
        conn = getattr(self, "_ms", None)
        if conn is not None:
            # pymssql's DB-API connection wraps the FreeTDS one, whose
            # cancel sends the server an attention signal.
            conn._conn.cancel()

    async def tables(self) -> list[TableSpec]:
        found = self._query(
            "SELECT t.object_id, s.name, t.name FROM sys.tables t "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name"
        )
        columns: dict[int, list] = {r[0]: [] for r in found}
        for r in self._query(
            """
            SELECT c.object_id, c.name, bt.name, c.max_length, c.precision, c.scale,
                   c.is_nullable, c.is_identity, c.is_computed,
                   CONVERT(varchar(40), ic.seed_value), CONVERT(varchar(40), ic.increment_value),
                   cc.definition, cc.is_persisted
            FROM sys.columns c
            JOIN sys.tables t ON t.object_id = c.object_id AND t.is_ms_shipped = 0
            JOIN sys.types ut ON ut.user_type_id = c.user_type_id
            JOIN sys.types bt ON bt.user_type_id =
                 CASE WHEN ut.is_user_defined = 1 AND ut.is_assembly_type = 0
                      THEN ut.system_type_id ELSE ut.user_type_id END
            LEFT JOIN sys.identity_columns ic
                   ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            LEFT JOIN sys.computed_columns cc
                   ON cc.object_id = c.object_id AND cc.column_id = c.column_id
            ORDER BY c.object_id, c.column_id
            """
        ):
            if r[0] in columns:
                columns[r[0]].append(r)
        keys: dict[int, tuple[str, str, list[str]]] = {}
        for object_id, constraint, index_type, column, descending in self._query(
            """
            SELECT kc.parent_object_id, kc.name, i.type_desc, col.name, ic.is_descending_key
            FROM sys.key_constraints kc
            JOIN sys.indexes i
              ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
            JOIN sys.index_columns ic
              ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            JOIN sys.columns col
              ON col.object_id = ic.object_id AND col.column_id = ic.column_id
            WHERE kc.type = 'PK'
            ORDER BY kc.parent_object_id, ic.key_ordinal
            """
        ):
            entry = keys.setdefault(object_id, (constraint, index_type, []))
            entry[2].append(self.name(column) + (" DESC" if descending else ""))

        specs, schemas_done = [], {"dbo"}
        for object_id, schema, table in found:
            target = f"{self.name(schema)}.{self.name(table)}"
            create: list[str] = []
            if schema not in schemas_done:
                schemas_done.add(schema)
                statement = quote_string(f"CREATE SCHEMA {self.name(schema)}")
                create.append(f"IF SCHEMA_ID(N{quote_string(schema)}) IS NULL EXEC(N{statement})")
            lines, inserted, selected, kinds, identity = [], [], [], [], False
            for (
                _oid,
                column,
                type_name,
                max_length,
                precision,
                scale,
                nullable,
                is_identity,
                is_computed,
                seed,
                increment,
                definition,
                persisted,
            ) in columns[object_id]:
                quoted = self.name(column)
                if is_computed:
                    lines.append(f"    {quoted} AS {definition}{' PERSISTED' if persisted else ''}")
                    continue
                sql_type = mssql_type(type_name, max_length, precision, scale)
                line = f"    {quoted} {sql_type}"
                if is_identity:
                    identity = True
                    line += f" IDENTITY({seed or 1}, {increment or 1})"
                lines.append(line + (" NULL" if nullable else " NOT NULL"))
                if sql_type == "rowversion":  # the server assigns these itself
                    continue
                inserted.append(quoted)
                if type_name.lower() in self._AS_TEXT:
                    # Seven decimal places of a second; Python's datetime
                    # holds six. Style 126 is ISO 8601, in full.
                    selected.append(f"CONVERT(varchar(40), {quoted}, 126)")
                    kinds.append("iso")
                else:
                    selected.append(quoted)
                    kinds.append("")
            if object_id in keys:
                constraint, index_type, key_columns = keys[object_id]
                lines.append(
                    f"    CONSTRAINT {self.name(constraint)} PRIMARY KEY {index_type} "
                    f"({', '.join(key_columns)})"
                )
            create.append(f"CREATE TABLE {target} (\n" + ",\n".join(lines) + "\n)")
            specs.append(
                TableSpec(
                    label=table if schema == "dbo" else f"{schema}.{table}",
                    target=target,
                    create=create,
                    columns=inserted,
                    select=f"SELECT {', '.join(selected) or 'NULL'} FROM {target}",
                    # Without this an IDENTITY column refuses the saved values.
                    before_rows=[f"SET IDENTITY_INSERT {target} ON"] if identity else [],
                    after_rows=[f"SET IDENTITY_INSERT {target} OFF"] if identity else [],
                    kinds=kinds,
                )
            )
        return specs

    async def rows(self, table: TableSpec) -> AsyncIterator[list[Sequence]]:
        if not table.columns:
            return
        cursor = self._ms.cursor()
        try:
            cursor.execute(table.select)
            while batch := cursor.fetchmany(BATCH_ROWS):
                yield batch
        finally:
            cursor.close()

    def _text(self, text: str) -> str:
        """An ``N'…'`` literal with no line break inside it.

        ``sqlcmd`` and SSMS end a batch at any line that says ``GO`` —
        even one in the middle of a string — so a value's own line breaks
        are written as ``NCHAR(10)``. The cast keeps the pieces from being
        joined as ``nvarchar(4000)``, which would cut a long value short.
        """
        pieces = self._LINE_BREAK.split(text)
        if len(pieces) == 1:
            return "N" + quote_string(text)
        parts = [f"CAST(N{quote_string(pieces[0])} AS nvarchar(max))"]
        for index in range(1, len(pieces), 2):
            parts.append(self._BREAKS[pieces[index]])
            if pieces[index + 1]:
                parts.append("N" + quote_string(pieces[index + 1]))
        return " + ".join(parts)

    def literal(self, value, kind: str = "") -> str:
        if value is None:
            return "NULL"
        if kind == "iso" and isinstance(value, str):
            return quote_string(value)
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return _number(value) or "NULL"
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "0x" + bytes(value).hex()
        # Dates are cast from ISO text: read the same whatever the
        # server's language and DATEFORMAT settings are.
        if isinstance(value, dt.datetime):
            kind = "datetimeoffset" if value.tzinfo is not None else "datetime2"
            return f"CAST('{_iso_datetime(value, sep='T')}' AS {kind})"
        if isinstance(value, dt.date):
            return f"CAST('{value.isoformat()}' AS date)"
        if isinstance(value, dt.time):
            return f"CAST('{_iso_time(value)}' AS time)"
        if isinstance(value, uuid.UUID):
            return quote_string(str(value))
        return self._text(str(value))


# ── Oracle ──────────────────────────────────────────────────────────


def oracle_type(
    data_type: str,
    data_length: int | None,
    char_length: int | None,
    char_used: str | None,
    precision: int | None,
    scale: int | None,
) -> str:
    """A column type as ``CREATE TABLE`` wants it, from ``USER_TAB_COLS``."""
    name = data_type.upper()
    if name in ("VARCHAR2", "CHAR"):
        unit = " CHAR" if char_used == "C" else " BYTE"
        return f"{name}({char_length or data_length or 1}{unit})"
    if name in ("NVARCHAR2", "NCHAR"):
        return f"{name}({char_length or 1})"
    if name == "NUMBER":
        if precision is not None:
            return f"NUMBER({precision}, {scale or 0})"
        return f"NUMBER(*, {scale})" if scale else "NUMBER"
    if name == "FLOAT":
        return f"FLOAT({precision})" if precision else "FLOAT"
    if name == "RAW":
        return f"RAW({data_length or 1})"
    return name  # DATE, CLOB, BLOB, TIMESTAMP(6) WITH TIME ZONE, … carry their own detail


class OracleExporter(Exporter):
    ENGINE = "Oracle"
    NOT_INCLUDED = (
        "views, packages, procedures, functions, triggers, sequences, indexes and "
        "constraints other than primary keys, foreign keys, column defaults, users "
        "and grants. Only the tables owned by the signed-in user are written."
    )
    RESTORE = "sqlplus <user>@<service> @<this file>   (or run it in SQLcl / SQL Developer)"
    ROWS_PER_INSERT = 1  # no multi-row VALUES before 23ai
    BLOCKING = True
    #: Oracle refuses a string literal over 4000 bytes; at up to four
    #: bytes a character, a thousand characters always fits.
    _CHUNK = 1000
    _RAW_LIMIT = 2000
    _LINE_BREAK = re.compile(r"(\r\n|\r|\n)")
    _BREAKS = {"\r\n": "CHR(13) || CHR(10)", "\r": "CHR(13)", "\n": "CHR(10)"}

    @staticmethod
    def name(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _query(self, sql: str) -> list:
        with self._ora.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())

    async def begin(self) -> list[str]:
        self._ora = self._connection._driver._conn
        self._ora.autocommit = False
        self._ora.rollback()
        # A read-only transaction sees every table as of this moment.
        with self._ora.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        # For SQL*Plus: "&" is not a prompt, and a blank line inside a
        # statement doesn't end it.
        return ["SET DEFINE OFF", "SET SQLBLANKLINES ON"]

    def interrupt(self) -> None:
        conn = getattr(self, "_ora", None)
        if conn is not None:
            conn.cancel()

    async def tables(self) -> list[TableSpec]:
        names = [
            r[0]
            for r in self._query(
                "SELECT table_name FROM user_tables "
                "WHERE temporary = 'N' AND nested = 'NO' AND secondary = 'N' "
                "AND dropped = 'NO' "
                "AND table_name NOT IN (SELECT mview_name FROM user_mviews) "
                "ORDER BY table_name"
            )
        ]
        columns: dict[str, list] = {name: [] for name in names}
        for r in self._query(
            "SELECT table_name, column_name, data_type, data_length, char_length, "
            "char_used, data_precision, data_scale, nullable "
            "FROM user_tab_cols WHERE hidden_column = 'NO' "
            "ORDER BY table_name, column_id"
        ):
            if r[0] in columns:
                columns[r[0]].append(r)
        keys: dict[str, tuple[str, list[str]]] = {}
        for table, constraint, column in self._query(
            "SELECT uc.table_name, uc.constraint_name, cc.column_name "
            "FROM user_constraints uc JOIN user_cons_columns cc "
            "  ON cc.constraint_name = uc.constraint_name "
            "WHERE uc.constraint_type = 'P' ORDER BY uc.table_name, cc.position"
        ):
            keys.setdefault(table, (constraint, []))[1].append(self.name(column))

        specs = []
        for table in names:
            target = self.name(table)
            lines, quoted, kinds = [], [], []
            for _t, column, data_type, length, chars, used, precision, scale, nullable in columns[
                table
            ]:
                sql_type = oracle_type(data_type, length, chars, used, precision, scale)
                lines.append(
                    f"    {self.name(column)} {sql_type}{'' if nullable == 'Y' else ' NOT NULL'}"
                )
                quoted.append(self.name(column))
                numeric = data_type.upper() in ("NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE")
                kinds.append("number" if numeric else "")
            if table in keys:
                constraint, key_columns = keys[table]
                # A system-generated name (SYS_C00…) can't be reused.
                named = (
                    "" if constraint.startswith("SYS_") else f"CONSTRAINT {self.name(constraint)} "
                )
                lines.append(f"    {named}PRIMARY KEY ({', '.join(key_columns)})")
            specs.append(
                TableSpec(
                    label=table,
                    target=target,
                    create=[f"CREATE TABLE {target} (\n" + ",\n".join(lines) + "\n)"],
                    columns=quoted,
                    select=f"SELECT {', '.join(quoted) or 'NULL'} FROM {target}",
                    kinds=kinds,
                )
            )
        return specs

    def _numbers_as_text(self, cursor, metadata):
        """Fetch NUMBER columns as their exact decimal text, not as a float."""
        import oracledb

        if metadata.type_code is oracledb.DB_TYPE_NUMBER:
            return cursor.var(oracledb.DB_TYPE_VARCHAR, arraysize=cursor.arraysize)
        return None

    async def rows(self, table: TableSpec) -> AsyncIterator[list[Sequence]]:
        if not table.columns:
            return
        self._table = table.label
        with self._ora.cursor() as cursor:
            cursor.arraysize = BATCH_ROWS
            cursor.outputtypehandler = self._numbers_as_text
            cursor.execute(table.select)
            while batch := cursor.fetchmany(BATCH_ROWS):
                yield batch

    def _text(self, text: str) -> str:
        # No raw line breaks inside a literal (SQL*Plus reads the file a
        # line at a time), and no piece over the literal size limit. Each
        # break is written as what it was — CR LF, CR or LF — so the value
        # comes back byte for byte.
        pieces: list[str] = []
        for index, part in enumerate(self._LINE_BREAK.split(text)):
            if index % 2:
                pieces.append(self._BREAKS[part])
                continue
            for start in range(0, len(part), self._CHUNK):
                pieces.append(quote_string(part[start : start + self._CHUNK]))
        if len(text) > self._CHUNK:  # a CLOB: plain || would overflow VARCHAR2
            pieces = [f"TO_CLOB({piece})" for piece in pieces]
        return " || ".join(pieces) or "NULL"

    def literal(self, value, kind: str = "") -> str:
        if value is None or value == "":
            return "NULL"  # Oracle's empty string is NULL
        if kind == "number" and isinstance(value, str):
            text = value.strip()
            # ".5" and "-.5" are valid as they stand; anything else odd is quoted.
            return text if text.lstrip("-").replace(".", "", 1).isdigit() else quote_string(text)
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return _number(value) or "NULL"
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            if len(data) > self._RAW_LIMIT:
                self.warn(
                    f"{getattr(self, '_table', 'A table')}: binary values over "
                    f"{self._RAW_LIMIT} bytes can't be written as SQL and were saved as NULL."
                )
                return "NULL"
            return f"HEXTORAW('{data.hex()}')"
        if isinstance(value, dt.datetime):
            return f"TIMESTAMP '{_iso_datetime(value)}'"
        if isinstance(value, dt.date):
            return f"DATE '{value.isoformat()}'"
        if isinstance(value, dt.timedelta):
            return f"NUMTODSINTERVAL({value.total_seconds()!r}, 'SECOND')"
        if isinstance(value, str):
            return self._text(value)
        self.warn(
            f"{getattr(self, '_table', 'A table')}: values of type "
            f"{type(value).__name__} can't be written as SQL and were saved as NULL."
        )
        return "NULL"

    def closing(self) -> list[str]:
        return ["COMMIT"]

    async def end(self) -> None:
        try:
            self._ora.rollback()
        finally:
            self._ora.autocommit = True


EXPORTERS: dict[str, type[Exporter]] = {
    "postgresql": PostgresExporter,
    "mysql": MySQLExporter,
    "mssql": MssqlExporter,
    "oracle": OracleExporter,
}
