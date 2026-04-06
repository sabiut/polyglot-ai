"""Database AI tools — let the AI query database schemas and data."""

from __future__ import annotations

import json
import logging

from polyglot_ai.core.db_explorer import get_global_db_manager

logger = logging.getLogger(__name__)

# SQL statements that are read-only and safe to auto-approve
_READONLY_PREFIXES = ("SELECT", "WITH", "SHOW", "DESCRIBE", "EXPLAIN", "PRAGMA")


def is_readonly_query(sql: str) -> bool:
    """Check if a SQL query is read-only (safe to auto-approve)."""
    stripped = sql.strip().upper()
    # Strip comments and whitespace
    while stripped.startswith("--"):
        newline = stripped.find("\n")
        if newline < 0:
            return False
        stripped = stripped[newline + 1 :].lstrip()
    return stripped.startswith(_READONLY_PREFIXES)


async def db_list_connections(args: dict) -> str:
    """List all available database connections."""
    mgr = get_global_db_manager()
    names = mgr.connection_names
    if not names:
        return "No database connections configured. Add one via the Database panel."

    lines = [f"Found {len(names)} connection(s):\n"]
    for name in names:
        conn = mgr.get_connection(name)
        if conn:
            lines.append(f"- {name} (type: {conn.db_type})")
    return "\n".join(lines)


async def db_get_schema(args: dict) -> str:
    """Get the schema (tables and columns) of a database."""
    conn_name = args.get("connection", "") or args.get("name", "")
    if not conn_name:
        return "Error: 'connection' name is required. Use db_list_connections to see available connections."

    mgr = get_global_db_manager()
    conn = mgr.get_connection(conn_name)
    if not conn:
        return f"Error: Connection '{conn_name}' not found. Available: {', '.join(mgr.connection_names) or '(none)'}"

    # Ensure connected
    try:
        if conn.db_type == "sqlite" and not conn._sqlite_conn:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting to '{conn_name}': {msg}"
        elif conn.db_type == "postgresql" and not conn._pg_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting to '{conn_name}': {msg}"
        elif conn.db_type == "mysql" and not conn._mysql_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting to '{conn_name}': {msg}"
    except Exception as e:
        return f"Error: {e}"

    try:
        tables = await conn.get_schema()
    except Exception as e:
        return f"Error getting schema: {e}"

    if not tables:
        return f"Database '{conn_name}' has no tables."

    lines = [f"Database '{conn_name}' has {len(tables)} table(s):\n"]
    for table in tables:
        lines.append(f"\n## {table.name}")
        for col in table.columns:
            pk = " [PK]" if col.primary_key else ""
            nn = "" if col.nullable else " NOT NULL"
            lines.append(f"  - {col.name}: {col.data_type}{nn}{pk}")
    return "\n".join(lines)


async def db_query(args: dict) -> str:
    """Execute a SQL query against a database connection."""
    conn_name = args.get("connection", "") or args.get("name", "")
    sql = args.get("sql", "") or args.get("query", "")
    max_rows = args.get("max_rows", 100)

    if not conn_name:
        return "Error: 'connection' name is required."
    if not sql:
        return "Error: 'sql' query is required."

    if not isinstance(max_rows, int):
        try:
            max_rows = int(max_rows)
        except (ValueError, TypeError):
            max_rows = 100

    mgr = get_global_db_manager()
    conn = mgr.get_connection(conn_name)
    if not conn:
        return f"Error: Connection '{conn_name}' not found. Available: {', '.join(mgr.connection_names) or '(none)'}"

    # Ensure connected
    try:
        if conn.db_type == "sqlite" and not conn._sqlite_conn:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
        elif conn.db_type == "postgresql" and not conn._pg_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
        elif conn.db_type == "mysql" and not conn._mysql_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
    except Exception as e:
        return f"Error: {e}"

    try:
        result = await conn.execute_query(sql, max_rows=max_rows)
    except Exception as e:
        return f"Query error: {e}"

    if result.error:
        return f"Query error: {result.error}"

    # Non-SELECT result
    if not result.columns:
        affected = result.affected_rows
        if affected is not None:
            return f"Query OK. {affected} row(s) affected."
        return "Query OK."

    # Format as readable table
    if not result.rows:
        return f"Query returned 0 rows. Columns: {', '.join(result.columns)}"

    # Build output
    lines = [f"Query returned {result.row_count} row(s) in {result.execution_time:.3f}s\n"]
    lines.append("Columns: " + ", ".join(result.columns))
    lines.append("")

    # Return first N rows as JSON for clarity
    display_rows = result.rows[:50]
    for i, row in enumerate(display_rows, 1):
        row_data = {
            col: (str(val) if val is not None else None) for col, val in zip(result.columns, row)
        }
        lines.append(f"Row {i}: {json.dumps(row_data, default=str)}")

    if len(result.rows) > 50:
        lines.append(f"\n... ({len(result.rows) - 50} more rows not shown)")

    return "\n".join(lines)


async def db_execute(args: dict) -> str:
    """Execute a write SQL statement (INSERT/UPDATE/DELETE/DDL). Requires approval."""
    conn_name = args.get("connection", "") or args.get("name", "")
    sql = args.get("sql", "") or args.get("query", "")

    if not conn_name:
        return "Error: 'connection' name is required."
    if not sql:
        return "Error: 'sql' statement is required."

    mgr = get_global_db_manager()
    conn = mgr.get_connection(conn_name)
    if not conn:
        return f"Error: Connection '{conn_name}' not found. Available: {', '.join(mgr.connection_names) or '(none)'}"

    # Ensure connected
    try:
        if conn.db_type == "sqlite" and not conn._sqlite_conn:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
        elif conn.db_type == "postgresql" and not conn._pg_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
        elif conn.db_type == "mysql" and not conn._mysql_pool:
            ok, msg = await conn.connect()
            if not ok:
                return f"Error connecting: {msg}"
    except Exception as e:
        return f"Error: {e}"

    try:
        result = await conn.execute_query(sql)
    except Exception as e:
        return f"Statement error: {e}"

    if result.error:
        return f"Statement error: {result.error}"

    affected = result.affected_rows
    if affected is not None:
        return f"Statement executed. {affected} row(s) affected."
    return "Statement executed successfully."
