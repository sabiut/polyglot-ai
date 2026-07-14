"""UI tests for the chat-history sidebar toggle in the ChatPanel header.

The conversation sidebar (search, category filters, list) starts
hidden so the panel opens straight into the chat; the history button
reveals it on demand. Pins that default and the toggle behavior.
"""

from __future__ import annotations

import pytest

from polyglot_ai.ui.panels.chat_panel import ChatPanel


@pytest.fixture
def chat_panel(qtbot):
    p = ChatPanel()
    qtbot.addWidget(p)
    p.show()
    return p


def test_sidebar_hidden_by_default(chat_panel):
    assert chat_panel._history_sidebar.isVisible() is False
    assert chat_panel._history_btn.isChecked() is False
    assert chat_panel._history_btn.toolTip() == "Show chat history"


def test_clicking_history_button_reveals_sidebar(chat_panel):
    chat_panel._history_btn.setChecked(True)

    assert chat_panel._history_sidebar.isVisible() is True
    assert chat_panel._history_btn.toolTip() == "Hide chat history"


def test_clicking_again_hides_sidebar(chat_panel):
    chat_panel._history_btn.setChecked(True)
    chat_panel._history_btn.setChecked(False)

    assert chat_panel._history_sidebar.isVisible() is False
    assert chat_panel._history_btn.toolTip() == "Show chat history"
