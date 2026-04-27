"""Settings manager backed by SQLite."""

from __future__ import annotations

import json
from typing import Any

from polyglot_ai.core.database import Database

DEFAULTS = {
    "theme": "dark",
    "editor.font_family": "Monospace",
    "editor.font_size": 11,
    "editor.tab_size": 4,
    "editor.show_line_numbers": True,
    "editor.word_wrap": False,
    "ai.default_model": "openai:gpt-5.5",
    "ai.temperature": 0.7,
    "ai.max_tokens": 4096,
    "ai.system_prompt": "",
    "terminal.shell": "/bin/bash",
    "terminal.font_size": 11,
    # Session restore
    "session.open_tabs": [],
    "session.active_tab_index": 0,
    "session.splitter_sizes": {},
    "session.active_conversation_id": None,
    "session.window_geometry": {},
    "session.terminal_cwd": "",
    # AI features
    "editor.ai_completions": True,
    "ai.auto_context": True,
}


class SettingsManager:
    """Read/write typed settings with in-memory cache and write-through to SQLite."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[str, Any] = {}

    async def load(self) -> None:
        rows = await self._db.fetchall("SELECT key, value FROM settings")
        for row in rows:
            self._cache[row["key"]] = json.loads(row["value"])

    def get(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        return DEFAULTS.get(key)

    async def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        await self._db.execute("DELETE FROM settings WHERE key = ?", (key,))

    def get_all(self) -> dict[str, Any]:
        result = dict(DEFAULTS)
        result.update(self._cache)
        return result
