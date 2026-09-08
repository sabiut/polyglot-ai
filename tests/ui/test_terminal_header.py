"""Terminal header: pop out into a window, expand to fill the column, close."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.main_window import MainWindow  # noqa: E402
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


class TestPopOut:
    def test_pop_out_moves_the_same_widget_and_dock_back_restores(self, qapp):
        panel = TerminalPanel()
        panel.show()
        qapp.processEvents()
        widget = panel._terminal_widget
        assert not panel.is_popped_out
        assert widget.parent() is panel

        panel.pop_out()
        qapp.processEvents()
        assert panel.is_popped_out
        assert widget.window() is panel._window  # same widget, new top-level home
        assert not panel._popped_placeholder.isHidden()
        assert not panel._popout_btn.isEnabled()

        panel.dock_back()
        qapp.processEvents()
        assert not panel.is_popped_out
        assert widget.parent() is panel
        assert panel._popped_placeholder.isHidden()
        assert panel._popout_btn.isEnabled()
        panel.close()

    def test_closing_the_window_docks_back(self, qapp):
        panel = TerminalPanel()
        panel.show()
        panel.pop_out()
        qapp.processEvents()
        panel._window.close()
        qapp.processEvents()
        assert not panel.is_popped_out
        assert panel._terminal_widget.parent() is panel
        panel.close()

    def test_second_pop_out_is_a_noop(self, qapp):
        panel = TerminalPanel()
        panel.show()
        panel.pop_out()
        win = panel._window
        panel.pop_out()
        assert panel._window is win
        panel.dock_back()
        panel.close()


class TestExpandAndClose:
    def test_expand_gives_terminal_the_column_and_restores(self, window, qapp):
        window._action_toggle_terminal.setChecked(True)
        qapp.processEvents()
        before = window._center_splitter.sizes()
        assert before[0] > 0

        window._terminal_panel.expand_requested.emit()
        qapp.processEvents()
        assert window._terminal_expanded
        assert window._center_splitter.sizes()[0] == 0
        assert window._terminal_panel._expanded

        window._terminal_panel.expand_requested.emit()
        qapp.processEvents()
        assert not window._terminal_expanded
        assert window._center_splitter.sizes()[0] > 0

    def test_expand_shows_hidden_terminal_first(self, window, qapp):
        assert not window._action_toggle_terminal.isChecked()
        window._terminal_panel.expand_requested.emit()
        qapp.processEvents()
        assert window._action_toggle_terminal.isChecked()
        assert window._terminal_expanded
        window._terminal_panel.expand_requested.emit()

    def test_hiding_an_expanded_terminal_restores_the_editor(self, window, qapp):
        window._terminal_panel.expand_requested.emit()
        qapp.processEvents()
        assert window._center_splitter.sizes()[0] == 0
        window._terminal_panel.close_requested.emit()
        qapp.processEvents()
        assert not window._action_toggle_terminal.isChecked()
        assert not window._terminal_expanded
        assert window._center_splitter.sizes()[0] > 0
