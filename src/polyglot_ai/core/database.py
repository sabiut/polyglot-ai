"""Async SQLite database with schema migrations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    model      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content         TEXT,
    tool_calls      TEXT,
    tool_call_id    TEXT,
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    detail     TEXT
);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    file_path       TEXT,
    file_size       INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id);
"""

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    content     TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'custom',
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Latest schema version
LATEST_VERSION = 4


class Database:
    """Async SQLite database wrapper with migration support."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        assert self._conn is not None
        current = await self._get_version()

        # Each migration is an explicit function for clarity and maintainability
        migration_steps = {
            1: self._migrate_v1,
            2: self._migrate_v2,
            3: self._migrate_v3,
            4: self._migrate_v4,
        }
        assert max(migration_steps) == LATEST_VERSION, (
            f"LATEST_VERSION ({LATEST_VERSION}) != max migration ({max(migration_steps)})"
        )

        for version in sorted(migration_steps):
            if version > current:
                logger.info("Running migration v%d", version)
                await migration_steps[version]()
                await self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                await self._conn.commit()

    async def _migrate_v1(self) -> None:
        """Initial schema: settings, conversations, messages, audit_log."""
        await self._conn.executescript(SCHEMA_V1)

    # Pre-declared ALTER TABLE statements for migrations.
    # All identifiers are constants — no dynamic SQL construction needed.
    _V2_ALTERS = [
        (
            "conversations",
            "pinned",
            "ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "conversations",
            "archived",
            "ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        ),
    ]
    _V3_ALTERS = [
        (
            "conversations",
            "parent_conversation_id",
            "ALTER TABLE conversations ADD COLUMN parent_conversation_id INTEGER",
        ),
        (
            "conversations",
            "fork_point_message_id",
            "ALTER TABLE conversations ADD COLUMN fork_point_message_id INTEGER",
        ),
    ]
    _V4_ALTERS = [
        (
            "conversations",
            "category",
            "ALTER TABLE conversations ADD COLUMN category TEXT NOT NULL DEFAULT 'all'",
        ),
    ]

    async def _migrate_v2(self) -> None:
        """Add attachments table and conversation columns."""
        await self._conn.executescript(SCHEMA_V2_SQL)
        for table, column, stmt in self._V2_ALTERS:
            if not await self._column_exists(table, column):
                await self._conn.execute(stmt)

    async def _migrate_v3(self) -> None:
        """Add conversation branching columns and prompt templates table."""
        await self._conn.executescript(SCHEMA_V3_SQL)
        for table, column, stmt in self._V3_ALTERS:
            if not await self._column_exists(table, column):
                await self._conn.execute(stmt)

    async def _migrate_v4(self) -> None:
        """Add conversation category column for standalone chat mode."""
        for table, column, stmt in self._V4_ALTERS:
            if not await self._column_exists(table, column):
                await self._conn.execute(stmt)

    # Tables and columns that may be referenced in _column_exists().
    # Only these identifiers are allowed in the PRAGMA query.
    _VALID_TABLES = frozenset(
        {
            "conversations",
            "messages",
            "attachments",
            "prompt_templates",
            "settings",
            "audit_log",
            "schema_version",
        }
    )
    _VALID_COLUMNS = frozenset(
        {"pinned", "archived", "parent_conversation_id", "fork_point_message_id", "category"}
    )

    async def _column_exists(self, table: str, column: str) -> bool:
        """Check if a column already exists in a table.

        Only accepts identifiers from the internal allowlist to prevent
        SQL injection if this method is ever called with external input.
        """
        assert self._conn is not None
        if table not in self._VALID_TABLES:
            raise ValueError(f"Invalid table name: {table}")
        if column not in self._VALID_COLUMNS:
            raise ValueError(f"Invalid column name: {column}")
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        return any(row[1] == column for row in rows)

    async def _get_version(self) -> int:
        assert self._conn is not None
        try:
            cursor = await self._conn.execute("SELECT MAX(version) FROM schema_version")
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            return 0

    # ── Internal SQL helpers ──────────────────────────────────────────
    # These accept raw SQL and MUST only be called with trusted,
    # hardcoded query strings — never with user/AI-generated SQL.
    # All callers must use parameterized placeholders (?) for values.

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a trusted SQL statement. Use parameterized queries only."""
        assert self._conn is not None
        if not isinstance(params, tuple):
            raise TypeError("params must be a tuple — use (value,) for single values")
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor

    async def execute_many(self, statements: list[tuple[str, tuple]]) -> None:
        """Execute multiple trusted statements in a single transaction."""
        assert self._conn is not None
        try:
            for sql, params in statements:
                await self._conn.execute(sql, params)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Fetch one row from a trusted SQL query."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows from a trusted SQL query."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # Convenience methods for conversations and messages

    async def create_conversation(
        self,
        title: str,
        model: str,
        category: str = "all",
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO conversations (title, model, category) VALUES (?, ?, ?)",
            (title, model, category),
        )
        return cursor.lastrowid

    async def list_conversations(self, category: str | None = None) -> list[dict]:
        if category and category != "all":
            return await self.fetchall(
                "SELECT * FROM conversations WHERE category = ? ORDER BY updated_at DESC",
                (category,),
            )
        return await self.fetchall("SELECT * FROM conversations ORDER BY updated_at DESC")

    async def insert_message(
        self,
        conversation_id: int,
        role: str,
        content: str | None = None,
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> int:
        tc_json = json.dumps(tool_calls) if tool_calls else None
        cursor = await self.execute(
            """INSERT INTO messages
               (conversation_id, role, content, tool_calls, tool_call_id,
                model, tokens_in, tokens_out)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, role, content, tc_json, tool_call_id, model, tokens_in, tokens_out),
        )
        await self.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
            (conversation_id,),
        )
        return cursor.lastrowid

    async def get_messages(self, conversation_id: int) -> list[dict]:
        rows = await self.fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        )
        for row in rows:
            if row.get("tool_calls"):
                row["tool_calls"] = json.loads(row["tool_calls"])
        return rows

    async def rename_conversation(self, conv_id: int, title: str) -> None:
        await self.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, conv_id),
        )

    async def delete_conversation(self, conv_id: int) -> None:
        # Messages and attachments are deleted via ON DELETE CASCADE
        await self.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    async def search_conversations(self, query: str) -> list[dict]:
        return await self.fetchall(
            """SELECT DISTINCT c.* FROM conversations c
               LEFT JOIN messages m ON m.conversation_id = c.id
               WHERE c.title LIKE ? OR m.content LIKE ?
               ORDER BY c.updated_at DESC""",
            (f"%{query}%", f"%{query}%"),
        )

    async def pin_conversation(self, conv_id: int, pinned: bool = True) -> None:
        await self.execute(
            "UPDATE conversations SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, conv_id),
        )

    async def insert_attachment(
        self,
        message_id: int,
        filename: str,
        mime_type: str,
        file_path: str | None = None,
        file_size: int | None = None,
    ) -> int:
        cursor = await self.execute(
            """INSERT INTO attachments (message_id, filename, mime_type, file_path, file_size)
               VALUES (?, ?, ?, ?, ?)""",
            (message_id, filename, mime_type, file_path, file_size),
        )
        return cursor.lastrowid

    async def get_attachments(self, message_id: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM attachments WHERE message_id = ? ORDER BY id",
            (message_id,),
        )

    async def log_audit(self, event_type: str, detail: dict | None = None) -> None:
        detail_json = json.dumps(detail) if detail else None
        await self.execute(
            "INSERT INTO audit_log (event_type, detail) VALUES (?, ?)",
            (event_type, detail_json),
        )

    # Conversation forking

    async def fork_conversation(
        self,
        conv_id: int,
        fork_message_id: int,
    ) -> int:
        """Create a new conversation forking from a specific message.

        Runs in a single transaction for atomicity and performance.
        """
        assert self._conn is not None
        conv = await self.fetchone(
            "SELECT title, model FROM conversations WHERE id = ?",
            (conv_id,),
        )
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        messages = await self.fetchall(
            "SELECT id, role, content, tool_calls, tool_call_id, model, tokens_in, tokens_out "
            "FROM messages WHERE conversation_id = ? AND id <= ? ORDER BY id",
            (conv_id, fork_message_id),
        )

        try:
            # Create forked conversation
            cursor = await self._conn.execute(
                """INSERT INTO conversations
                   (title, model, parent_conversation_id, fork_point_message_id)
                   VALUES (?, ?, ?, ?)""",
                (f"{conv['title']} (fork)", conv["model"], conv_id, fork_message_id),
            )
            new_conv_id = cursor.lastrowid

            # Copy messages and their attachments
            for msg in messages:
                cursor2 = await self._conn.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, tool_calls, tool_call_id,
                        model, tokens_in, tokens_out)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_conv_id,
                        msg["role"],
                        msg["content"],
                        msg["tool_calls"],
                        msg["tool_call_id"],
                        msg["model"],
                        msg["tokens_in"],
                        msg["tokens_out"],
                    ),
                )
                new_msg_id = cursor2.lastrowid

                attachments = await self.fetchall(
                    "SELECT filename, mime_type, file_path, file_size "
                    "FROM attachments WHERE message_id = ?",
                    (msg["id"],),
                )
                for att in attachments:
                    await self._conn.execute(
                        """INSERT INTO attachments
                           (message_id, filename, mime_type, file_path, file_size)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            new_msg_id,
                            att["filename"],
                            att["mime_type"],
                            att["file_path"],
                            att["file_size"],
                        ),
                    )

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return new_conv_id

    # Prompt templates

    async def list_prompt_templates(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM prompt_templates ORDER BY is_builtin DESC, name")

    async def create_prompt_template(
        self,
        name: str,
        content: str,
        category: str = "custom",
        is_builtin: bool = False,
    ) -> int:
        cursor = await self.execute(
            """INSERT INTO prompt_templates (name, content, category, is_builtin)
               VALUES (?, ?, ?, ?)""",
            (name, content, category, 1 if is_builtin else 0),
        )
        return cursor.lastrowid

    async def update_prompt_template(
        self,
        template_id: int,
        name: str,
        content: str,
    ) -> None:
        await self.execute(
            "UPDATE prompt_templates SET name = ?, content = ?, updated_at = datetime('now') WHERE id = ?",
            (name, content, template_id),
        )

    async def delete_prompt_template(self, template_id: int) -> None:
        await self.execute(
            "DELETE FROM prompt_templates WHERE id = ? AND is_builtin = 0",
            (template_id,),
        )
