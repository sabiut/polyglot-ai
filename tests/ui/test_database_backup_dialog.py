"""The Back up and Restore dialogs, and the panel button that opens them."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from polyglot_ai.core import db_explorer, db_notebook  # noqa: E402
from polyglot_ai.core.db_backup import BackupCancelled, BackupResult, Progress  # noqa: E402
from polyglot_ai.core.db_explorer import DatabaseConnection  # noqa: E402
from polyglot_ai.ui.panels import database_backup_dialog, database_panel  # noqa: E402
from polyglot_ai.ui.panels.database_backup_dialog import (  # noqa: E402
    BackupDialog,
    RestoreDialog,
    human_size,
    progress_text,
)
from polyglot_ai.ui.panels.database_dialogs import NewConnection  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")

PG_URL = "postgresql://app:hunter2@db.example.com:5432/shop"
EXPORT = (
    "-- Polyglot AI built-in export\n"
    "-- Connection: orders ({engine})  app@db.example.com:5432/shop\n"
    "-- Taken:      2026-11-12 22:04:54 +1100\n\n"
    "BEGIN;\nCOMMIT;\n-- End of export: 0 tables, 0 rows\n"
)


def _sqlite(path: Path, names=("a", "b")) -> str:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE items (name TEXT)")
    connection.executemany("INSERT INTO items VALUES (?)", [(n,) for n in names])
    connection.commit()
    connection.close()
    return str(path)


def _names(path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [r[0] for r in connection.execute("SELECT name FROM items ORDER BY rowid")]
    finally:
        connection.close()


async def _settle(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting for the dialog"
        await asyncio.sleep(0.01)
        QApplication.processEvents()


@pytest.fixture
def no_tools(monkeypatch, tmp_path):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ── wording ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("size", "text"),
    [(0, "0 bytes"), (1023, "1023 bytes"), (1536, "1.5 KB"), (5 * 1024**3, "5.0 GB")],
)
def test_human_size(size, text):
    assert human_size(size) == text


def test_progress_lines():
    assert progress_text(Progress("table", "orders", 2, 9, rows=12345)) == (
        "Table 2 of 9: orders — 12,345 rows so far"
    )
    assert progress_text(Progress("dumping", size=2048)) == "Dumping… 2.0 KB written"
    assert progress_text(Progress("loading", index=40, size=1024, total=4096)) == (
        "Loading… 40 statements, 1.0 KB of 4.0 KB read"
    )
    assert progress_text(Progress("loading", size=1024, total=4096)) == (
        "Loading… 1.0 KB of 4.0 KB read"
    )
    assert progress_text(Progress("connecting")) == "Connecting…"


# ── Back up ─────────────────────────────────────────────────────────


def test_backup_dialog_shows_where_without_the_password(qtbot, home, no_tools):
    dialog = BackupDialog(DatabaseConnection("orders", "postgresql", PG_URL))
    qtbot.addWidget(dialog)
    texts = " ".join(label.text() for label in dialog.findChildren(type(dialog._status)))
    assert "app@db.example.com:5432/shop" in texts
    assert "hunter2" not in texts
    assert dialog._method_combo.count() == 1
    assert dialog._method_combo.itemData(0) == "builtin"
    assert "Not included" in dialog._summary.text()
    assert "pg_dump" in dialog._hint.text() and not dialog._hint.isHidden()
    path = dialog._path.text()
    assert path.startswith(str(home / "orders-")) and path.endswith(".sql")
    assert dialog._start_btn.isEnabled()


def test_compress_tick_follows_the_file_name(qtbot, home, no_tools):
    dialog = BackupDialog(DatabaseConnection("orders", "postgresql", PG_URL))
    qtbot.addWidget(dialog)
    plain = dialog._path.text()
    dialog._gzip.setChecked(True)
    assert dialog._path.text() == plain + ".gz"
    dialog._gzip.setChecked(False)
    assert dialog._path.text() == plain
    # Typing the ending does the same as ticking the box.
    dialog._path.setText(plain + ".gz")
    assert dialog._gzip.isChecked() and dialog._path.text() == plain + ".gz"
    dialog._path.setText(plain)
    assert not dialog._gzip.isChecked()


def test_sqlite_offers_a_copy_and_no_compression(qtbot, home, tmp_path):
    dialog = BackupDialog(DatabaseConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db")))
    qtbot.addWidget(dialog)
    assert dialog._method_combo.itemData(0) == "copy"
    assert dialog._gzip.isHidden() and dialog._hint.isHidden()
    assert dialog._path.text().endswith(".db")


async def test_backing_up_sqlite_writes_the_file_and_says_so(qtbot, home, tmp_path):
    conn = DatabaseConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db"))
    dialog = BackupDialog(conn)
    qtbot.addWidget(dialog)
    destination = tmp_path / "saved.db"
    dialog._path.setText(str(destination))

    dialog._start()
    assert dialog._running and not dialog._start_btn.isEnabled()
    assert dialog._close_btn.text() == "Cancel" and not dialog._path.isEnabled()
    await _settle(lambda: not dialog._running)

    assert dialog.finished_ok
    assert _names(destination) == ["a", "b"]
    assert f"Backup saved: {destination}" in dialog._status.text()
    assert "1 table" in dialog._status.text()
    assert dialog._close_btn.text() == "Close" and dialog._path.isEnabled()
    assert dialog._start_btn.isEnabled() and dialog._start_btn.text() == "Back up again"
    assert dialog._bar.isHidden()


async def test_backup_asks_before_replacing_a_file(qtbot, home, tmp_path, monkeypatch):
    conn = DatabaseConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db"))
    dialog = BackupDialog(conn)
    qtbot.addWidget(dialog)
    destination = tmp_path / "saved.db"
    destination.write_text("an earlier backup")
    dialog._path.setText(str(destination))

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._start()
    assert not dialog._running and destination.read_text() == "an earlier backup"

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._start()
    await _settle(lambda: not dialog._running)
    assert _names(destination) == ["a", "b"]


@pytest.mark.parametrize(("typed", "message"), [("", "Choose where"), ("{tmp}", "folder")])
def test_backup_wants_a_file_name(qtbot, home, tmp_path, typed, message):
    dialog = BackupDialog(DatabaseConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db")))
    qtbot.addWidget(dialog)
    dialog._path.setText(typed.replace("{tmp}", str(tmp_path)))
    dialog._start()
    assert not dialog._running and message in dialog._status.text()


async def test_a_failed_backup_shows_the_reason_and_can_be_retried(qtbot, home, tmp_path):
    conn = DatabaseConnection("shop", "sqlite", str(tmp_path / "gone.db"))
    dialog = BackupDialog(conn)
    qtbot.addWidget(dialog)
    dialog._path.setText(str(tmp_path / "saved.db"))
    dialog._start()
    await _settle(lambda: not dialog._running)
    assert not dialog.finished_ok
    assert "isn't there" in dialog._status.text()
    assert dialog._start_btn.isEnabled() and dialog._start_btn.text() == "Back up"


async def test_cancel_stops_the_work_and_progress_reaches_the_dialog(
    qtbot, home, tmp_path, monkeypatch, no_tools
):
    started, seen = threading.Event(), {}

    def slow_backup(source, destination, *, method, progress, cancel):
        seen.update(source=source, destination=destination, method=method)
        progress(Progress("table", "orders", 1, 4, rows=10))
        started.set()
        if cancel.wait(5):
            raise BackupCancelled("Cancelled.")
        return BackupResult(Path(destination), method)

    monkeypatch.setattr(database_backup_dialog, "run_backup", slow_backup)
    dialog = BackupDialog(DatabaseConnection("orders", "postgresql", PG_URL))
    qtbot.addWidget(dialog)
    dialog._path.setText(str(tmp_path / "out.sql.gz"))
    dialog._start()
    await _settle(lambda: "Table 1 of 4: orders" in dialog._status.text())
    assert dialog._bar.maximum() == 1000 and dialog._bar.value() == 250

    dialog.reject()  # Esc or the window's ✕ while it runs
    assert dialog._running  # still there: it asked the work to stop
    await _settle(lambda: not dialog._running)

    assert dialog._status.text() == "Cancelled."
    assert not dialog.finished_ok
    assert seen["method"] == "builtin" and seen["destination"] == tmp_path / "out.sql.gz"
    assert seen["source"].connection_string == PG_URL


# ── Restore ─────────────────────────────────────────────────────────


def _restore_dialog(qtbot, conn, path=None):
    dialog = RestoreDialog(conn)
    qtbot.addWidget(dialog)
    if path is not None:
        dialog._path.setText(str(path))
    return dialog


def test_restore_needs_a_file_that_fits_and_an_explicit_yes(qtbot, tmp_path, no_tools):
    export = tmp_path / "out.sql"
    export.write_text(EXPORT.replace("{engine}", "PostgreSQL"))
    conn = DatabaseConnection("orders", "postgresql", PG_URL, read_only=False)
    dialog = _restore_dialog(qtbot, conn)
    assert not dialog._start_btn.isEnabled() and dialog._read_only_note.isHidden()
    assert dialog._agree.text() == "Load this backup into app@db.example.com:5432/shop"

    dialog._path.setText(str(export))
    assert "Built-in export of a PostgreSQL database" in dialog._what.text()
    assert "one transaction" in dialog._how.text()
    assert not dialog._start_btn.isEnabled()  # not until the box is ticked
    dialog._agree.setChecked(True)
    assert dialog._start_btn.isEnabled()

    dialog._path.setText(str(tmp_path / "missing.sql"))
    assert not dialog._start_btn.isEnabled() and "no file" in dialog._how.text()


def test_restore_refuses_another_engines_backup(qtbot, tmp_path, no_tools):
    export = tmp_path / "out.sql"
    export.write_text(EXPORT.replace("{engine}", "SQL Server"))
    conn = DatabaseConnection("orders", "postgresql", PG_URL, read_only=False)
    dialog = _restore_dialog(qtbot, conn, export)
    dialog._agree.setChecked(True)
    assert "made from SQL Server" in dialog._how.text()
    assert not dialog._start_btn.isEnabled()


def test_restore_into_a_read_only_connection_is_blocked(qtbot, tmp_path, no_tools):
    export = tmp_path / "out.sql"
    export.write_text(EXPORT.replace("{engine}", "PostgreSQL"))
    conn = DatabaseConnection("orders", "postgresql", PG_URL, read_only=True)
    dialog = _restore_dialog(qtbot, conn, export)
    dialog._agree.setChecked(True)
    assert not dialog._read_only_note.isHidden()
    assert "read-only" in dialog._read_only_note.text()
    assert not dialog._start_btn.isEnabled()
    dialog._start()
    assert not dialog._running


async def test_restoring_sqlite_replaces_the_file_and_keeps_the_old_one(qtbot, tmp_path):
    live = _sqlite(tmp_path / "shop.db", names=("old",))
    backup = _sqlite(tmp_path / "backup.db", names=("x", "y"))
    conn = DatabaseConnection("shop", "sqlite", live, read_only=False)
    assert (await conn.connect())[0]
    dialog = _restore_dialog(qtbot, conn, backup)
    assert "replaced with this copy" in dialog._how.text()
    dialog._agree.setChecked(True)

    dialog._start()
    await _settle(lambda: not dialog._running)

    assert dialog.finished_ok, dialog._status.text()
    assert not conn.is_connected  # closed so the file could be replaced under it
    assert _names(live) == ["x", "y"]
    (saved,) = tmp_path.glob("shop.db.before-restore-*")
    assert _names(saved) == ["old"]
    assert "Restored from backup.db" in dialog._status.text()
    assert str(saved) in dialog._status.text()
    assert not dialog._start_btn.isEnabled()  # not twice by accident


# ── the panel ───────────────────────────────────────────────────────


class _Keyring:
    def __init__(self):
        self.entries = {}

    def get_password(self, _service, name):
        return self.entries.get(name)

    def set_password(self, _service, name, value):
        self.entries[name] = value

    def delete_password(self, _service, name):
        self.entries.pop(name, None)


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    import keyring

    fake = _Keyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(fake, name))
    monkeypatch.setattr(db_explorer, "_global_db_manager", None)
    monkeypatch.setattr(db_notebook, "_store", db_notebook.DBNotebookStore(tmp_path / "nb.db"))
    monkeypatch.setattr(
        database_panel.DatabasePanel, "_CONFIG_PATH", tmp_path / "db_connections.json"
    )
    widget = database_panel.DatabasePanel()
    qtbot.addWidget(widget)
    return widget


def _tables(panel) -> list[str]:
    tree = panel._schema_tree
    return tree.table_names()


def test_the_backup_button_offers_both_directions(panel):
    actions = [action.text() for action in panel._backup_btn.menu().actions()]
    assert actions == ["Back up…", "Restore from a backup…"]


def test_backup_and_restore_need_a_connection(panel, monkeypatch):
    opened = []
    monkeypatch.setattr(database_panel, "BackupDialog", lambda *a: opened.append(a))
    monkeypatch.setattr(database_panel, "RestoreDialog", lambda *a: opened.append(a))
    panel._backup_action.trigger()
    panel._restore_action.trigger()
    assert opened == [] and panel._status_label.text() == "Add a connection first"


def test_backup_opens_the_dialog_for_the_selected_connection(panel, tmp_path, monkeypatch):
    panel._add_connection(NewConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db"), True))
    seen = []

    class Dialog:
        finished_ok = True

        def __init__(self, connection, parent):
            seen.append((connection.name, parent))

        def exec(self):
            seen.append("exec")

    monkeypatch.setattr(database_panel, "BackupDialog", Dialog)
    panel._backup_action.trigger()
    assert seen == [("shop", panel), "exec"]
    assert panel._status_label.text() == "Backed up: shop"


async def test_after_a_restore_the_panel_reconnects_and_shows_the_new_tables(
    panel, tmp_path, monkeypatch
):
    live = _sqlite(tmp_path / "shop.db")
    panel._add_connection(NewConnection("shop", "sqlite", live, False))
    conn = panel._db_manager.get_connection("shop")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["items"])

    handles = []

    class Dialog:
        finished_ok = True

        def __init__(self, connection, parent):
            self._conn = connection

        def exec(self):
            # What the real dialog does for SQLite: close the handle, swap the file.
            handles.append(self._conn._sqlite_conn)
            self._conn._sqlite_conn = None
            os.remove(live)
            other = sqlite3.connect(live)
            other.execute("CREATE TABLE restored_table (a)")
            other.commit()
            other.close()

    monkeypatch.setattr(database_panel, "RestoreDialog", Dialog)
    panel._restore_action.trigger()
    await _settle(lambda: _tables(panel) == ["restored_table"])
    assert conn.is_connected
    await handles[0].close()
    await conn.disconnect()


async def test_a_restore_that_was_not_run_leaves_the_connection_alone(panel, tmp_path, monkeypatch):
    panel._add_connection(NewConnection("shop", "sqlite", _sqlite(tmp_path / "shop.db"), False))
    conn = panel._db_manager.get_connection("shop")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["items"])
    reconnects = []
    monkeypatch.setattr(panel, "_connect_selected", lambda: reconnects.append(1))

    class Dialog:
        finished_ok = False

        def __init__(self, *args):
            pass

        def exec(self):
            pass

    monkeypatch.setattr(database_panel, "RestoreDialog", Dialog)
    panel._restore_action.trigger()
    assert reconnects == [] and conn.is_connected
    await conn.disconnect()
