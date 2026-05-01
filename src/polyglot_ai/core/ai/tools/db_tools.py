"""Database AI tools — let the AI query database schemas and data."""

from __future__ import annotations

import json
import logging
import re

from polyglot_ai.core.db_explorer import get_global_db_manager

logger = logging.getLogger(__name__)


def _sanitize_db_error(err: Exception) -> str:
    """Strip connection strings, credentials, and internal paths from DB errors.

    Database drivers often embed DSN strings, usernames, passwords, or
    host:port in their exception messages. We scrub known patterns and
    truncate the message to prevent leaking internals to the AI.
    """
    msg = str(err)
    # Strip anything that looks like a connection URI
    msg = re.sub(
        r"(mysql|postgres|postgresql|sqlite|mssql|mongodb)(\+\w+)?://[^\s'\"]+",
        r"\1://***",
        msg,
        flags=re.IGNORECASE,
    )
    # Strip host:port patterns after @ (user:pass@host:port)
    msg = re.sub(r"@[\w.\-]+:\d+", "@***:***", msg)
    # Strip password= fragments
    msg = re.sub(r"password\s*=\s*\S+", "password=***", msg, flags=re.IGNORECASE)
    # Truncate to avoid dumping multi-line driver tracebacks
    if len(msg) > 300:
        msg = msg[:300] + "... (truncated)"
    return msg


# SQL statements that are read-only and safe to auto-approve.
# Note: WITH is intentionally NOT in this list because Postgres supports
# data-modifying CTEs (WITH x AS (DELETE FROM t RETURNING *) SELECT ...).
# WITH is handled specially after confirming no write keywords appear.
_READONLY_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "PRAGMA", "VALUES")

# Any of these tokens outside of string literals means the query is a write.
# Matched as whole words only.
_WRITE_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "GRANT",
    "REVOKE",
    "MERGE",
    "CALL",
    "EXEC",
    "EXECUTE",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
    "COPY",
    "LOCK",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "SET",
    "RESET",
    "LOAD",
    "UNLOAD",
)

_WRITE_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
# Strip single-quoted, double-quoted, and backtick-quoted strings so keywords
# inside string literals don't trigger the write-keyword check.
_STRING_LITERAL_RE = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`",
    re.DOTALL,
)


def _strip_comments_and_strings(sql: str) -> str:
    """Remove SQL comments and string literals for keyword scanning.

    Strings are stripped **before** line comments because a ``--``
    sequence is valid content inside a quoted string (e.g.
    ``SELECT '"; DROP TABLE x--' AS s``). Stripping comments first
    would consume everything from the inline ``--`` to end-of-line,
    leaving the opening quote of the literal dangling so the literal
    no longer matches the string regex — and the bare ``DROP`` then
    leaks into the keyword scan.
    """
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _STRING_LITERAL_RE.sub("''", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    return sql


def is_readonly_query(sql: str) -> bool:
    """Check if a SQL query is read-only (safe to auto-approve).

    Rejects:
    - Empty queries
    - Multi-statement queries (anything with `;` followed by non-whitespace)
    - Queries containing any write keyword outside of string literals
    - Queries whose first non-comment token is not in _READONLY_PREFIXES
      (with a special allowance for pure SELECT-only CTEs starting with WITH)
    """
    if not sql or not sql.strip():
        return False

    cleaned = _strip_comments_and_strings(sql).strip()
    if not cleaned:
        return False

    # Reject stacked statements: any `;` followed by more non-whitespace content.
    # A single trailing semicolon is fine.
    stripped_trailing = cleaned.rstrip().rstrip(";").rstrip()
    if ";" in stripped_trailing:
        return False

    # Any write keyword outside string literals / comments is a write.
    if _WRITE_KEYWORD_RE.search(cleaned):
        return False

    # Check the first token is an allowed read-only keyword, or WITH
    # (which we now know contains no write keywords, so it's a SELECT CTE).
    upper = cleaned.upper().lstrip()
    if upper.startswith("WITH"):
        return True
    return upper.startswith(_READONLY_PREFIXES)


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
        return f"Error: {_sanitize_db_error(e)}"

    try:
        tables = await conn.get_schema()
    except Exception as e:
        return f"Error getting schema: {_sanitize_db_error(e)}"

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
        return f"Error: {_sanitize_db_error(e)}"

    try:
        result = await conn.execute_query(sql, max_rows=max_rows)
    except Exception as e:
        return f"Query error: {_sanitize_db_error(e)}"

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

    if conn.read_only:
        return (
            f"Error: Connection '{conn_name}' is read-only. "
            "Enable write access in the Database panel to run write statements."
        )

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
        return f"Error: {_sanitize_db_error(e)}"

    try:
        result = await conn.execute_query(sql)
    except Exception as e:
        return f"Statement error: {_sanitize_db_error(e)}"

    if result.error:
        return f"Statement error: {result.error}"

    affected = result.affected_rows
    if affected is not None:
        return f"Statement executed. {affected} row(s) affected."
    return "Statement executed successfully."
