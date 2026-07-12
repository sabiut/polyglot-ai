"""The ChatMessage reasoning panel (extended-thinking display)."""

from __future__ import annotations

import pytest

from polyglot_ai.ui.panels.chat_message import ChatMessage


@pytest.fixture
def qt(qtbot):
    return qtbot


def test_assistant_reasoning_builds_panel(qt):
    msg = ChatMessage("assistant", "")
    qt.addWidget(msg)
    assert msg._reasoning_view is None  # not built until reasoning arrives

    msg.append_reasoning("Considering the options")
    assert msg._reasoning_view is not None
    assert "Considering the options" in msg._reasoning_view.toPlainText()
    # Collapsed by default so it doesn't crowd the answer. (isHidden rather
    # than isVisible: the widget has no shown ancestor in the test harness.)
    assert msg._reasoning_view.isHidden() is True
    assert msg._reasoning_toggle is not None
    assert msg._reasoning_toggle.isChecked() is False


def test_reasoning_accumulates(qt):
    msg = ChatMessage("assistant", "")
    qt.addWidget(msg)
    msg.append_reasoning("part one ")
    msg.append_reasoning("part two")
    assert msg._reasoning_view.toPlainText() == "part one part two"


def test_toggle_reveals_panel(qt):
    msg = ChatMessage("assistant", "")
    qt.addWidget(msg)
    msg.append_reasoning("hmm")
    msg._reasoning_toggle.setChecked(True)
    # Toggling the button un-hides the panel (isHidden False), independent
    # of whether an ancestor window is shown.
    assert msg._reasoning_view.isHidden() is False


def test_user_message_ignores_reasoning(qt):
    msg = ChatMessage("user", "hello")
    qt.addWidget(msg)
    msg.append_reasoning("should be ignored")
    assert msg._reasoning_view is None


def test_empty_reasoning_is_noop(qt):
    msg = ChatMessage("assistant", "")
    qt.addWidget(msg)
    msg.append_reasoning("")
    assert msg._reasoning_view is None
