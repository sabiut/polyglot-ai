"""Focus mode and the on-demand terminal.

- Focus mode gives the editor the whole window and, when left,
  restores exactly the panels that were open before.
- The terminal starts hidden; the activity-bar button, the menu
  action and the session all agree on its state.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w.close()


class TestFocusMode:
    def test_toggle_hides_panels_and_restores_them(self, window, qapp):
        window._action_toggle_terminal.setChecked(True)
        assert window._sidebar_visible
        assert window._action_toggle_chat.isChecked()

        window._action_focus_mode.toggle()
        qapp.processEvents()
        assert window._focus_mode
        assert not window._sidebar_visible
        assert not window._action_toggle_chat.isChecked()
        assert not window._action_toggle_terminal.isChecked()
        assert window._action_focus_mode.isChecked()

        window._action_focus_mode.toggle()
        qapp.processEvents()
        assert not window._focus_mode
        assert window._sidebar_visible
        assert window._action_toggle_chat.isChecked()
        assert window._action_toggle_terminal.isChecked()

    def test_restores_only_what_was_open(self, window, qapp):
        # Terminal was closed (the default) — leaving focus mode must
        # not conjure it up.
        assert not window._action_toggle_terminal.isChecked()
        window._action_focus_mode.toggle()
        window._action_focus_mode.toggle()
        qapp.processEvents()
        assert not window._action_toggle_terminal.isChecked()
        assert window._action_toggle_chat.isChecked()

    def test_corner_button_and_palette_entry_exist(self, window):
        assert window._editor_panel.cornerWidget() is window._focus_btn
        ids = {a.action_id for a in window.action_registry.get_all()}
        assert "view.focus_mode" in ids
        window._focus_btn.click()
        assert window._focus_mode
        window._focus_btn.click()
        assert not window._focus_mode


class TestOnDemandTerminal:
    def test_hidden_by_default(self, window):
        assert not window._action_toggle_terminal.isChecked()
        assert window._terminal_panel.isHidden()
        assert not window._activity_bar._buttons["terminal"].active

    def test_activity_bar_button_toggles_and_highlights(self, window, qapp):
        window._activity_bar._buttons["terminal"].clicked.emit()
        qapp.processEvents()
        assert window._action_toggle_terminal.isChecked()
        assert not window._terminal_panel.isHidden()
        assert window._activity_bar._buttons["terminal"].active
        # Switching sidebar views must not clear the terminal highlight.
        window._activity_bar.set_active("git")
        assert window._activity_bar._buttons["terminal"].active

        window._activity_bar._buttons["terminal"].clicked.emit()
        qapp.processEvents()
        assert not window._action_toggle_terminal.isChecked()
        assert not window._activity_bar._buttons["terminal"].active

    def test_visibility_is_not_persisted(self, window):
        # Closing the app with the terminal open must not bring it
        # back on the next launch — it's hidden by default, always.
        window._action_toggle_terminal.setChecked(True)
        assert "session.terminal_visible" not in window.save_session()

        window._action_toggle_terminal.setChecked(False)
        window.restore_session({"session.terminal_visible": True})  # stale key from old builds
        assert not window._action_toggle_terminal.isChecked()
