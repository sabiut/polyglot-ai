"""Tests for conversation content search, Markdown export, and the
live model dropdown — three audit findings where the machinery
existed (search_conversations, get_all_models) but nothing called it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem

from polyglot_ai.ui.panels import chat_conversation_list as ccl
from polyglot_ai.ui.panels.chat_panel import ChatPanel


def _list_with(qtbot, rows: list[tuple[str, int]]) -> QListWidget:
    lw = QListWidget()
    qtbot.addWidget(lw)
    for title, conv_id in rows:
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, conv_id)
        lw.addItem(item)
    return lw


class TestFilterBySearch:
    def test_title_match_stays_visible(self, qtbot):
        lw = _list_with(qtbot, [("Fix login bug", 1), ("Groceries", 2)])
        ccl.filter_by_search(lw, "login")
        assert not lw.item(0).isHidden()
        assert lw.item(1).isHidden()

    def test_content_match_ids_unhide_title_misses(self, qtbot):
        lw = _list_with(qtbot, [("Fix login bug", 1), ("Groceries", 2)])
        ccl.filter_by_search(lw, "login", content_match_ids={2})
        assert not lw.item(0).isHidden()  # title hit
        assert not lw.item(1).isHidden()  # content hit

    def test_empty_query_shows_all(self, qtbot):
        lw = _list_with(qtbot, [("A", 1), ("B", 2)])
        ccl.filter_by_search(lw, "zzz")
        ccl.filter_by_search(lw, "")
        assert not lw.item(0).isHidden()
        assert not lw.item(1).isHidden()


class TestMarkdownExport:
    def _conv(self):
        return {
            "title": "Debugging session",
            "model": "gpt-5.5",
            "created_at": "2026-08-30 10:00:00",
            "updated_at": "2026-08-30 11:00:00",
        }

    def test_header_and_metadata(self):
        md = ccl.format_conversation_markdown(self._conv(), [])
        assert md.startswith("# Debugging session\n")
        assert "- **Model:** gpt-5.5" in md
        assert "- **Created:** 2026-08-30 10:00:00" in md

    def test_roles_and_content(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there", "model": "gpt-5.5"},
        ]
        md = ccl.format_conversation_markdown(self._conv(), messages)
        assert "You are helpful" not in md  # system boilerplate skipped
        assert "## User\n\nhello" in md
        assert "## Assistant (gpt-5.5)\n\nhi there" in md

    def test_tool_calls_render_as_json_blocks(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "file_read", "arguments": '{"path": "a.py"}'}}
                ],
            },
            {"role": "tool", "content": "print('hi')"},
        ]
        md = ccl.format_conversation_markdown(self._conv(), messages)
        assert "**Tool call: `file_read`**" in md
        assert '```json\n{"path": "a.py"}\n```' in md
        assert "## Tool\n\n```\nprint('hi')\n```" in md

    def test_untitled_fallback(self):
        md = ccl.format_conversation_markdown({}, [])
        assert md.startswith("# Conversation")


class _FakeEntry:
    def __init__(self, provider_name, provider_display, model_id):
        self.provider_name = provider_name
        self.provider_display = provider_display
        self.model_id = model_id

    @property
    def full_id(self):
        return f"{self.provider_name}:{self.model_id}"


class _FakeProviderManager:
    def __init__(self, entries):
        self._entries = entries

    async def get_all_models(self):
        return self._entries


class TestPopulateModels:
    @pytest.fixture
    def panel(self, qtbot):
        p = ChatPanel()
        qtbot.addWidget(p)
        return p

    async def test_live_models_replace_defaults(self, panel):
        panel.set_provider_manager(
            _FakeProviderManager(
                [
                    _FakeEntry("openai", "OpenAI", "gpt-5.5"),
                    _FakeEntry("anthropic", "Anthropic", "claude-sonnet-5"),
                ]
            )
        )
        await panel.populate_models()
        datas = [panel._model_combo.itemData(i) for i in range(panel._model_combo.count())]
        assert "openai:gpt-5.5" in datas
        assert "anthropic:claude-sonnet-5" in datas
        # only headers + the two live entries remain
        assert sum(1 for d in datas if d) == 2

    async def test_empty_fetch_keeps_defaults(self, panel):
        before = panel._model_combo.count()
        panel.set_provider_manager(_FakeProviderManager([]))
        await panel.populate_models()
        assert panel._model_combo.count() == before

    async def test_fetch_failure_keeps_defaults(self, panel):
        class _Boom:
            async def get_all_models(self):
                raise RuntimeError("network down")

        before = panel._model_combo.count()
        panel.set_provider_manager(_Boom())
        await panel.populate_models()
        assert panel._model_combo.count() == before

    async def test_default_selection_restored(self, panel):
        panel.set_provider_manager(
            _FakeProviderManager(
                [
                    _FakeEntry("openai", "OpenAI", "o4-mini"),
                    _FakeEntry("openai", "OpenAI", "gpt-5.5"),
                ]
            )
        )
        await panel.populate_models()
        assert panel._model_combo.currentData() == "openai:gpt-5.5"
