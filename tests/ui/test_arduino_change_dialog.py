"""Headless tests for the Change-project dialog.

These tests don't fork a Qt event loop's modal — ``QDialog.exec``
needs an actual window manager and clicks. Instead they construct
the dialog, drive its internal state via the methods the slots
call, and read the resulting ``ChangeResult``.

The tests pin three things the panel relies on:

- "Open existing" picks correctly populate ``ChangeResult.existing``
  when the chosen folder has an Arduino-shaped entry file, and
  refuse to enable OK otherwise.
- The "Save to" / "Will be saved as" preview hides on the existing
  tab (the project is being adopted, not created).
- Switching tabs updates the OK-button state (each tab has its own
  validity rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
yaml = pytest.importorskip("yaml")

from PyQt6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from polyglot_ai.core.arduino.boards import Language  # noqa: E402
from polyglot_ai.core.arduino.starters import list_starters  # noqa: E402
from polyglot_ai.ui.dialogs.arduino_change_dialog import (  # noqa: E402
    ArduinoChangeDialog,
    ChangeResult,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestExistingTab:
    def _dialog(self, qapp, default_target: Path) -> ArduinoChangeDialog:
        dlg = ArduinoChangeDialog(default_target)
        dlg._stack.setCurrentIndex(2)
        dlg._on_tab_changed(2)
        return dlg

    def test_ok_disabled_until_folder_picked(self, qapp, tmp_path):
        dlg = self._dialog(qapp, tmp_path)
        ok = dlg._bb.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False

    def test_picking_arduino_folder_enables_ok(self, qapp, tmp_path):
        # Plant a recognisable .ino so detect_in() finds it.
        sketch = tmp_path / "blink"
        sketch.mkdir()
        (sketch / "blink.ino").write_text("void setup(){} void loop(){}")

        dlg = self._dialog(qapp, tmp_path)
        # Drive the slot directly — QFileDialog can't be opened in
        # a headless test. Patch the picker by setting the result
        # via the same code path the slot would.
        from polyglot_ai.core.arduino.project import detect_in

        detected = detect_in(tmp_path)
        assert detected is not None
        dlg._chosen_existing = detected
        dlg._update_ok_state()

        ok = dlg._bb.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is True

    def test_picking_non_arduino_folder_keeps_ok_disabled(self, qapp, tmp_path):
        # No .ino / main.py / code.py — detect_in returns None.
        (tmp_path / "README.md").write_text("# nothing here\n")
        dlg = self._dialog(qapp, tmp_path)
        from polyglot_ai.core.arduino.project import detect_in

        assert detect_in(tmp_path) is None

        # Mimic the "user picked, we found nothing" branch.
        dlg._chosen_existing = None
        dlg._update_ok_state()
        ok = dlg._bb.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False

    def test_accepted_returns_existing_change_result(self, qapp, tmp_path):
        sketch = tmp_path / "blink"
        sketch.mkdir()
        (sketch / "blink.ino").write_text("void setup(){} void loop(){}")
        from polyglot_ai.core.arduino.project import detect_in

        dlg = self._dialog(qapp, tmp_path)
        dlg._chosen_existing = detect_in(tmp_path)
        dlg._update_ok_state()
        dlg._on_accept()
        assert dlg.result_value is not None
        assert isinstance(dlg.result_value, ChangeResult)
        assert dlg.result_value.existing is not None
        assert dlg.result_value.existing.language is Language.CPP
        # Target dir for "existing" comes from the detected project,
        # not the dialog's default — the panel uses it as the
        # canonical project location.
        assert dlg.result_value.target_dir == sketch

    def test_target_box_hidden_on_existing_tab(self, qapp, tmp_path):
        # ``isVisible`` returns False on any widget whose parent hasn't
        # been shown yet (isn't painted). The hide-state we actually
        # care about is the explicit ``setVisible(False)`` flag, which
        # ``isHidden()`` reports independently of paint state.
        dlg = ArduinoChangeDialog(tmp_path)
        assert dlg._target_box.isHidden() is False  # default tab keeps it on
        dlg._stack.setCurrentIndex(2)
        dlg._on_tab_changed(2)
        assert dlg._target_box.isHidden() is True

        # Switching back shows it again.
        dlg._stack.setCurrentIndex(0)
        dlg._on_tab_changed(0)
        assert dlg._target_box.isHidden() is False


class TestStarterAndBlankStillWork:
    """Sanity checks that the existing tabs aren't broken by the new one."""

    def test_starter_path_unchanged(self, qapp, tmp_path):
        dlg = ArduinoChangeDialog(tmp_path)
        dlg._stack.setCurrentIndex(0)
        starters = [s for s in list_starters() if s.slug == "blink-cpp"]
        assert starters
        dlg._chosen_starter = starters[0]
        dlg._update_ok_state()
        ok = dlg._bb.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is True
        dlg._on_accept()
        assert dlg.result_value is not None
        assert dlg.result_value.starter is starters[0]

    def test_blank_requires_name(self, qapp, tmp_path):
        dlg = ArduinoChangeDialog(tmp_path)
        dlg._stack.setCurrentIndex(1)
        dlg._on_tab_changed(1)
        ok = dlg._bb.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False
        dlg._blank_name.setText("my_thing")
        dlg._update_ok_state()
        assert ok.isEnabled() is True
