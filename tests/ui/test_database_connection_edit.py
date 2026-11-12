"""Editing and deleting a saved database connection from the Database panel."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

from polyglot_ai.core import db_explorer, db_notebook  # noqa: E402
from polyglot_ai.core.db_dialects import build_url, form_values, parse_url  # noqa: E402
from polyglot_ai.ui.panels import database_panel  # noqa: E402
from polyglot_ai.ui.panels.database_dialogs import AddConnectionDialog, NewConnection  # noqa: E402
from polyglot_ai.ui.panels.database_widgets import delete_explanation, notes_label  # noqa: E402

AURORA_OPTIONS = {
    "url_encoded": True,
    "ssl": "verify",
    "ca_file": "~/global-bundle.pem",
    "engine_label": "Amazon Aurora (PostgreSQL)",
    "tunnel": {"kind": "ssh", "host": "bastion.example.com", "user": "ec2-user", "port": 22},
}
AURORA_URL = build_url("postgresql", "orders.rds.example.com", 5432, "app", "p@ss/word", "orders")


def _edit_dialog(name, db_type, url, options, taken=()):
    return AddConnectionDialog(
        prefill=form_values(name, db_type, url, options), editing=True, taken_names=taken
    )


# ── the dialog ──────────────────────────────────────────────────────


def test_saved_connection_reopens_with_everything_it_holds(qtbot):
    dialog = _edit_dialog("orders-prod", "postgresql", AURORA_URL, AURORA_OPTIONS)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Edit Database Connection"
    assert dialog._type_combo.currentText() == "Amazon Aurora (PostgreSQL)"
    assert dialog._host_input.text() == "orders.rds.example.com"
    assert dialog._pass_input.text() == "p@ss/word"
    assert dialog._ssh_host.text() == "bastion.example.com"

    # Saved without touching anything: nothing changes.
    value = dialog.get_values()
    assert (value.name, value.db_type) == ("orders-prod", "postgresql")
    assert value.connection_string == AURORA_URL
    assert value.options == AURORA_OPTIONS


def test_iam_and_session_manager_connection_round_trips(qtbot):
    options = {
        "url_encoded": True,
        "ssl": "require",
        "iam": {"profile": "prod", "region": "eu-west-1"},
        "tunnel": {"kind": "ssm", "target": "i-0abc123", "profile": "prod", "region": "eu-west-1"},
    }
    url = build_url("mysql", "db.internal", 3306, "iam_user", "", "app")
    dialog = _edit_dialog("aurora", "mysql", url, options)
    qtbot.addWidget(dialog)

    assert dialog._auth_combo.currentData() == "iam"
    assert dialog._ssm_target.currentText() == "i-0abc123"
    value = dialog.get_values()
    assert value.connection_string == url and value.options == options


def test_one_field_changed_changes_only_that(qtbot):
    dialog = _edit_dialog("orders-prod", "postgresql", AURORA_URL, AURORA_OPTIONS)
    qtbot.addWidget(dialog)
    dialog._host_input.setText("orders-replica.rds.example.com")

    value = dialog.get_values()
    target = parse_url(value.connection_string, "postgresql")
    assert target.host == "orders-replica.rds.example.com"
    assert (target.user, target.password, target.database) == ("app", "p@ss/word", "orders")
    assert value.options == AURORA_OPTIONS


def test_sqlite_connection_reopens_with_its_file(qtbot):
    dialog = _edit_dialog("local", "sqlite", "/data/app.db", {})
    qtbot.addWidget(dialog)
    assert dialog._conn_input.text() == "/data/app.db"
    assert dialog.get_values() == NewConnection("local", "sqlite", "/data/app.db", True)


def test_cannot_be_renamed_onto_another_connection(qtbot):
    dialog = _edit_dialog("local", "sqlite", "/data/app.db", {}, taken=["staging"])
    qtbot.addWidget(dialog)
    dialog._name_input.setText("staging")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "already called" in dialog._error_label.text()

    dialog._name_input.setText("local-2")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_plain_engine_choice_stores_no_label(qtbot):
    dialog = AddConnectionDialog(prefill={"db_type": "mysql", "name": "x", "host": "h"})
    qtbot.addWidget(dialog)
    assert "engine_label" not in dialog.get_values().options
    dialog._type_combo.setCurrentIndex(dialog._type_combo.findText("MariaDB"))
    assert dialog.get_values().options["engine_label"] == "MariaDB"


# ── the panel ───────────────────────────────────────────────────────


class _Keyring:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def get_password(self, _service, name):
        return self.entries.get(name)

    def set_password(self, _service, name, value):
        self.entries[name] = value

    def delete_password(self, _service, name):
        del self.entries[name]


def _make_db(path, table: str) -> str:
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    return str(path)


async def _settle(condition, timeout: float = 5.0) -> None:
    """Let the panel's tasks and queued Qt calls run until ``condition()``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting for the panel"
        await asyncio.sleep(0.01)
        QApplication.processEvents()


@pytest.fixture
def env(qtbot, tmp_path, monkeypatch):
    import keyring

    fake = _Keyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(fake, name))
    monkeypatch.setattr(db_explorer, "_global_db_manager", None)
    monkeypatch.setattr(db_notebook, "_store", db_notebook.DBNotebookStore(tmp_path / "nb.db"))
    config = tmp_path / "db_connections.json"
    monkeypatch.setattr(database_panel.DatabasePanel, "_CONFIG_PATH", config)
    panel = database_panel.DatabasePanel()
    qtbot.addWidget(panel)
    return panel, fake, config


def _labels(panel) -> list[str]:
    return [panel._conn_combo.itemText(i) for i in range(panel._conn_combo.count())]


def _tables(panel) -> list[str]:
    tree = panel._schema_tree
    return tree.table_names()


async def test_rename_keeps_the_connection_open_and_moves_what_is_saved(env, tmp_path):
    panel, keyring, config = env
    path = _make_db(tmp_path / "a.db", "apples")
    panel._add_connection(NewConnection("local", "sqlite", path, True))
    panel._add_connection(NewConnection("other", "sqlite", path, True))
    panel._conn_combo.setCurrentIndex(0)
    conn = panel._db_manager.get_connection("local")
    assert (await conn.connect())[0]
    db_notebook.get_notebook_store().add_history("local", "SELECT 1")

    panel._apply_edit("local", NewConnection("local-dev", "sqlite", path, False))

    try:
        assert panel._db_manager.get_connection("local-dev") is conn
        assert conn.is_connected and conn.read_only is False
        assert _labels(panel) == ["local-dev (sqlite)", "other (sqlite)"]
        assert panel._active_connection == "local-dev"
        assert keyring.entries == {"local-dev": path, "other": path}
        saved = json.loads(config.read_text())
        assert [(e["name"], e["read_only"]) for e in saved] == [
            ("local-dev", False),
            ("other", True),
        ]
        history = db_notebook.get_notebook_store().list_history("local-dev")
        assert [h.sql for h in history] == ["SELECT 1"]
    finally:
        await conn.disconnect()


async def test_changed_details_close_the_old_connection_and_open_the_new(env, tmp_path):
    panel, keyring, _config = env
    first = _make_db(tmp_path / "a.db", "apples")
    second = _make_db(tmp_path / "b.db", "bananas")
    panel._add_connection(NewConnection("local", "sqlite", first, True))
    old = panel._db_manager.get_connection("local")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])

    panel._apply_edit("local", NewConnection("local", "sqlite", second, True))
    new = panel._db_manager.get_connection("local")

    try:
        assert new is not old
        await _settle(lambda: _tables(panel) == ["bananas"])
        assert not old.is_connected and new.is_connected
        assert keyring.entries == {"local": second}
        assert _labels(panel) == ["local (sqlite)"]
    finally:
        await new.disconnect()


async def test_edit_of_a_closed_connection_does_not_open_it(env, tmp_path):
    panel, _keyring, _config = env
    first = _make_db(tmp_path / "a.db", "apples")
    second = _make_db(tmp_path / "b.db", "bananas")
    panel._add_connection(NewConnection("local", "sqlite", first, True))

    panel._apply_edit("local", NewConnection("local", "sqlite", second, True))
    for _ in range(5):
        await asyncio.sleep(0.01)
        QApplication.processEvents()

    assert not panel._db_manager.get_connection("local").is_connected
    assert _tables(panel) == []


async def test_adding_under_a_saved_name_closes_the_connection_it_replaces(env, tmp_path):
    """It used to be dropped from the list while still open."""
    panel, _keyring, _config = env
    first = _make_db(tmp_path / "a.db", "apples")
    second = _make_db(tmp_path / "b.db", "bananas")
    panel._add_connection(NewConnection("local", "sqlite", first, True))
    old = panel._db_manager.get_connection("local")
    assert (await old.connect())[0]

    panel._add_connection(NewConnection("local", "sqlite", second, True))
    new = panel._db_manager.get_connection("local")

    try:
        await _settle(lambda: not old.is_connected and new.is_connected)
        assert _labels(panel) == ["local (sqlite)"]
    finally:
        await new.disconnect()


async def test_edit_button_opens_the_dialog_on_the_selected_connection(env, tmp_path, monkeypatch):
    panel, keyring, _config = env
    panel._add_connection(
        NewConnection("orders-prod", "postgresql", AURORA_URL, True, AURORA_OPTIONS)
    )
    panel._add_connection(NewConnection("other", "sqlite", "/data/x.db", True))
    panel._conn_combo.setCurrentIndex(0)
    seen = {}

    def fake_exec(dialog):
        seen["title"] = dialog.windowTitle()
        seen["host"] = dialog._host_input.text()
        seen["taken"] = set(dialog._taken_names)
        dialog._host_input.setText("orders-replica.rds.example.com")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AddConnectionDialog, "exec", fake_exec)
    panel._edit_selected()
    await asyncio.sleep(0)

    assert seen == {
        "title": "Edit Database Connection",
        "host": "orders.rds.example.com",
        "taken": {"other"},
    }
    conn = panel._db_manager.get_connection("orders-prod")
    assert parse_url(conn._connection_string, "postgresql").host == "orders-replica.rds.example.com"
    assert conn.options == AURORA_OPTIONS
    assert keyring.entries["orders-prod"] == conn._connection_string


async def test_open_sql_editor_follows_the_edit(env, tmp_path):
    panel, _keyring, _config = env
    first = _make_db(tmp_path / "a.db", "apples")
    second = _make_db(tmp_path / "b.db", "bananas")
    panel._add_connection(NewConnection("local", "sqlite", first, True))
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])
    panel._open_full_window()
    window = panel._full_window
    window.set_query("SELECT * FROM apples -- half-written")

    def window_tables():
        tree = window._schema_tree
        return tree.table_names()

    await _settle(lambda: window_tables() == ["apples"])

    panel._apply_edit("local", NewConnection("local", "sqlite", second, True))
    new = panel._db_manager.get_connection("local")

    try:
        await _settle(lambda: window_tables() == ["bananas"])
        assert window._conn is new
        assert window.windowTitle() == "Database — b"
        assert window._sql_editor.toPlainText() == "SELECT * FROM apples -- half-written"
    finally:
        window.close()
        await new.disconnect()


# ── deleting ────────────────────────────────────────────────────────


def _answer(monkeypatch, panel, go_ahead=True, with_notes=False) -> list[str]:
    """Stand in for the question; returns the names it was asked about."""
    asked: list[str] = []

    def confirm(conn):
        asked.append(conn.name)
        return go_ahead, with_notes

    monkeypatch.setattr(panel, "_confirm_delete", confirm)
    return asked


def _two_connections(panel, tmp_path):
    first = _make_db(tmp_path / "a.db", "apples")
    second = _make_db(tmp_path / "b.db", "bananas")
    panel._add_connection(NewConnection("local", "sqlite", first, True))
    panel._add_connection(NewConnection("other", "sqlite", second, True))
    panel._conn_combo.setCurrentIndex(0)
    return first, second


async def test_delete_button_removes_the_connection_and_what_was_saved_for_it(
    env, tmp_path, monkeypatch
):
    panel, keyring, config = env
    _first, second = _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])
    db_notebook.get_notebook_store().add_history("local", "SELECT 1")
    asked = _answer(monkeypatch, panel)

    button = panel.findChild(QPushButton, "dbDeleteBtn")
    assert button.toolTip() == "Delete this saved connection"
    button.click()
    await _settle(lambda: not conn.is_connected)

    assert asked == ["local"]
    assert panel._db_manager.connection_names == ["other"]
    assert _labels(panel) == ["other (sqlite)"]
    assert panel._active_connection == "other"
    assert keyring.entries == {"other": second}
    assert [e["name"] for e in json.loads(config.read_text())] == ["other"]
    assert _tables(panel) == []
    assert panel._status_label.text() == "Deleted: local"
    # Not asked to delete them, so the notes stay.
    assert db_notebook.get_notebook_store().counts("local") == (1, 0)


async def test_delete_can_take_the_history_and_snippets_too(env, tmp_path, monkeypatch):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    store = db_notebook.get_notebook_store()
    store.add_history("local", "SELECT 1")
    store.save_snippet("local", "count", "SELECT count(*) FROM apples")
    store.add_history("other", "SELECT 2")
    _answer(monkeypatch, panel, with_notes=True)

    panel._delete_selected()
    await asyncio.sleep(0)

    assert store.counts("local") == (0, 0)
    assert store.counts("other") == (1, 0)


async def test_answering_no_deletes_nothing(env, tmp_path, monkeypatch):
    panel, keyring, config = env
    first, second = _two_connections(panel, tmp_path)
    saved = config.read_text()
    _answer(monkeypatch, panel, go_ahead=False, with_notes=True)

    panel._delete_selected()
    await asyncio.sleep(0)

    assert panel._db_manager.connection_names == ["local", "other"]
    assert _labels(panel) == ["local (sqlite)", "other (sqlite)"]
    assert keyring.entries == {"local": first, "other": second}
    assert config.read_text() == saved


async def test_deleting_the_last_connection_leaves_an_empty_list(env, tmp_path, monkeypatch):
    panel, keyring, config = env
    panel._add_connection(NewConnection("only", "sqlite", _make_db(tmp_path / "a.db", "t"), True))
    asked = _answer(monkeypatch, panel)

    panel._delete_selected()
    await asyncio.sleep(0)

    assert _labels(panel) == [] and panel._active_connection is None
    assert keyring.entries == {} and json.loads(config.read_text()) == []

    # Nothing is selected now: the button says so instead of asking again.
    panel._delete_selected()
    assert asked == ["only"]
    assert panel._status_label.text() == "Add a connection first"

    # And nothing comes back at the next start.
    again = database_panel.DatabasePanel()
    try:
        assert again._conn_combo.count() == 0
    finally:
        again.deleteLater()


async def test_delete_closes_the_sql_editor_that_was_using_it(env, tmp_path, monkeypatch):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])
    panel._open_full_window()
    window = panel._full_window
    assert window.isVisible()
    _answer(monkeypatch, panel)

    panel._delete_selected()
    await _settle(lambda: not conn.is_connected)

    assert panel._full_window is None and not window.isVisible()


async def test_after_a_delete_the_next_connection_shows_its_tables_if_open(
    env, tmp_path, monkeypatch
):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    other = panel._db_manager.get_connection("other")
    assert (await other.connect())[0]
    _answer(monkeypatch, panel)

    try:
        panel._delete_selected()  # "local", which was never opened
        await _settle(lambda: _tables(panel) == ["bananas"])
        assert panel._active_connection == "other"
    finally:
        await other.disconnect()


async def test_a_connection_deleted_while_connecting_is_not_left_open(env, tmp_path, monkeypatch):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    opened = []
    real_connect = conn.connect

    async def connect():
        result = await real_connect()
        opened.append(result[0])
        return result

    monkeypatch.setattr(conn, "connect", connect)
    _answer(monkeypatch, panel)

    panel._connect_selected()
    panel._delete_selected()  # before the connect above has had a turn
    await _settle(lambda: opened == [True] and not conn.is_connected)
    await asyncio.sleep(0.05)
    QApplication.processEvents()

    assert not conn.is_connected
    assert _tables(panel) == []
    assert panel._status_label.text() == "Deleted: local"


def _press(monkeypatch, button_text: str, tick: bool = False) -> dict:
    """Run the real question box, pressing ``button_text`` instead of showing it."""
    seen: dict = {}

    def fake_exec(box):
        seen["text"] = box.text()
        seen["detail"] = box.informativeText()
        seen["default"] = box.defaultButton().text().replace("&", "")
        seen["tick"] = box.checkBox().text() if box.checkBox() else None
        if tick:
            box.checkBox().setChecked(True)
        for button in box.buttons():
            if button.text().replace("&", "") == button_text:
                button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return seen


async def test_the_question_names_the_connection_and_defaults_to_cancel(env, tmp_path, monkeypatch):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")

    seen = _press(monkeypatch, "Cancel")
    assert panel._confirm_delete(conn) == (False, False)
    assert seen["text"] == "Delete the saved connection “local”?"
    assert "The database itself isn't touched." in seen["detail"]
    assert "will be closed" not in seen["detail"]
    assert seen["default"] == "Cancel"
    assert seen["tick"] is None  # nothing filed under it, so nothing to offer

    seen = _press(monkeypatch, "Delete")
    assert panel._confirm_delete(conn) == (True, False)


async def test_the_question_offers_the_notes_only_when_there_are_some(env, tmp_path, monkeypatch):
    panel, _keyring, _config = env
    _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    store = db_notebook.get_notebook_store()
    store.add_history("local", "SELECT 1")
    store.add_history("local", "SELECT 2")
    store.save_snippet("local", "count", "SELECT count(*) FROM apples")
    assert (await conn.connect())[0]

    try:
        seen = _press(monkeypatch, "Delete")
        assert panel._confirm_delete(conn) == (True, False)  # the box starts unticked
        assert seen["tick"] == "Also delete its query history (2) and saved snippets (1)"
        assert "It is open now and will be closed." in seen["detail"]
        assert "are kept" in seen["detail"]

        _press(monkeypatch, "Delete", tick=True)
        assert panel._confirm_delete(conn) == (True, True)
        _press(monkeypatch, "Cancel", tick=True)
        assert panel._confirm_delete(conn) == (False, False)
    finally:
        await conn.disconnect()


def test_the_wording_of_the_question():
    assert notes_label(3, 0) == "Also delete its query history (3)"
    assert notes_label(0, 2) == "Also delete its saved snippets (2)"
    assert delete_explanation(False, False).count("\n\n") == 0
    assert delete_explanation(True, True).count("\n\n") == 2


# ── when the list can't be saved ────────────────────────────────────


def _warnings(monkeypatch) -> list[str]:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _title, text: shown.append(text))
    return shown


def _disk_full(monkeypatch) -> None:
    from polyglot_ai.core import security

    def refuse(_path, _content, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(security, "secure_write", refuse)


async def test_a_delete_that_cannot_be_saved_changes_nothing(env, tmp_path, monkeypatch):
    """It used to leave the connection open, off the list and impossible to close."""
    panel, keyring, config = env
    first, second = _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])
    panel._open_full_window()
    window = panel._full_window
    db_notebook.get_notebook_store().add_history("local", "SELECT 1")
    saved = config.read_text()
    _answer(monkeypatch, panel, with_notes=True)
    shown = _warnings(monkeypatch)
    _disk_full(monkeypatch)

    try:
        panel._delete_selected()
        await asyncio.sleep(0.05)
        QApplication.processEvents()

        assert len(shown) == 1
        assert "“local” wasn't deleted" in shown[0] and "No space left on device" in shown[0]
        assert shown[0].endswith("Nothing was changed.")
        assert panel._db_manager.connection_names == ["local", "other"]
        assert panel._db_manager.get_connection("local") is conn and conn.is_connected
        assert _labels(panel) == ["local (sqlite)", "other (sqlite)"]
        assert panel._active_connection == "local" and _tables(panel) == ["apples"]
        assert keyring.entries == {"local": first, "other": second}
        assert config.read_text() == saved
        assert panel._full_window is window and window.isVisible()
        assert db_notebook.get_notebook_store().counts("local") == (1, 0)
        assert panel._status_label.text() == "Not deleted: local"
    finally:
        window.close()
        await conn.disconnect()


async def test_a_delete_refused_by_the_keyring_changes_nothing(env, tmp_path, monkeypatch):
    import keyring as real_keyring

    panel, keyring, config = env
    first, second = _two_connections(panel, tmp_path)
    saved = config.read_text()
    _answer(monkeypatch, panel)
    shown = _warnings(monkeypatch)

    def locked(_service, _name, _value):
        raise RuntimeError("The keyring is locked")

    monkeypatch.setattr(real_keyring, "set_password", locked)
    panel._delete_selected()
    await asyncio.sleep(0)

    assert len(shown) == 1 and "The keyring is locked" in shown[0]
    assert panel._db_manager.connection_names == ["local", "other"]
    assert keyring.entries == {"local": first, "other": second}
    assert config.read_text() == saved


async def test_a_deleted_connection_is_closed_even_if_tidying_up_fails(env, tmp_path, monkeypatch):
    panel, _keyring, config = env
    _two_connections(panel, tmp_path)
    conn = panel._db_manager.get_connection("local")
    assert (await conn.connect())[0]
    _answer(monkeypatch, panel, with_notes=True)

    def broken(_name):
        raise RuntimeError("notebook is gone")

    monkeypatch.setattr(db_notebook.get_notebook_store(), "forget_connection", broken)
    with pytest.raises(RuntimeError, match="notebook is gone"):
        panel._delete_selected()
    await _settle(lambda: not conn.is_connected)

    assert panel._db_manager.connection_names == ["other"]
    assert [e["name"] for e in json.loads(config.read_text())] == ["other"]


async def test_an_edit_that_cannot_be_saved_still_closes_what_it_replaced(
    env, tmp_path, monkeypatch
):
    panel, keyring, config = env
    first, second = _two_connections(panel, tmp_path)
    old = panel._db_manager.get_connection("local")
    panel._connect_selected()
    await _settle(lambda: _tables(panel) == ["apples"])
    db_notebook.get_notebook_store().add_history("local", "SELECT 1")
    saved = config.read_text()
    shown = _warnings(monkeypatch)
    _disk_full(monkeypatch)

    panel._apply_edit("local", NewConnection("fruit", "sqlite", second, True))
    new = panel._db_manager.get_connection("fruit")

    try:
        await _settle(lambda: not old.is_connected and _tables(panel) == ["bananas"])
        assert len(shown) == 1
        assert (
            "The change to “fruit” was made, but the connection list couldn't be saved"
            in (shown[0])
        )
        assert panel._status_label.text().startswith(("Not saved: fruit", "Connect"))
        assert _labels(panel) == ["fruit (sqlite)", "other (sqlite)"]
        # What the next start will read is as it was, password and notes included.
        assert config.read_text() == saved
        assert keyring.entries["local"] == first
        assert db_notebook.get_notebook_store().counts("local") == (1, 0)
    finally:
        await new.disconnect()


async def test_an_add_that_cannot_be_saved_says_so(env, tmp_path, monkeypatch):
    panel, _keyring, config = env
    shown = _warnings(monkeypatch)
    _disk_full(monkeypatch)

    panel._add_connection(NewConnection("local", "sqlite", str(tmp_path / "a.db"), True))

    assert len(shown) == 1 and "“local” was added, but" in shown[0]
    assert _labels(panel) == ["local (sqlite)"]
    assert not config.exists()
