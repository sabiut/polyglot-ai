"""A corrupt settings row must not prevent the app from starting."""

from __future__ import annotations

import asyncio

from polyglot_ai.core.settings import DEFAULTS, SettingsManager


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self, _sql, _params=()):
        return self._rows


def test_load_skips_corrupt_row_and_keeps_the_rest():
    rows = [
        {"key": "theme", "value": '"light"'},
        {"key": "editor.word_wrap", "value": "{not json"},  # truncated write
        {"key": "editor.show_line_numbers", "value": "false"},
    ]
    mgr = SettingsManager(_FakeDB(rows))
    asyncio.run(mgr.load())

    assert mgr.get("theme") == "light"
    assert mgr.get("editor.show_line_numbers") is False
    # The corrupt key falls back to its default instead of crashing load().
    assert mgr.get("editor.word_wrap") == DEFAULTS.get("editor.word_wrap")
