"""Signals that previously had no listener now keep the UI current.

- ``ChangesetPanel.change_applied`` / ``change_rolledback`` → reload the
  open editor tab and broadcast ``file:saved``.
- ``EditorPanel.reload_from_disk`` respects unsaved edits.
- The indexer drops a whole directory when it's deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.bridge import EventBus  # noqa: E402
from polyglot_ai.core.indexer import ProjectIndexer  # noqa: E402
from polyglot_ai.startup.ui_wiring import wire_changeset_events  # noqa: E402
from polyglot_ai.ui.panels.changeset_panel import ChangesetPanel  # noqa: E402
from polyglot_ai.ui.panels.editor_panel import EditorPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class TestReloadFromDisk:
    def test_reloads_clean_tab_and_keeps_cursor(self, qapp, tmp_path):
        path = tmp_path / "a.py"
        path.write_text("one\ntwo\nthree\n")
        panel = EditorPanel()
        panel.open_file(path)
        tab = panel.get_current_tab()
        tab.editor.setCursorPosition(2, 0)

        path.write_text("one\nTWO\nthree\nfour\n")
        assert panel.reload_from_disk(path) is True
        assert "TWO" in tab.editor.text()
        assert tab.editor.getCursorPosition() == (2, 0)
        assert tab.is_modified is False

    def test_leaves_dirty_tab_alone(self, qapp, tmp_path):
        path = tmp_path / "b.py"
        path.write_text("original\n")
        panel = EditorPanel()
        panel.open_file(path)
        tab = panel.get_current_tab()
        tab.editor.setText("user edits not yet saved\n")
        assert tab.is_modified

        path.write_text("rewritten on disk\n")
        assert panel.reload_from_disk(path) is False
        assert "user edits" in tab.editor.text()

    def test_unknown_path_is_noop(self, qapp, tmp_path):
        panel = EditorPanel()
        assert panel.reload_from_disk(tmp_path / "nope.py") is False


class TestChangesetWiring:
    def test_apply_signal_reloads_editor_and_emits_file_saved(self, qapp, tmp_path):
        target = tmp_path / "mod.py"
        target.write_text("before\n")

        bus = EventBus()
        editor_panel = EditorPanel()
        editor_panel.open_file(target)
        changeset = ChangesetPanel()
        changeset.set_project_root(tmp_path)

        window = type("W", (), {"editor_panel": editor_panel})()
        wire_changeset_events(bus, changeset, window)

        saved = []
        bus.subscribe("file:saved", lambda path="", **kw: saved.append(path))

        # Simulate the panel having written the file, then signalling.
        target.write_text("after\n")
        changeset.change_applied.emit("mod.py")

        assert saved == [str(target)]
        assert "after" in editor_panel.get_current_tab().editor.text()

    def test_rollback_signal_also_broadcasts(self, qapp, tmp_path):
        bus = EventBus()
        changeset = ChangesetPanel()
        changeset.set_project_root(tmp_path)
        wire_changeset_events(bus, changeset)
        saved = []
        bus.subscribe("file:saved", lambda path="", **kw: saved.append(path))
        changeset.change_rolledback.emit("x.py")
        assert saved == [str(tmp_path / "x.py")]


class TestIndexerIncremental:
    def _indexer(self, root: Path) -> ProjectIndexer:
        idx = ProjectIndexer()
        idx._project_root = root
        return idx

    def test_update_then_remove_file(self, tmp_path):
        f = tmp_path / "pkg" / "mod.py"
        f.parent.mkdir()
        f.write_text("def frobnicate(): pass\n")
        idx = self._indexer(tmp_path)
        idx.update_file(f)
        assert "pkg/mod.py" in idx._files
        idx.remove_file(f)
        assert "pkg/mod.py" not in idx._files

    def test_remove_directory_drops_everything_under_it(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        a = tmp_path / "pkg" / "a.py"
        b = tmp_path / "pkg" / "b.py"
        other = tmp_path / "other.py"
        for p in (a, b, other):
            p.write_text("x = 1\n")
        idx = self._indexer(tmp_path)
        for p in (a, b, other):
            idx.update_file(p)
        idx.remove_file(tmp_path / "pkg")
        assert idx._files == {"other.py"}

    def test_paths_outside_project_are_ignored(self, tmp_path):
        idx = self._indexer(tmp_path / "proj")
        (tmp_path / "proj").mkdir()
        stray = tmp_path / "elsewhere.py"
        stray.write_text("y = 2\n")
        idx.update_file(stray)  # must not raise
        idx.remove_file(stray)
        assert idx._files == set()
