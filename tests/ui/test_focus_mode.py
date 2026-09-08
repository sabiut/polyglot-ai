"""Focus mode: the editor takes the whole window, and leaving it restores
exactly the panels that were open before."""

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


def test_toggle_hides_panels_and_restores_them(window, qapp):
    assert window._sidebar_visible
    assert window._action_toggle_chat.isChecked()
    assert window._action_toggle_terminal.isChecked()

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


def test_restores_only_what_was_open(window, qapp):
    # User had already closed the terminal; focus mode must not
    # bring it back when leaving.
    window._action_toggle_terminal.setChecked(False)
    window._action_focus_mode.toggle()
    window._action_focus_mode.toggle()
    qapp.processEvents()
    assert not window._action_toggle_terminal.isChecked()
    assert window._action_toggle_chat.isChecked()


def test_corner_button_and_palette_entry_exist(window):
    assert window._editor_panel.cornerWidget() is window._focus_btn
    ids = {a.action_id for a in window.action_registry.get_all()}
    assert "view.focus_mode" in ids
    window._focus_btn.click()
    assert window._focus_mode
    window._focus_btn.click()
    assert not window._focus_mode
