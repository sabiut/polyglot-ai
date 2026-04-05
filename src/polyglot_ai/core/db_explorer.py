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

    async def connect(self) -> tuple[bool, str]:
        """Test and establish connection. Returns (success, message)."""
        if self.db_type == "sqlite":
            return await self._connect_sqlite()
        elif self.db_type in ("postgresql", "mysql"):
            return await self._connect_mcp()
        return False, f"Unknown database type: {self.db_type}"

    async def disconnect(self) -> None:
        if self._sqlite_conn:
            await self._sqlite_conn.close()
            self._sqlite_conn = None

    async def get_schema(self) -> list[TableInfo]:
        if self.db_type == "sqlite":
            return await self._schema_sqlite()
        elif self.db_type in ("postgresql", "mysql"):
            return await self._schema_mcp()
        return []

    async def execute_query(self, sql: str, max_rows: int = 10_000) -> QueryResult:
        if self.db_type == "sqlite":
            return await self._execute_sqlite(sql, max_rows)
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

    # ── MCP (PostgreSQL / MySQL) ────────────────────────────────────

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
