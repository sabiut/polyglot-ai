"""User messages expose an "Edit & resend" button wired to ``on_edit``."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from polyglot_ai.ui.panels.chat_message import ChatMessage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _buttons(widget: ChatMessage) -> dict[str, QPushButton]:
    return {b.toolTip(): b for b in widget.findChildren(QPushButton) if b.toolTip()}


def test_user_message_has_edit_button_calling_on_edit(qapp):
    msg = ChatMessage("user", "make it faster")
    seen = []
    msg.on_edit = lambda widget, content: seen.append((widget, content))

    btn = _buttons(msg).get("Edit & resend")
    assert btn is not None
    btn.click()
    assert seen == [(msg, "make it faster")]


def test_assistant_message_has_no_edit_button(qapp):
    msg = ChatMessage("assistant", "done")
    assert "Edit & resend" not in _buttons(msg)
    assert "Regenerate" in _buttons(msg)
