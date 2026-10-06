"""The explorer can show more than one folder at a time.

The first folder is the project (git, tools and indexing key off it);
``add_folder`` stacks extra folders beneath it, each with its own
collapsible section and tree rooted at that folder.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.settings import DEFAULTS  # noqa: E402
from polyglot_ai.ui.panels.file_explorer import FileExplorer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _names_under(explorer: FileExplorer, root: Path) -> set[str]:
    app = QApplication.instance()
    fs, proxy = explorer._fs_model, explorer._proxy_model
    src = fs.index(str(root.resolve()))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        if src.isValid() and fs.rowCount(src) and not fs.canFetchMore(src):
            break
        if src.isValid() and fs.canFetchMore(src):
            fs.fetchMore(src)
        src = fs.index(str(root.resolve()))
        time.sleep(0.02)
    idx = proxy.mapFromSource(src)
    return {proxy.data(proxy.index(r, 0, idx)) for r in range(proxy.rowCount(idx))}


@pytest.fixture
def roots(tmp_path):
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    for root, name in ((a, "a.py"), (b, "b.py")):
        root.mkdir()
        (root / name).write_text("x = 1\n")
    return a, b


def test_default_setting():
    assert DEFAULTS["session.extra_folders"] == []


def test_add_folder_shows_second_section_rooted_at_it(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.set_root(a)
    assert explorer.add_folder(b) is True
    assert explorer.folders == [a, b]
    assert explorer.extra_folders == [b]
    assert explorer.project_root == a  # the project is unchanged
    assert len(explorer._sections) == 2
    assert explorer._sections[0].primary and not explorer._sections[1].primary
    assert explorer._sections[1].badge.text() == ""
    assert explorer._sections[0].badge.text() == "PROJECT"
    # The second tree is rooted at beta, not at alpha.
    tree_root = explorer._proxy_model.mapToSource(explorer._sections[1].tree.rootIndex())
    assert Path(explorer._fs_model.filePath(tree_root)) == b.resolve()
    assert "b.py" in _names_under(explorer, b)


def test_duplicates_and_missing_dirs_are_rejected(qapp, roots, tmp_path):
    a, b = roots
    explorer = FileExplorer()
    explorer.set_root(a)
    assert explorer.add_folder(a) is False
    assert explorer.add_folder(tmp_path / "nope") is False
    explorer.add_folder(b)
    assert explorer.add_folder(b) is False
    assert explorer.folders == [a, b]


def test_remove_folder_and_folders_changed(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    seen = []
    explorer.folders_changed.connect(lambda folders: seen.append(list(folders)))
    explorer.set_root(a)
    explorer.add_folder(b)
    assert explorer.remove_folder(b) is True
    assert explorer.remove_folder(b) is False
    assert explorer.remove_folder(a) is False  # the project can't be "removed"
    assert explorer.folders == [a]
    assert seen == [[str(a)], [str(a), str(b)], [str(a)]]


def test_open_project_keeps_extras_and_drops_old_project(qapp, roots, tmp_path):
    a, b = roots
    c = tmp_path / "gamma"
    c.mkdir()
    explorer = FileExplorer()
    explorer.set_root(a)
    explorer.add_folder(b)
    explorer.set_root(c)
    assert explorer.project_root == c
    assert explorer.folders == [c, b]


def test_promoting_an_extra_folder_swaps_roles(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.set_root(a)
    explorer.add_folder(b)
    explorer.set_root(b, keep_previous=True)
    assert explorer.project_root == b
    assert explorer.folders == [b, a]
    assert explorer._sections[0].primary and not explorer._sections[1].primary


def test_add_folder_without_project_proposes_it_as_project(qapp, roots):
    a, _b = roots
    explorer = FileExplorer()
    proposed = []
    explorer.set_as_project_requested.connect(proposed.append)
    assert explorer.add_folder(a) is False
    assert proposed == [a]
    assert explorer.folders == []


def test_clear_removes_everything(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.set_root(a)
    explorer.add_folder(b)
    explorer.clear()
    assert explorer.folders == []
    assert explorer.project_root is None
    assert explorer.tree is None
    assert explorer._placeholder.isVisibleTo(explorer)


def test_relative_path_uses_containing_folder(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.set_root(a)
    explorer.add_folder(b)
    assert explorer._root_for(b / "b.py") == b
    assert explorer._root_for(a / "a.py") == a
    assert explorer._root_for(Path("/definitely/elsewhere")) is None


def test_collapsed_section_shrinks_to_its_header(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.resize(300, 800)
    explorer.show()
    explorer.set_root(a)
    explorer.add_folder(b)
    QApplication.instance().processEvents()
    first, second = explorer._sections
    before = second.height()
    first.toggle_collapsed()
    QApplication.instance().processEvents()
    assert first.collapsed is True
    assert first.maximumHeight() == first.header.height()
    assert first.height() <= first.header.height() + 2
    assert second.height() > before
    first.toggle_collapsed()
    QApplication.instance().processEvents()
    assert first.collapsed is False
    assert first.maximumHeight() > 10_000
    assert first.tree.isVisibleTo(first)
    explorer.hide()


def test_all_collapsed_stays_pinned_to_the_top(qapp, roots):
    a, b = roots
    explorer = FileExplorer()
    explorer.resize(300, 800)
    explorer.show()
    explorer.set_root(a)
    explorer.add_folder(b)
    for section in explorer._sections:
        section.set_collapsed(True)
    QApplication.instance().processEvents()
    assert explorer._header_bar.y() == 0
    assert explorer._sections_box.y() == explorer._header_bar.height()
    assert explorer._sections_box.height() <= sum(s.header.height() for s in explorer._sections) + 4
    explorer.hide()
