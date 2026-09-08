"""A freshly constructed TerminalPanel must feed PTY output into its emulator.

Regression: 0.18.6 constructed the panel with the PTY signal
connections displaced out of ``__init__``, so the shell's prompt never
reached the emulator and the terminal looked dead until popped out
and docked back. This drives the panel without a real shell.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core import panel_state  # noqa: E402
from polyglot_ai.core.terminal.emulator import TerminalEmulator  # noqa: E402
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _visible(panel: TerminalPanel) -> list[str]:
    return [line.rstrip() for line in panel._emulator._screen.display if line.strip()]


def test_pty_output_signal_reaches_emulator_without_pop_out(qapp):
    panel = TerminalPanel()
    panel._emulator = TerminalEmulator(24, 80)
    panel._terminal_widget.set_emulator(panel._emulator)

    panel._pty_output.emit(b"user@host:~$ ")
    qapp.processEvents()
    assert _visible(panel) == ["user@host:~$"]


def test_output_survives_being_shown_after_a_hidden_start(qapp):
    panel = TerminalPanel()
    panel.setVisible(False)
    panel._emulator = TerminalEmulator(26, 80)  # sized while the widget had no geometry
    panel._terminal_widget.set_emulator(panel._emulator)
    panel._pty_output.emit(b"user@host:~/project$ ")
    qapp.processEvents()

    panel.resize(600, 180)  # what showing the pane does: a much shorter screen
    panel.show()
    qapp.processEvents()
    assert _visible(panel) == ["user@host:~/project$"]
    panel.close()


def test_ai_buffer_reader_is_registered_at_construction(qapp):
    panel = TerminalPanel()
    panel._emulator = TerminalEmulator(24, 80)
    panel._pty_output.emit(b"hello\r\n$ ")
    qapp.processEvents()
    reader = panel_state._terminal_reader
    assert reader is not None
    assert "hello" in (reader() or "")
