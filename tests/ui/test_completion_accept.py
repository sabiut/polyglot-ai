"""Tests for accepting inline AI completions with Tab.

Audit finding: suggestions rendered as a QScintilla annotation with a
"(Tab to accept)" hint, but Tab merely cleared the annotation — the
suggestion text was never stored, so there was no way to insert it.
Now the raw suggestion is kept in ``_completion_text`` and Tab (via an
event filter on the editor, which otherwise consumes Tab for
indentation) inserts it at the cursor as a single undo action.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from polyglot_ai.ui.panels.editor_tab import EditorTab


@pytest.fixture
def tab(qtbot):
    t = EditorTab()
    qtbot.addWidget(t)
    t._editor.setText("def add(a, b):\n    ret\n")
    t.show()
    return t


def seed_suggestion(tab, line: int, col: int, text: str) -> None:
    """Place the cursor and register a pending suggestion the way
    ``_do_completion`` would (annotation + stored raw text)."""
    tab._editor.setCursorPosition(line, col)
    tab._show_completion_annotation(line, text)


class TestAcceptWithTab:
    def test_tab_inserts_suggestion_at_cursor(self, qtbot, tab):
        seed_suggestion(tab, 1, 7, "urn a + b")
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        lines = tab._editor.text().split("\n")
        assert lines[1] == "    return a + b"
        assert "\t" not in tab._editor.text()

    def test_tab_clears_annotation_and_stored_text(self, qtbot, tab):
        seed_suggestion(tab, 1, 7, "urn a + b")
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        assert tab._completion_annotation_line is None
        assert tab._completion_text is None

    def test_cursor_lands_after_inserted_text(self, qtbot, tab):
        seed_suggestion(tab, 1, 7, "urn a + b")
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        assert tab._editor.getCursorPosition() == (1, len("    return a + b"))

    def test_multiline_suggestion_inserted_verbatim(self, qtbot, tab):
        suggestion = "urn a + b\n\n\ndef sub(a, b):\n    return a - b"
        seed_suggestion(tab, 1, 7, suggestion)
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        # Verbatim: the editor's auto-indent must not re-indent the
        # suggestion's lines (blank lines stay blank, body keeps the
        # model's 4-space indent, no doubling).
        assert tab._editor.text() == (
            "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
        )
        assert tab._editor.getCursorPosition() == (5, len("    return a - b"))

    def test_accept_is_single_undo_action(self, qtbot, tab):
        original = tab._editor.text()
        seed_suggestion(tab, 1, 7, "urn a + b\ndef sub():\n    pass")
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        tab._editor.undo()
        assert tab._editor.text() == original


class TestNormalTabBehaviour:
    def test_tab_indents_when_no_suggestion(self, qtbot, tab):
        tab._editor.setCursorPosition(1, 0)
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        lines = tab._editor.text().split("\n")
        # setIndentationsUseTabs(False) + tab width 4 → spaces added
        assert lines[1] == "        ret"
        assert "urn" not in tab._editor.text()

    def test_tab_indents_when_cursor_left_annotated_line(self, qtbot, tab):
        seed_suggestion(tab, 1, 7, "urn a + b")
        tab._editor.setCursorPosition(0, 0)  # cursor moved away
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        assert "urn a + b" not in tab._editor.text()
        assert tab._editor.text().split("\n")[0] == "    def add(a, b):"


class TestDismissal:
    def test_typing_dismisses_without_inserting(self, qtbot, tab):
        seed_suggestion(tab, 1, 7, "urn a + b")
        qtbot.keyClick(tab._editor, Qt.Key.Key_X)
        assert tab._completion_annotation_line is None
        assert tab._completion_text is None
        assert "urn a + b" not in tab._editor.text()
        # A later Tab must indent, not resurrect the suggestion
        qtbot.keyClick(tab._editor, Qt.Key.Key_Tab)
        assert "urn a + b" not in tab._editor.text()

    def test_escape_dismisses_without_inserting(self, qtbot, tab):
        original = tab._editor.text()
        seed_suggestion(tab, 1, 7, "urn a + b")
        qtbot.keyClick(tab._editor, Qt.Key.Key_Escape)
        assert tab._completion_annotation_line is None
        assert tab._completion_text is None
        assert tab._editor.text() == original
