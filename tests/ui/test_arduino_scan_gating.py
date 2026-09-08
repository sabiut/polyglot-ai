"""The Arduino panel must not scan boards while hidden.

Board detection shells out to arduino-cli (about a second) and the
panel is hidden at startup — the startup timer showed the scan landing
on the GUI thread right after the window appeared.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.panels.arduino_panel import ArduinoPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    p = ArduinoPanel()
    yield p
    p._poll_timer.stop()
    p.deleteLater()


async def _noop():
    return None


def test_hidden_panel_does_not_scan(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "_run_detect", lambda: calls.append(1) or _noop())
    assert not panel.isVisible()
    panel._kick_detect()
    assert calls == []
    assert getattr(panel, "_detecting", False) is False


def test_visible_panel_scans(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "_run_detect", lambda: calls.append(1) or _noop())
    panel.show()
    try:
        panel._kick_detect()
    finally:
        panel.hide()
    assert calls == [1]


def test_main_window_builds_arduino_panel_lazily(qapp, tmp_path, monkeypatch):
    from polyglot_ai.ui.main_window import MainWindow

    roots = []
    monkeypatch.setattr(ArduinoPanel, "set_project_root", lambda self, p: roots.append(p))
    window = MainWindow()
    try:
        assert window._arduino_panel is None
        # A project opened before the panel exists is applied on construction.
        window._arduino_project_root = tmp_path
        panel = window.arduino_panel
        assert isinstance(panel, ArduinoPanel)
        assert panel is window.arduino_panel  # cached
        assert roots == [tmp_path]
    finally:
        window.close()
