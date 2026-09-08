"""Context-menu actions keep working while the terminal is popped out."""

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


def test_main_window_lookup_survives_pop_out(window, qapp):
    widget = window.terminal_panel._terminal_widget
    assert widget._main_window() is window

    window.terminal_panel.pop_out()
    qapp.processEvents()
    assert widget.window() is not window  # now inside the pop-out window
    assert widget._main_window() is window
    window.terminal_panel.dock_back()


def test_send_to_ai_from_popped_out_terminal_prefills_chat(window, qapp, monkeypatch):
    panel = window.terminal_panel
    widget = panel._terminal_widget
    panel.pop_out()
    qapp.processEvents()

    monkeypatch.setattr(widget, "_get_selected_text", lambda: "bash: nope: command not found")
    received = []
    monkeypatch.setattr(window.chat_panel, "prefill_input", lambda text: received.append(text))

    widget._send_selection_to_ai()
    assert received and "command not found" in received[0]
    panel.dock_back()
