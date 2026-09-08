"""Per-project chat history: the sidebar lists this project's chats by default."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.database import Database  # noqa: E402
from polyglot_ai.ui.panels.chat_panel import ChatPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(tmp_path, loop):
    database = Database(tmp_path / "chat.db")
    loop.run_until_complete(database.init())
    yield database
    loop.run_until_complete(database.close())


def _listed_ids(panel: ChatPanel) -> set[int]:
    lw = panel._conv_list
    return {lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count())}


@pytest.fixture
def panel(qapp, db, loop, tmp_path):
    p = ChatPanel()
    p.set_database(db)
    # ChatPanel resolves the project via its top-level window's explorer;
    # standalone, the panel *is* the window.
    p.file_explorer = SimpleNamespace(project_root=Path(tmp_path / "proj-a"))
    return p


class TestScopedSidebar:
    def test_defaults_to_current_project_plus_unscoped(self, panel, db, loop, tmp_path):
        a = loop.run_until_complete(
            db.create_conversation("A", "m", project_root=str(tmp_path / "proj-a"))
        )
        b = loop.run_until_complete(
            db.create_conversation("B", "m", project_root=str(tmp_path / "proj-b"))
        )
        g = loop.run_until_complete(db.create_conversation("General", "m"))

        loop.run_until_complete(panel.populate_conversations())
        assert _listed_ids(panel) == {a, g}
        assert panel._scope_btn.isChecked()

        panel._scope_btn.setChecked(False)
        # The toggle schedules a repopulate task, so it needs a running loop.
        loop.call_soon(panel._toggle_project_scope, False)
        loop.run_until_complete(asyncio.sleep(0.05))
        assert _listed_ids(panel) == {a, b, g}
        assert panel._scope_btn.text() == "All projects"

    def test_no_project_open_shows_everything(self, panel, db, loop, tmp_path):
        panel.file_explorer = SimpleNamespace(project_root=None)
        a = loop.run_until_complete(db.create_conversation("A", "m", project_root="/x"))
        loop.run_until_complete(panel.populate_conversations())
        assert _listed_ids(panel) == {a}

    def test_scope_root_follows_project(self, panel, tmp_path):
        assert panel._history_scope_root() == str(tmp_path / "proj-a")
        panel._scope_project_only = False
        assert panel._history_scope_root() is None
