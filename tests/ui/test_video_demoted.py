"""The video editor is reachable from the View menu and palette, not the activity bar."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.main_window import MainWindow  # noqa: E402
from polyglot_ai.ui.widgets.activity_bar import ActivityBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_activity_bar_has_no_video_entry(qapp):
    bar = ActivityBar()
    assert "video" not in bar._buttons
    assert "arduino" in bar._buttons  # the neighbour stays


def test_menu_and_palette_still_open_it(qapp, monkeypatch):
    opened = []
    # Patch on the class *before* construction: the menu action binds
    # the method at connect time, so an instance patch is too late.
    monkeypatch.setattr(MainWindow, "_show_video_window", lambda self: opened.append("menu"))
    w = MainWindow()
    assert w._action_video.text().startswith("&Video Editor")
    w._action_video.trigger()
    assert opened == ["menu"]

    ids = {a.action_id: a for a in w.action_registry.get_all()}
    assert "view.video" in ids
    ids["view.video"].callback()
    assert opened == ["menu", "menu"]
    w.close()
