"""The Settings dialog's editor/terminal/AI controls must actually take effect.

Eight keys were persisted by the dialog but never read anywhere
(font family/size, tab size, terminal font size, default model,
temperature, max tokens, system prompt). These pin the consumers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.panels.chat_panel import ChatPanel  # noqa: E402
from polyglot_ai.ui.panels.editor_tab import EditorTab  # noqa: E402
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Settings:
    def __init__(self, **values):
        self._values = values

    def get(self, key):
        return self._values.get(key)


class TestEditorSettings:
    def test_font_and_tab_size_applied_from_settings(self, qapp):
        tab = EditorTab()
        tab.set_ai_services(None, _Settings(**{"editor.font_size": 15, "editor.tab_size": 2}))
        assert tab.editor.font().pointSize() == 15
        assert tab.editor.tabWidth() == 2

    def test_lexer_font_follows_setting(self, qapp, tmp_path):
        path = tmp_path / "x.py"
        path.write_text("print('hi')\n")
        tab = EditorTab()
        tab.load(path)  # installs a Python lexer
        tab.set_ai_services(None, _Settings(**{"editor.font_size": 17}))
        assert tab.editor.lexer() is not None
        # style 0 is the lexer's "default" style; setFont(font) fans out to all
        assert tab.editor.lexer().font(0).pointSize() == 17

    def test_garbage_values_fall_back_to_defaults(self, qapp):
        tab = EditorTab()
        tab.set_ai_services(
            None, _Settings(**{"editor.font_size": "huge", "editor.tab_size": None})
        )
        assert tab.editor.font().pointSize() == 11
        assert tab.editor.tabWidth() == 4


class TestTerminalSettings:
    def test_set_font_size_changes_widget_font(self, qapp):
        panel = TerminalPanel()
        panel.set_font_size(14)
        assert panel._terminal_widget._font_size == 14

    def test_set_font_size_ignores_garbage(self, qapp):
        panel = TerminalPanel()
        before = panel._terminal_widget._font_size
        panel.set_font_size("nope")
        assert panel._terminal_widget._font_size == before


class TestChatGenerationSettings:
    def test_generation_params_read_from_window_settings(self, qapp, monkeypatch):
        panel = ChatPanel()
        monkeypatch.setattr(
            panel,
            "window",
            lambda: type(
                "W", (), {"_settings": _Settings(**{"ai.temperature": 0.2, "ai.max_tokens": 512})}
            )(),
        )
        assert panel._generation_params() == {"temperature": 0.2, "max_tokens": 512}

    def test_generation_params_default_without_window_settings(self, qapp):
        panel = ChatPanel()
        params = panel._generation_params()
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 4096

    def test_generation_params_sanitise_bad_values(self, qapp, monkeypatch):
        panel = ChatPanel()
        monkeypatch.setattr(
            panel,
            "window",
            lambda: type(
                "W", (), {"_settings": _Settings(**{"ai.temperature": "warm", "ai.max_tokens": -5})}
            )(),
        )
        params = panel._generation_params()
        assert "temperature" not in params
        assert params["max_tokens"] == 1
