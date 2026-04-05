"""Database explorer — unified connection layer for PostgreSQL, SQLite, MySQL."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int | None = None


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_time: float
    error: str | None = None

    @staticmethod
    def from_error(error: str) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0, execution_time=0, error=error)


class DatabaseConnection:
    """Abstraction over different database backends."""

    def __init__(
        self,
        name: str,
        db_type: str,
        connection_string: str,
        mcp_client=None,
    ) -> None:
        self.name = name
        self.db_type = db_type  # "sqlite" | "postgresql" | "mysql"
        self._connection_string = connection_string
        self._mcp_client = mcp_client
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._pg_pool = None  # asyncpg connection pool
        self._mysql_pool = None  # aiomysql connection pool

    async def connect(self) -> tuple[bool, str]:
        """Test and establish connection. Returns (success, message)."""
        if self.db_type == "sqlite":
            return await self._connect_sqlite()
        elif self.db_type == "postgresql":
            return await self._connect_postgresql()
        elif self.db_type == "mysql":
            return await self._connect_mysql()
        return False, f"Unknown database type: {self.db_type}"

    async def disconnect(self) -> None:
        if self._sqlite_conn:
            await self._sqlite_conn.close()
            self._sqlite_conn = None
        if self._pg_pool:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._mysql_pool:
            self._mysql_pool.close()
            await self._mysql_pool.wait_closed()
            self._mysql_pool = None

    async def get_schema(self) -> list[TableInfo]:
        if self.db_type == "sqlite":
            return await self._schema_sqlite()
        elif self.db_type == "postgresql" and self._pg_pool:
            return await self._schema_postgresql()
        elif self.db_type == "mysql" and self._mysql_pool:
            return await self._schema_mysql()
        elif self.db_type in ("postgresql", "mysql"):
            return await self._schema_mcp()
        return []

    async def execute_query(self, sql: str, max_rows: int = 10_000) -> QueryResult:
        if self.db_type == "sqlite":
            return await self._execute_sqlite(sql, max_rows)
        elif self.db_type == "postgresql" and self._pg_pool:
            return await self._execute_postgresql(sql, max_rows)
        elif self.db_type == "mysql" and self._mysql_pool:
            return await self._execute_mysql(sql, max_rows)
        elif self.db_type in ("postgresql", "mysql"):
            return await self._execute_mcp(sql)
        return QueryResult.from_error(f"Unknown database type: {self.db_type}")

    # ── SQLite ──────────────────────────────────────────────────────

    async def _connect_sqlite(self) -> tuple[bool, str]:
        try:
            path = Path(self._connection_string)
            if not path.exists():
                return False, f"File not found: {path}"
            self._sqlite_conn = await aiosqlite.connect(str(path))
            self._sqlite_conn.row_factory = aiosqlite.Row
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    async def _schema_sqlite(self) -> list[TableInfo]:
        if not self._sqlite_conn:
            return []
        try:
            cursor = await self._sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = []
            for row in await cursor.fetchall():
                table_name = row[0]
                col_cursor = await self._sqlite_conn.execute(
                    f"PRAGMA table_info({table_name})"  # noqa: S608
                )
                columns = []
                for col in await col_cursor.fetchall():
                    columns.append(
                        ColumnInfo(
                            name=col[1],
                            data_type=col[2] or "TEXT",
                            nullable=not col[3],
                            primary_key=bool(col[5]),
                        )
                    )
                tables.append(TableInfo(name=table_name, columns=columns))
            return tables
        except Exception:
            logger.exception("Failed to get SQLite schema")
            return []

    async def _execute_sqlite(self, sql: str, max_rows: int) -> QueryResult:
        if not self._sqlite_conn:
            return QueryResult.from_error("Not connected")
        try:
            start = time.monotonic()
            cursor = await self._sqlite_conn.execute(sql)
            rows_raw = await cursor.fetchmany(max_rows)
            elapsed = time.monotonic() - start

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [list(row) for row in rows_raw]
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time=elapsed,
            )
        except Exception as e:
            return QueryResult.from_error(str(e))

    # ── PostgreSQL (direct via asyncpg) ───────────────────────────────

    async def _connect_postgresql(self) -> tuple[bool, str]:
        try:
            import asyncpg

            self._pg_pool = await asyncpg.create_pool(
                self._connection_string, min_size=1, max_size=3, timeout=10
            )
            # Test the connection
            async with self._pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True, "Connected (direct)"
        except ImportError:
            # asyncpg not installed — fall back to MCP
            return await self._connect_mcp()
        except Exception as e:
            return False, str(e)

    async def _schema_postgresql(self) -> list[TableInfo]:
        if not self._pg_pool:
            return []
        try:
            async with self._pg_pool.acquire() as conn:
                # Get all user tables
                table_rows = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
                tables = []
                for trow in table_rows:
                    table_name = trow["table_name"]
                    col_rows = await conn.fetch(
                        "SELECT column_name, data_type, is_nullable, "
                        "  (SELECT COUNT(*) FROM information_schema.key_column_usage k "
                        "   JOIN information_schema.table_constraints tc "
                        "     ON k.constraint_name = tc.constraint_name "
                        "   WHERE k.table_name = c.table_name "
                        "     AND k.column_name = c.column_name "
                        "     AND tc.constraint_type = 'PRIMARY KEY') as is_pk "
                        "FROM information_schema.columns c "
                        "WHERE table_schema = 'public' AND table_name = $1 "
                        "ORDER BY ordinal_position",
                        table_name,
                    )
                    columns = [
                        ColumnInfo(
                            name=cr["column_name"],
                            data_type=cr["data_type"],
                            nullable=cr["is_nullable"] == "YES",
                            primary_key=cr["is_pk"] > 0,
                        )
                        for cr in col_rows
                    ]
                    tables.append(TableInfo(name=table_name, columns=columns))
                return tables
        except Exception:
            logger.exception("Failed to get PostgreSQL schema")
            return []

    async def _execute_postgresql(self, sql: str, max_rows: int) -> QueryResult:
        if not self._pg_pool:
            return QueryResult.from_error("Not connected")
        try:
            start = time.monotonic()
            async with self._pg_pool.acquire() as conn:
                # Use a prepared statement for safety
                rows_raw = await conn.fetch(sql)
                elapsed = time.monotonic() - start

                if not rows_raw:
                    return QueryResult(columns=[], rows=[], row_count=0, execution_time=elapsed)

                columns = list(rows_raw[0].keys())
                rows = [list(row.values()) for row in rows_raw[:max_rows]]
                # Convert non-serializable types to strings
                for r_idx, row in enumerate(rows):
                    for c_idx, val in enumerate(row):
                        if not isinstance(val, (str, int, float, bool, type(None))):
                            rows[r_idx][c_idx] = str(val)

                return QueryResult(
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    execution_time=elapsed,
                )
        except Exception as e:
            return QueryResult.from_error(str(e))

    # ── MySQL (direct via aiomysql) ─────────────────────────────────

    async def _connect_mysql(self) -> tuple[bool, str]:
        try:
            import aiomysql
            from urllib.parse import urlparse

            parsed = urlparse(self._connection_string)
            self._mysql_pool = await aiomysql.create_pool(
                host=parsed.hostname or "localhost",
                port=parsed.port or 3306,
                user=parsed.username or "root",
                password=parsed.password or "",
                db=parsed.path.lstrip("/") if parsed.path else "",
                minsize=1,
                maxsize=3,
                connect_timeout=10,
            )
            # Test the connection
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            return True, "Connected (direct)"
        except ImportError:
            return await self._connect_mcp()
        except Exception as e:
            return False, str(e)

    async def _schema_mysql(self) -> list[TableInfo]:
        if not self._mysql_pool:
            return []
        try:
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SHOW TABLES")
                    table_rows = await cur.fetchall()
                    tables = []
                    for (table_name,) in table_rows:
                        await cur.execute(f"DESCRIBE `{table_name}`")  # noqa: S608
                        col_rows = await cur.fetchall()
                        columns = [
                            ColumnInfo(
                                name=cr[0],
                                data_type=cr[1],
                                nullable=cr[2] == "YES",
                                primary_key=cr[3] == "PRI",
                            )
                            for cr in col_rows
                        ]
                        tables.append(TableInfo(name=table_name, columns=columns))
                    return tables
        except Exception:
            logger.exception("Failed to get MySQL schema")
            return []

    async def _execute_mysql(self, sql: str, max_rows: int) -> QueryResult:
        if not self._mysql_pool:
            return QueryResult.from_error("Not connected")
        try:
            import aiomysql

            start = time.monotonic()
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(sql)
                    rows_raw = await cur.fetchmany(max_rows)
                    elapsed = time.monotonic() - start

                    if not rows_raw:
                        columns = [d[0] for d in cur.description] if cur.description else []
                        return QueryResult(
                            columns=columns, rows=[], row_count=0, execution_time=elapsed
                        )

                    columns = list(rows_raw[0].keys())
                    rows = [list(row.values()) for row in rows_raw]
                    return QueryResult(
                        columns=columns,
                        rows=rows,
                        row_count=len(rows),
                        execution_time=elapsed,
                    )
        except Exception as e:
            return QueryResult.from_error(str(e))

    # ── MCP (fallback for PostgreSQL / MySQL) ───────────────────────

    async def _connect_mcp(self) -> tuple[bool, str]:
        if not self._mcp_client:
            return False, "MCP client not available"
        # MCP servers are connected separately via MCPClient
        server_name = "postgres" if self.db_type == "postgresql" else "mysql"
        if server_name not in self._mcp_client.connected_servers:
            return (
                False,
                f"MCP server '{server_name}' not connected. Connect it from Settings → MCP.",
            )
        return True, "Connected via MCP"

    async def _schema_mcp(self) -> list[TableInfo]:
        if not self._mcp_client:
            return []
        try:
            server_name = "postgres" if self.db_type == "postgresql" else "mysql"
            # Find the schema/list tool for this server
            schema_tools = [
                name
                for name, tool in self._mcp_client.available_tools.items()
                if tool.server_name == server_name
                and any(k in tool.name.lower() for k in ("schema", "list", "describe", "tables"))
            ]
            if not schema_tools:
                return []
            result = await self._mcp_client.call_tool(schema_tools[0], {})
            # Parse result text into TableInfo objects
            return self._parse_schema_text(result)
        except Exception:
            logger.exception("Failed to get MCP schema")
            return []

    async def _execute_mcp(self, sql: str) -> QueryResult:
        if not self._mcp_client:
            return QueryResult.from_error("MCP client not available")
        try:
            server_name = "postgres" if self.db_type == "postgresql" else "mysql"
            # Find the query tool
            query_tools = [
                name
                for name, tool in self._mcp_client.available_tools.items()
                if tool.server_name == server_name
                and any(k in tool.name.lower() for k in ("query", "execute", "run"))
            ]
            if not query_tools:
                return QueryResult.from_error(f"No query tool found for {server_name}")

            start = time.monotonic()
            result = await self._mcp_client.call_tool(query_tools[0], {"query": sql})
            elapsed = time.monotonic() - start

            return self._parse_query_result(result, elapsed)
        except Exception as e:
            return QueryResult.from_error(str(e))

    @staticmethod
    def _parse_schema_text(text: str) -> list[TableInfo]:
        """Best-effort parse of MCP schema tool output into TableInfo list."""
        tables: list[TableInfo] = []
        current_table: str | None = None
        current_columns: list[ColumnInfo] = []

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("-"):
                continue
            # Look for table headers (various formats)
            if line.endswith(":") or (line.isupper() and not line.startswith(" ")):
                if current_table:
                    tables.append(TableInfo(name=current_table, columns=current_columns))
                current_table = line.rstrip(":")
                current_columns = []
            elif current_table and ("│" in line or "|" in line or "\t" in line):
                # Column definition line
                parts = [p.strip() for p in line.replace("│", "|").split("|") if p.strip()]
                if len(parts) >= 2:
                    current_columns.append(ColumnInfo(name=parts[0], data_type=parts[1]))

        if current_table:
            tables.append(TableInfo(name=current_table, columns=current_columns))
        return tables

    @staticmethod
    def _parse_query_result(text: str, elapsed: float) -> QueryResult:
        """Best-effort parse of MCP query tool output into QueryResult."""
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return QueryResult(columns=[], rows=[], row_count=0, execution_time=elapsed)

        # Try to detect tabular output (pipe-separated or tab-separated)
        sep = "|" if "|" in lines[0] else "\t"
        columns = [c.strip() for c in lines[0].split(sep) if c.strip()]

        rows = []
        for line in lines[1:]:
            if all(c in "-+| " for c in line):
                continue  # Skip separator lines
            cells = [c.strip() for c in line.split(sep) if c.strip()]
            if cells:
                rows.append(cells)

        return QueryResult(columns=columns, rows=rows, row_count=len(rows), execution_time=elapsed)


class DatabaseManager:
    """Manages multiple named database connections."""

    def __init__(self) -> None:
        self._connections: dict[str, DatabaseConnection] = {}

    def add_connection(
        self,
        name: str,
        db_type: str,
        connection_string: str,
        mcp_client=None,
    ) -> DatabaseConnection:
        conn = DatabaseConnection(name, db_type, connection_string, mcp_client)
        self._connections[name] = conn
        return conn

    def remove_connection(self, name: str) -> None:
        self._connections.pop(name, None)

    def get_connection(self, name: str) -> DatabaseConnection | None:
        return self._connections.get(name)

    @property
    def connection_names(self) -> list[str]:
        return list(self._connections.keys())

    @property
    def connections(self) -> dict[str, DatabaseConnection]:
        return dict(self._connections)

    async def disconnect_all(self) -> None:
        for conn in self._connections.values():
            await conn.disconnect()
