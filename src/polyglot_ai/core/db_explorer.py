"""Database explorer — unified connection layer for PostgreSQL, SQLite, MySQL."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Identifier validation — only allow safe table/column names
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]*$")


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
    affected_rows: int | None = None  # For non-SELECT statements

    @staticmethod
    def from_error(error: str) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0, execution_time=0, error=error)


def _safe_identifier(name: str) -> str:
    """Quote a SQL identifier safely for interpolation into PRAGMA/DESCRIBE.

    Removes one balanced surrounding quote pair if present, then quotes
    with double-quotes. Raises ValueError for dangerous characters.
    """
    # Remove one balanced surrounding quote pair only
    if len(name) >= 2:
        if (
            (name[0] == '"' and name[-1] == '"')
            or (name[0] == "`" and name[-1] == "`")
            or (name[0] == "'" and name[-1] == "'")
        ):
            name = name[1:-1]
    # Reject clearly dangerous patterns
    if not name or "\x00" in name or ";" in name or "--" in name:
        raise ValueError(f"Unsafe identifier: {name!r}")
    if _SAFE_IDENTIFIER_RE.match(name):
        return f'"{name}"'
    # For names with special chars (spaces, etc.), escape double quotes
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


class DatabaseConnection:
    """Abstraction over different database backends."""

    def __init__(
        self,
        name: str,
        db_type: str,
        connection_string: str,
        mcp_client=None,
        *,
        read_only: bool = True,
    ) -> None:
        self.name = name
        self.db_type = db_type  # "sqlite" | "postgresql" | "mysql"
        self._connection_string = connection_string
        self._mcp_client = mcp_client
        self.read_only = read_only
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._pg_pool = None  # asyncpg connection pool
        self._mysql_pool = None  # aiomysql connection pool
        self._lock = asyncio.Lock()  # Prevent concurrent connect/disconnect/query

    async def connect(self) -> tuple[bool, str]:
        """Test and establish connection. Returns (success, message)."""
        async with self._lock:
            if self.db_type == "sqlite":
                return await self._connect_sqlite()
            elif self.db_type == "postgresql":
                return await self._connect_postgresql()
            elif self.db_type == "mysql":
                return await self._connect_mysql()
            return False, f"Unknown database type: {self.db_type}"

    async def disconnect(self) -> None:
        async with self._lock:
            if self._sqlite_conn:
                try:
                    await self._sqlite_conn.close()
                except Exception:
                    pass
                self._sqlite_conn = None
            if self._pg_pool:
                try:
                    await self._pg_pool.close()
                except Exception:
                    pass
                self._pg_pool = None
            if self._mysql_pool:
                try:
                    self._mysql_pool.close()
                    await self._mysql_pool.wait_closed()
                except Exception:
                    pass
                self._mysql_pool = None

    async def get_schema(self) -> list[TableInfo]:
        async with self._lock:
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
        # Enforce read-only policy at the connection level (defense-in-depth).
        if self.read_only:
            from polyglot_ai.core.ai.tools.db_tools import is_readonly_query

            if not is_readonly_query(sql):
                return QueryResult.from_error(
                    f"Connection '{self.name}' is read-only. "
                    "Enable write access in the Database panel to run this statement."
                )
        async with self._lock:
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
            conn_str = self._connection_string
            # Allow special SQLite connection strings
            if conn_str in (":memory:", ""):
                self._sqlite_conn = await aiosqlite.connect(conn_str)
            elif conn_str.startswith("file:"):
                # URI-style connection string
                self._sqlite_conn = await aiosqlite.connect(conn_str, uri=True)
            else:
                path = Path(conn_str)
                if not path.exists() and not path.parent.exists():
                    return False, f"Directory not found: {path.parent}"
                self._sqlite_conn = await aiosqlite.connect(str(path))
            self._sqlite_conn.row_factory = aiosqlite.Row
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    async def _schema_sqlite(self) -> list[TableInfo]:
        if not self._sqlite_conn:
            return []
        try:
            # Filter out internal sqlite tables
            cursor = await self._sqlite_conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            table_rows = await cursor.fetchall()
            await cursor.close()

            tables = []
            for row in table_rows:
                table_name = row[0]
                safe_name = _safe_identifier(table_name)
                col_cursor = await self._sqlite_conn.execute(
                    f"PRAGMA table_info({safe_name})"  # noqa: S608
                )
                col_rows = await col_cursor.fetchall()
                await col_cursor.close()

                columns = []
                for col in col_rows:
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
            elapsed = time.monotonic() - start

            # Non-row-returning statements (INSERT, UPDATE, DELETE, etc.)
            if cursor.description is None:
                affected = cursor.rowcount if cursor.rowcount >= 0 else None
                await cursor.close()
                try:
                    await self._sqlite_conn.commit()
                except Exception:
                    await self._sqlite_conn.rollback()
                    raise
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time=elapsed,
                    affected_rows=affected,
                )

            rows_raw = await cursor.fetchmany(max_rows)
            columns = [desc[0] for desc in cursor.description]
            rows = [list(row) for row in rows_raw]
            await cursor.close()
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
            async with self._pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True, "Connected (direct)"
        except ImportError:
            return await self._connect_mcp()
        except Exception as e:
            # Try MCP as fallback if direct connection fails
            if self._mcp_client:
                mcp_result = await self._connect_mcp()
                if mcp_result[0]:
                    return True, f"Direct connection failed ({e}); connected via MCP instead"
            return False, str(e)

    async def _schema_postgresql(self) -> list[TableInfo]:
        if not self._pg_pool:
            return []
        try:
            async with self._pg_pool.acquire() as conn:
                # Single bulk query for all tables and columns (avoids N+1)
                col_rows = await conn.fetch(
                    "SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, "
                    "  c.ordinal_position, "
                    "  CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_pk "
                    "FROM information_schema.columns c "
                    "LEFT JOIN ( "
                    "  SELECT k.table_name, k.column_name "
                    "  FROM information_schema.key_column_usage k "
                    "  JOIN information_schema.table_constraints tc "
                    "    ON k.constraint_name = tc.constraint_name "
                    "    AND k.table_schema = tc.table_schema "
                    "  WHERE tc.constraint_type = 'PRIMARY KEY' "
                    "    AND tc.table_schema = 'public' "
                    ") pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name "
                    "WHERE c.table_schema = 'public' "
                    "ORDER BY c.table_name, c.ordinal_position"
                )

                # Group columns by table
                tables_map: dict[str, list[ColumnInfo]] = {}
                for cr in col_rows:
                    tname = cr["table_name"]
                    if tname not in tables_map:
                        tables_map[tname] = []
                    tables_map[tname].append(
                        ColumnInfo(
                            name=cr["column_name"],
                            data_type=cr["data_type"],
                            nullable=cr["is_nullable"] == "YES",
                            primary_key=cr["is_pk"] > 0,
                        )
                    )

                return [TableInfo(name=tname, columns=cols) for tname, cols in tables_map.items()]
        except Exception:
            logger.exception("Failed to get PostgreSQL schema")
            return []

    async def _execute_postgresql(self, sql: str, max_rows: int) -> QueryResult:
        if not self._pg_pool:
            return QueryResult.from_error("Not connected")
        try:
            start = time.monotonic()
            async with self._pg_pool.acquire() as conn:
                # Detect if this is a row-returning statement
                sql_upper = sql.strip().upper()
                is_select = sql_upper.startswith(("SELECT", "WITH", "EXPLAIN", "SHOW"))

                if is_select:
                    # Fetch rows — client-side cap at max_rows
                    rows_raw = await conn.fetch(sql)
                    elapsed = time.monotonic() - start
                    rows_raw = rows_raw[:max_rows]

                    if not rows_raw:
                        return QueryResult(columns=[], rows=[], row_count=0, execution_time=elapsed)

                    columns = list(rows_raw[0].keys())
                    rows = [list(row.values()) for row in rows_raw]
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
                else:
                    # Non-SELECT: execute and return affected row count
                    result = await conn.execute(sql)
                    elapsed = time.monotonic() - start
                    # asyncpg returns status string like "DELETE 3"
                    affected = None
                    if result:
                        parts = result.split()
                        if len(parts) >= 2 and parts[-1].isdigit():
                            affected = int(parts[-1])
                    return QueryResult(
                        columns=[],
                        rows=[],
                        row_count=0,
                        execution_time=elapsed,
                        affected_rows=affected,
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
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            return True, "Connected (direct)"
        except ImportError:
            return await self._connect_mcp()
        except Exception as e:
            if self._mcp_client:
                mcp_result = await self._connect_mcp()
                if mcp_result[0]:
                    return True, f"Direct connection failed ({e}); connected via MCP instead"
            return False, str(e)

    async def _schema_mysql(self) -> list[TableInfo]:
        if not self._mysql_pool:
            return []
        try:
            async with self._mysql_pool.acquire() as conn:
                # Single bulk query for all tables and columns (avoids N+1)
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.COLUMN_TYPE, "
                        "  c.IS_NULLABLE, c.COLUMN_KEY "
                        "FROM information_schema.COLUMNS c "
                        "WHERE c.TABLE_SCHEMA = DATABASE() "
                        "ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION"
                    )
                    col_rows = await cur.fetchall()

                # Group columns by table
                tables_map: dict[str, list[ColumnInfo]] = {}
                for cr in col_rows:
                    tname = cr[0]
                    if tname not in tables_map:
                        tables_map[tname] = []
                    tables_map[tname].append(
                        ColumnInfo(
                            name=cr[1],
                            data_type=cr[2],
                            nullable=cr[3] == "YES",
                            primary_key=cr[4] == "PRI",
                        )
                    )

                return [TableInfo(name=tname, columns=cols) for tname, cols in tables_map.items()]
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
                    elapsed = time.monotonic() - start

                    # Non-row-returning statement
                    if cur.description is None:
                        try:
                            await conn.commit()
                        except Exception:
                            await conn.rollback()
                            raise
                        return QueryResult(
                            columns=[],
                            rows=[],
                            row_count=0,
                            execution_time=elapsed,
                            affected_rows=cur.rowcount if cur.rowcount >= 0 else None,
                        )

                    rows_raw = await cur.fetchmany(max_rows)
                    if not rows_raw:
                        columns = [d[0] for d in cur.description]
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
            schema_tools = [
                name
                for name, tool in self._mcp_client.available_tools.items()
                if tool.server_name == server_name
                and any(k in tool.name.lower() for k in ("schema", "list", "describe", "tables"))
            ]
            if not schema_tools:
                return []
            result = await self._mcp_client.call_tool(schema_tools[0], {})
            return self._parse_schema_text(result)
        except Exception:
            logger.exception("Failed to get MCP schema")
            return []

    # MCP tool names known to be read-only (query/select only).
    # These are checked first; write-capable names like "execute" and
    # "run" are only used as a fallback when the SQL has already passed
    # the read-only validation in execute_query().
    _MCP_READ_ONLY_KEYWORDS = ("read_query", "query", "select")
    _MCP_WRITE_KEYWORDS = ("execute", "run", "write_query", "run_query")

    async def _execute_mcp(self, sql: str) -> QueryResult:
        if not self._mcp_client:
            return QueryResult.from_error("MCP client not available")
        try:
            server_name = "postgres" if self.db_type == "postgresql" else "mysql"

            # Separate read-only tools from write-capable tools so we
            # always prefer the read-only tool for SELECT queries.
            read_tools: list[str] = []
            write_tools: list[str] = []
            for name, tool in self._mcp_client.available_tools.items():
                if tool.server_name != server_name:
                    continue
                lower = tool.name.lower()
                if any(k in lower for k in self._MCP_READ_ONLY_KEYWORDS):
                    read_tools.append(name)
                elif any(k in lower for k in self._MCP_WRITE_KEYWORDS):
                    write_tools.append(name)

            # Prefer read-only tool; fall back to write tool only if no
            # read-only tool exists (the SQL has already been validated
            # as read-only by execute_query before reaching here).
            query_tools = read_tools or write_tools
            if not query_tools:
                return QueryResult.from_error(f"No query tool found for {server_name}")

            start = time.monotonic()
            result = await self._mcp_client.call_tool(query_tools[0], {"query": sql})
            elapsed = time.monotonic() - start

            return self._parse_query_result(result, elapsed)
        except Exception as e:
            return QueryResult.from_error(str(e))

    # Generic headings to skip when parsing MCP schema output
    _SCHEMA_IGNORE = frozenset(
        {
            "tables",
            "schema",
            "columns",
            "database",
            "result",
            "output",
            "information",
            "public",
        }
    )

    @staticmethod
    def _parse_schema_text(text: str) -> list[TableInfo]:
        """Best-effort parse of MCP schema tool output into TableInfo list.

        This is a heuristic parser — MCP tool output formats vary.
        Skips generic section headings like "Tables:", "Schema:".
        Returns empty list if format is unrecognizable.
        """
        tables: list[TableInfo] = []
        current_table: str | None = None
        current_columns: list[ColumnInfo] = []

        raw_lines = text.splitlines()
        for raw_line in raw_lines:
            stripped = raw_line.strip()
            if not stripped or all(c in "-+= " for c in stripped):
                continue
            # Detect table header: line ending with ":" that isn't a column
            if stripped.endswith(":") and "|" not in stripped and "\t" not in stripped:
                candidate = stripped.rstrip(":").strip()
                # Skip generic section headings
                if candidate.lower() in DatabaseConnection._SCHEMA_IGNORE:
                    continue
                if not candidate or candidate.startswith("("):
                    continue
                if current_table:
                    tables.append(TableInfo(name=current_table, columns=current_columns))
                current_table = candidate
                current_columns = []
            elif current_table and ("│" in raw_line or "|" in raw_line or "\t" in raw_line):
                parts = [p.strip() for p in raw_line.replace("│", "|").split("|")]
                parts = [p for i, p in enumerate(parts) if p or (0 < i < len(parts) - 1)]
                if len(parts) >= 2:
                    current_columns.append(ColumnInfo(name=parts[0], data_type=parts[1]))

        if current_table:
            tables.append(TableInfo(name=current_table, columns=current_columns))
        return tables

    @staticmethod
    def _parse_pipe_parts(line: str, sep: str) -> list[str]:
        """Split a line by separator, trimming outer empty cells for pipe format."""
        parts = line.split(sep)
        if sep == "|":
            if parts and not parts[0].strip():
                parts = parts[1:]
            if parts and not parts[-1].strip():
                parts = parts[:-1]
        return [c.strip() for c in parts]

    @staticmethod
    def _parse_query_result(text: str, elapsed: float) -> QueryResult:
        """Best-effort parse of MCP query tool output into QueryResult.

        Finds the first tabular line as header (not generic preamble).
        Preserves empty cells and normalizes row length.
        """
        lines = text.splitlines()
        if not lines:
            return QueryResult(columns=[], rows=[], row_count=0, execution_time=elapsed)

        # Find the first line that looks tabular (has separator + multiple cells)
        header_idx = -1
        sep = "\t"
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or all(c in "-+|= " for c in stripped):
                continue
            if "|" in line:
                parts = DatabaseConnection._parse_pipe_parts(line, "|")
                if len(parts) >= 2:
                    header_idx = i
                    sep = "|"
                    break
            elif "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    header_idx = i
                    sep = "\t"
                    break

        if header_idx < 0:
            # No tabular data found — return as single-column raw text
            content = [line for line in lines if line.strip()]
            if content:
                return QueryResult(
                    columns=["output"],
                    rows=[[line] for line in content],
                    row_count=len(content),
                    execution_time=elapsed,
                )
            return QueryResult(columns=[], rows=[], row_count=0, execution_time=elapsed)

        columns = DatabaseConnection._parse_pipe_parts(lines[header_idx], sep)
        num_cols = len(columns)

        # Common footer/status patterns to skip
        _FOOTER_RE = re.compile(
            r"^\(?(\d+)\s+(rows?|records?)\b|^query\s+(ok|successful)|^time:|^rows?\s+affected",
            re.IGNORECASE,
        )

        rows = []
        for line in lines[header_idx + 1 :]:
            stripped = line.strip()
            if not stripped or all(c in "-+|= " for c in stripped):
                continue
            # Skip footer/status lines
            if _FOOTER_RE.match(stripped):
                continue
            # Only accept lines that have the same separator as the header
            if sep == "|" and "|" not in line:
                continue
            cells = DatabaseConnection._parse_pipe_parts(line, sep)
            # Normalize row length to match header
            if len(cells) < num_cols:
                cells.extend([""] * (num_cols - len(cells)))
            elif len(cells) > num_cols:
                cells = cells[:num_cols]
            rows.append(cells)

        return QueryResult(columns=columns, rows=rows, row_count=len(rows), execution_time=elapsed)


class DatabaseManager:
    """Manages multiple named database connections."""

    def __init__(self) -> None:
        self._connections: dict[str, DatabaseConnection] = {}

    async def add_connection(
        self,
        name: str,
        db_type: str,
        connection_string: str,
        mcp_client=None,
        *,
        read_only: bool = True,
    ) -> DatabaseConnection:
        # Disconnect existing connection with same name if present
        if name in self._connections:
            try:
                await self._connections[name].disconnect()
            except Exception:
                pass
        conn = DatabaseConnection(name, db_type, connection_string, mcp_client, read_only=read_only)
        self._connections[name] = conn
        return conn

    def add_connection_sync(
        self,
        name: str,
        db_type: str,
        connection_string: str,
        mcp_client=None,
        *,
        read_only: bool = True,
    ) -> DatabaseConnection:
        """Synchronous add — for use during config loading (no active connections)."""
        conn = DatabaseConnection(name, db_type, connection_string, mcp_client, read_only=read_only)
        self._connections[name] = conn
        return conn

    async def remove_connection(self, name: str) -> None:
        conn = self._connections.pop(name, None)
        if conn:
            await conn.disconnect()

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
            try:
                await conn.disconnect()
            except Exception:
                logger.exception("Error disconnecting %s", conn.name)


# ── Global DatabaseManager singleton ────────────────────────────────
# Shared between the DatabasePanel UI and the AI tools so that when
# a user connects to a database, the AI can query the same connection.

_global_db_manager: DatabaseManager | None = None


def get_global_db_manager() -> DatabaseManager:
    """Return the shared DatabaseManager instance."""
    global _global_db_manager
    if _global_db_manager is None:
        _global_db_manager = DatabaseManager()
    return _global_db_manager
