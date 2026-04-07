"""Persistence layer for the SQL notebook: query history and saved snippets.

Both stores live in a single SQLite database under
``~/.config/polyglot-ai/db_notebook.sqlite``. The schema is created on
first access and migrated forward as needed (currently no migrations).

The API is intentionally synchronous — both stores are tiny and only
written from UI event handlers, so the cost of a sqlite call is well
under a frame budget. Wrap in ``asyncio.to_thread`` if you ever need
to call this from a tight async loop.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    return Path.home() / ".config" / "polyglot-ai" / "db_notebook.sqlite"


@dataclass
class HistoryEntry:
    id: int
    connection: str
    sql: str
    executed_at: float  # Unix epoch seconds
    duration_ms: int  # 0 if unknown
    row_count: int  # -1 if unknown / failed
    error: str | None  # None on success


@dataclass
class Snippet:
    id: int
    connection: str
    name: str
    sql: str
    created_at: float


class DBNotebookStore:
    """SQLite-backed store for query history + named snippets."""

    HISTORY_LIMIT_PER_CONNECTION = 50

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    executed_at REAL NOT NULL,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    row_count INTEGER NOT NULL DEFAULT -1,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_history_conn_time
                    ON history(connection, executed_at DESC);

                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(connection, name)
                );
                CREATE INDEX IF NOT EXISTS idx_snippets_conn_name
                    ON snippets(connection, name);
                """
            )

    # ── History ─────────────────────────────────────────────────────

    def add_history(
        self,
        connection: str,
        sql: str,
        duration_ms: int = 0,
        row_count: int = -1,
        error: str | None = None,
    ) -> None:
        sql = sql.strip()
        if not sql:
            return
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO history(connection, sql, executed_at, duration_ms, row_count, error) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (connection, sql, time.time(), duration_ms, row_count, error),
                )
                # Trim to the most recent N entries per connection.
                c.execute(
                    "DELETE FROM history WHERE id IN ("
                    "  SELECT id FROM history WHERE connection = ? "
                    "  ORDER BY executed_at DESC LIMIT -1 OFFSET ?"
                    ")",
                    (connection, self.HISTORY_LIMIT_PER_CONNECTION),
                )
        except sqlite3.Error as e:
            logger.warning("db_notebook: could not record history: %s", e)

    def list_history(self, connection: str, limit: int = 50) -> list[HistoryEntry]:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM history WHERE connection = ? ORDER BY executed_at DESC LIMIT ?",
                    (connection, limit),
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning("db_notebook: could not load history: %s", e)
            return []
        return [
            HistoryEntry(
                id=r["id"],
                connection=r["connection"],
                sql=r["sql"],
                executed_at=r["executed_at"],
                duration_ms=r["duration_ms"],
                row_count=r["row_count"],
                error=r["error"],
            )
            for r in rows
        ]

    def clear_history(self, connection: str) -> None:
        try:
            with self._conn() as c:
                c.execute("DELETE FROM history WHERE connection = ?", (connection,))
        except sqlite3.Error as e:
            logger.warning("db_notebook: could not clear history: %s", e)

    # ── Snippets ────────────────────────────────────────────────────

    def save_snippet(self, connection: str, name: str, sql: str) -> tuple[bool, str]:
        """Insert or replace a snippet.

        Returns ``(success, message)`` so the UI can distinguish a
        validation failure ("name cannot be empty") from a backend
        failure ("disk full / permission denied"). Previously both
        cases collapsed to a bare ``False``, which left the user
        guessing.
        """
        name = name.strip()
        sql = sql.strip()
        if not name:
            return False, "Snippet name cannot be empty."
        if not sql:
            return False, "Snippet SQL cannot be empty."
        try:
            with self._conn() as c:
                # UPSERT: replace existing same-name snippet on this connection.
                c.execute(
                    "INSERT INTO snippets(connection, name, sql, created_at) "
                    "VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(connection, name) DO UPDATE SET "
                    "sql = excluded.sql, created_at = excluded.created_at",
                    (connection, name, sql, time.time()),
                )
            return True, f"Saved snippet '{name}'"
        except sqlite3.Error as e:
            logger.exception("db_notebook: could not save snippet")
            return False, f"Could not save snippet: {e}"

    def list_snippets(self, connection: str) -> list[Snippet]:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM snippets WHERE connection = ? ORDER BY name",
                    (connection,),
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning("db_notebook: could not load snippets: %s", e)
            return []
        return [
            Snippet(
                id=r["id"],
                connection=r["connection"],
                name=r["name"],
                sql=r["sql"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def delete_snippet(self, snippet_id: int) -> None:
        try:
            with self._conn() as c:
                c.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
        except sqlite3.Error as e:
            logger.warning("db_notebook: could not delete snippet: %s", e)


# Module-level singleton — both UI panels share the same store.
_store: DBNotebookStore | None = None


def get_notebook_store() -> DBNotebookStore:
    global _store
    if _store is None:
        _store = DBNotebookStore()
    return _store
