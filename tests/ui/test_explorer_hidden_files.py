"""The file explorer shows dotfiles (.env, .gitignore, .github) by default.

The QFileSystemModel filter lacked ``QDir.Filter.Hidden``, so every
dotfile was dropped before the explorer's own ignore list ran — users
couldn't see .env or .gitignore at all. Noise (.git, .venv, caches)
must still be hidden, and the header eye button toggles dotfiles.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.settings import DEFAULTS  # noqa: E402
from polyglot_ai.ui.panels.file_explorer import FileExplorer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _visible_names(explorer: FileExplorer) -> set[str]:
    """Top-level entry names the tree currently shows (after the model loads)."""
    app = QApplication.instance()
    proxy = explorer._proxy_model
    root = explorer._tree.rootIndex()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if proxy.rowCount(root) and not explorer._fs_model.canFetchMore(proxy.mapToSource(root)):
            break
        explorer._fs_model.fetchMore(proxy.mapToSource(root))
        time.sleep(0.02)
    return {proxy.data(proxy.index(r, 0, root)) for r in range(proxy.rowCount(root))}


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "app.py").write_text("print('hi')\n")
    return tmp_path


def test_default_setting_shows_hidden():
    assert DEFAULTS["explorer.show_hidden"] is True


def test_dotfiles_visible_but_noise_dirs_hidden(qapp, project):
    explorer = FileExplorer()
    explorer.set_root(project)
    names = _visible_names(explorer)
    assert {".env", ".gitignore", ".github", "app.py"} <= names
    assert not {".git", ".venv", "__pycache__"} & names


def test_toggle_hides_and_restores_dotfiles(qapp, project):
    explorer = FileExplorer()
    explorer.set_root(project)
    assert ".env" in _visible_names(explorer)

    explorer.set_show_hidden(False)
    assert explorer.show_hidden is False
    assert not explorer._hidden_btn.isChecked()
    names = _visible_names(explorer)
    assert ".env" not in names and ".gitignore" not in names
    assert "app.py" in names

    explorer._hidden_btn.setChecked(True)  # the header button path
    assert explorer.show_hidden is True
    assert ".env" in _visible_names(explorer)


def test_toggle_announces_on_event_bus(qapp, project):
    from polyglot_ai.core.bridge import EventBus

    bus = EventBus()
    seen = []
    bus.subscribe("explorer:show_hidden", lambda show=None, **_: seen.append(show))
    explorer = FileExplorer()
    explorer.set_event_bus(bus)
    explorer.set_show_hidden(False)
    explorer.set_show_hidden(True)
    assert seen == [False, True]
