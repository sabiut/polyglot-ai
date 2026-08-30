"""Tests for the editor find/replace bar and the file:saved event.

Both were audit findings: Ctrl+F/Ctrl+H menu actions existed but were
never connected (no find UI at all), and EVT_FILE_SAVED had two
subscribers (git panel refresh, test panel re-collect) but zero
emitters — saving a file never triggered either.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.ui.panels.editor_panel import EditorPanel
from polyglot_ai.ui.panels.editor_tab import EditorTab


@pytest.fixture
def tab(qtbot):
    t = EditorTab()
    qtbot.addWidget(t)
    t._editor.setText("alpha beta\nalpha gamma\nALPHA delta\n")
    t.show()
    return t


class TestFindBar:
    def test_hidden_by_default(self, tab):
        assert not tab._find_bar.isVisible()

    def test_show_find_hides_replace_controls(self, tab):
        tab.show_find_bar(replace=False)
        assert tab._find_bar.isVisible()
        assert not tab._replace_input.isVisible()

    def test_show_replace_reveals_replace_controls(self, tab):
        tab.show_find_bar(replace=True)
        assert tab._replace_input.isVisible()
        assert tab._replace_all_btn.isVisible()

    def test_find_next_selects_match(self, tab):
        tab.show_find_bar()
        tab._find_input.setText("alpha")
        assert tab.find_next() is True
        assert tab._editor.selectedText().lower() == "alpha"

    def test_find_reports_no_matches(self, tab):
        tab.show_find_bar()
        tab._find_input.setText("nonexistent")
        assert tab.find_next() is False
        assert tab._find_status.text() == "No matches"

    def test_case_sensitivity_toggle(self, tab):
        tab.show_find_bar()
        tab._case_btn.setChecked(True)
        tab._find_input.setText("ALPHA")
        assert tab.find_next() is True
        line, _, _, _ = tab._editor.getSelection()
        assert line == 2  # only the third line matches case-sensitively

    def test_replace_all_counts_and_replaces(self, tab):
        tab.show_find_bar(replace=True)
        tab._find_input.setText("alpha")
        tab._replace_input.setText("omega")
        tab._replace_all()
        assert "Replaced 3" in tab._find_status.text()
        assert "alpha" not in tab._editor.text().lower()

    def test_replace_all_is_one_undo_action(self, tab):
        original = tab._editor.text()
        tab.show_find_bar(replace=True)
        tab._find_input.setText("alpha")
        tab._replace_input.setText("omega")
        tab._replace_all()
        tab._editor.undo()
        assert tab._editor.text() == original

    def test_escape_hides_bar(self, tab):
        tab.show_find_bar()
        tab.hide_find_bar()
        assert not tab._find_bar.isVisible()

    def test_selection_prefills_find_input(self, tab):
        tab._editor.setSelection(0, 0, 0, 5)  # "alpha"
        tab.show_find_bar()
        assert tab._find_input.text() == "alpha"


class TestFileSavedEvent:
    def test_save_current_emits_file_saved(self, qtbot, tmp_path):
        panel = EditorPanel()
        qtbot.addWidget(panel)
        bus = EventBus()
        panel.set_event_bus(bus)

        received = []
        bus.subscribe("file:saved", lambda path="", **_: received.append(path))

        target = tmp_path / "example.py"
        target.write_text("x = 1\n")
        panel.open_file(target)
        panel.save_current()

        assert received == [str(target)]

    def test_save_without_bus_is_safe(self, qtbot, tmp_path):
        # app.py wiring is best-effort; a panel without a bus must
        # still save without raising.
        panel = EditorPanel()
        qtbot.addWidget(panel)
        target = tmp_path / "example.py"
        target.write_text("x = 1\n")
        panel.open_file(target)
        assert panel.save_current() is True
