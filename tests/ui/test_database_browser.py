"""The schema tree as a database browser: databases, their tables, the tables' columns."""

from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core import db_explorer, db_notebook  # noqa: E402
from polyglot_ai.core.db_explorer import ColumnInfo, DatabaseInfo, TableInfo  # noqa: E402
from polyglot_ai.ui.panels import database_panel  # noqa: E402
from polyglot_ai.ui.panels.database_dialogs import NewConnection  # noqa: E402
from polyglot_ai.ui.panels.database_widgets import (  # noqa: E402
    KIND_ROLE,
    SchemaTree,
    fill_schema_tree,
    load_database_into,
    other_database_problem,
)

CASES = TableInfo(
    "cases",
    [ColumnInfo("id", "int", nullable=False, primary_key=True), ColumnInfo("title", "nvarchar")],
)
NOTES = TableInfo("notes", [ColumnInfo("body", "text")])
DATABASES = [
    DatabaseInfo("shop", current=True),
    DatabaseInfo("archive"),
    DatabaseInfo("master", system=True),
]


async def _settle(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting for the tree"
        await asyncio.sleep(0.01)
        QApplication.processEvents()


def _children(item) -> list[str]:
    return [item.child(i).text(0) for i in range(item.childCount())]


@pytest.fixture
def tree(qtbot):
    widget = SchemaTree()
    qtbot.addWidget(widget)
    widget.resize(320, 400)
    return widget


# ── the tree ────────────────────────────────────────────────────────


def test_databases_are_listed_with_the_connected_one_open(tree):
    tree.set_databases(DATABASES, [CASES, NOTES])

    assert tree.database_names() == ["shop", "archive", "master"]
    shop, archive, master = (tree.topLevelItem(i) for i in range(3))
    assert shop.isExpanded() and _children(shop) == ["cases", "notes"]
    assert shop.text(1) == "2 tables" and shop.font(0).bold()
    assert "signed in to" in shop.toolTip(0)
    assert _children(shop.child(0)) == ["id", "title"]  # columns, as before
    assert shop.child(0).child(0).text(1) == "int PK"
    # The others wait to be opened, and can be: each has its arrow.
    assert not archive.isExpanded() and archive.childCount() == 1
    assert archive.child(0).data(0, KIND_ROLE) == "note"
    assert master.text(1) == "system" and "system database" in master.toolTip(0)
    assert tree.table_names() == ["cases", "notes"]
    assert tree.table_names("archive") == []


def test_opening_a_database_asks_for_its_tables_once(tree, qtbot):
    tree.set_databases(DATABASES, [CASES])
    asked = []
    tree.database_requested.connect(asked.append)
    archive = tree.topLevelItem(1)

    archive.setExpanded(True)
    archive.setExpanded(False)
    archive.setExpanded(True)
    tree.topLevelItem(0).setExpanded(False)
    tree.topLevelItem(0).setExpanded(True)  # the connected one is already filled

    assert asked == ["archive"]


def test_a_databases_tables_are_filled_in_under_it(tree):
    tree.set_databases(DATABASES, [CASES])
    tree.set_database_tables("archive", [NOTES, CASES])

    archive = tree.topLevelItem(1)
    assert _children(archive) == ["notes", "cases"]
    assert archive.text(1) == "2 tables"
    assert not archive.child(0).isExpanded()  # columns folded: it is a long list already
    assert archive.child(1).toolTip(0) == "archive.cases"
    assert tree.table_names("archive") == ["notes", "cases"]
    assert tree.table_names() == ["cases"]  # the connected database's, untouched


def test_an_empty_database_says_so(tree):
    tree.set_databases(DATABASES, [])
    assert _children(tree.topLevelItem(0)) == ["No tables"]
    assert tree.topLevelItem(0).text(1) == "0 tables"
    tree.set_database_tables("archive", [])
    assert _children(tree.topLevelItem(1)) == ["No tables"]
    assert tree.table_names() == [] and tree.table_names("archive") == []


def test_a_database_that_cannot_be_read_shows_why_and_can_be_tried_again(tree):
    tree.set_databases(DATABASES, [CASES])
    asked = []
    tree.database_requested.connect(asked.append)
    archive = tree.topLevelItem(1)
    archive.setExpanded(True)

    tree.set_database_tables("archive", [], error="Login failed for database archive")
    (note,) = (archive.child(i) for i in range(archive.childCount()))
    assert note.text(0) == "Can't read this database: Login failed for database archive"
    assert note.toolTip(0) == "Login failed for database archive"

    archive.setExpanded(False)
    archive.setExpanded(True)
    assert asked == ["archive", "archive"]


def test_table_at_tells_tables_from_everything_else(tree):
    tree.set_databases(DATABASES, [CASES])
    tree.set_database_tables("archive", [NOTES])
    shop, archive = tree.topLevelItem(0), tree.topLevelItem(1)

    assert tree.table_at(shop.child(0)) == ("", "cases")  # "" = the connected database
    assert tree.table_at(archive.child(0)) == ("archive", "notes")
    assert tree.table_at(shop) is None  # a database
    assert tree.table_at(shop.child(0).child(0)) is None  # a column
    assert tree.table_at(tree.topLevelItem(2).child(0)) is None  # the "Loading…" note
    assert tree.table_at(None) is None


def test_tables_alone_when_there_is_no_list_of_databases(tree):
    fill_schema_tree(tree, [CASES, NOTES], [])
    assert tree.database_names() == []
    assert tree.table_names() == ["cases", "notes"]
    assert tree.table_at(tree.topLevelItem(0)) == ("", "cases")
    assert tree.topLevelItem(0).isExpanded()

    fill_schema_tree(tree, [CASES], DATABASES)
    assert tree.database_names() == ["shop", "archive", "master"]


def test_filling_a_database_that_is_no_longer_shown_does_nothing(tree):
    tree.set_tables([CASES])
    tree.set_database_tables("archive", [NOTES])
    assert tree.table_names() == ["cases"]


# ── reading a database when it is opened ────────────────────────────


class _Conn:
    db_type = "mssql"

    def __init__(self, answer):
        self.answer, self.asked = answer, []
        self.release = asyncio.Event()
        self.release.set()

    async def get_schema(self, database=None):
        self.asked.append(database)
        await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_opening_a_database_reads_and_shows_its_tables(tree):
    tree.set_databases(DATABASES, [CASES])
    conn = _Conn([NOTES])
    tree.database_requested.connect(lambda name: load_database_into(tree, conn, name))

    tree.topLevelItem(1).setExpanded(True)
    await _settle(lambda: tree.table_names("archive") == ["notes"])
    assert conn.asked == ["archive"]


async def test_the_servers_refusal_is_shown_under_the_database(tree):
    tree.set_databases(DATABASES, [CASES])
    conn = _Conn(PermissionError("The server principal is not able to access the database"))
    load_database_into(tree, conn, "archive")
    await _settle(lambda: "not able to access" in tree.topLevelItem(1).child(0).text(0))


async def test_tables_read_for_an_earlier_connection_are_not_shown_in_the_next(tree):
    tree.set_databases(DATABASES, [CASES])
    conn = _Conn([NOTES])
    conn.release.clear()
    load_database_into(tree, conn, "archive")
    await _settle(lambda: conn.asked == ["archive"])

    # Meanwhile the panel reconnected somewhere else that also has an "archive".
    tree.set_databases(DATABASES, [CASES])
    conn.release.set()
    await asyncio.sleep(0.05)
    QApplication.processEvents()
    assert tree.table_names("archive") == []


def test_which_engines_can_query_a_table_in_another_database():
    assert other_database_problem("mssql", "archive") == ""
    assert other_database_problem("mysql", "archive") == ""
    assert other_database_problem("postgresql", "") == ""  # its own database: no problem
    problem = other_database_problem("postgresql", "archive")
    assert "reads one database" in problem and "“archive”" in problem


# ── the panel and the editor window ─────────────────────────────────


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


def _sqlite(path) -> str:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE apples (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("CREATE TABLE pears (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return str(path)


async def test_connecting_shows_the_database_with_its_tables(panel, tmp_path):
    panel._add_connection(NewConnection("local", "sqlite", _sqlite(tmp_path / "fruit.db"), True))
    conn = panel._db_manager.get_connection("local")
    panel._connect_selected()
    tree = panel._schema_tree
    await _settle(lambda: tree.database_names() == ["fruit.db"])
    try:
        assert tree.table_names() == ["apples", "pears"]
        database = tree.topLevelItem(0)
        assert database.isExpanded() and database.text(1) == "2 tables"
        assert _children(database.child(0)) == ["id", "name"]
    finally:
        await conn.disconnect()


async def test_the_editor_window_shows_the_same_and_previews_a_table(panel, tmp_path, qtbot):
    panel._add_connection(NewConnection("local", "sqlite", _sqlite(tmp_path / "fruit.db"), True))
    conn = panel._db_manager.get_connection("local")
    assert (await conn.connect())[0]
    panel._open_full_window()
    window = panel._full_window
    qtbot.addWidget(window)
    tree = window._schema_tree
    try:
        await _settle(lambda: tree.database_names() == ["fruit.db"])
        assert tree.table_names() == ["apples", "pears"]

        window._on_table_clicked(tree.topLevelItem(0), 0)  # the database row: nothing to run
        assert window._sql_editor.toPlainText() == ""
        window._on_table_clicked(tree.topLevelItem(0).child(1), 0)
        assert window._sql_editor.toPlainText() == 'SELECT * FROM "pears" LIMIT 100'

        window._reload_schema()
        await _settle(lambda: "Schema reloaded — 2 tables" in window._results_status.text())
        assert tree.database_names() == ["fruit.db"]
    finally:
        window.close()
        await conn.disconnect()


class _Server:
    """A connected server with two databases, for the panel to browse."""

    name, read_only, route, is_connected, options = "srv", True, "", True, {}
    _connection_string = ""

    def __init__(self, db_type):
        self.db_type = db_type
        self.ran = []

    async def get_schema(self, database=None):
        return [NOTES] if database else [CASES]

    async def list_databases(self):
        return [DatabaseInfo("shop", current=True), DatabaseInfo("archive")]

    async def ensure_connected(self):
        return True, ""

    async def execute_query(self, sql, max_rows=10_000):
        self.ran.append(sql)
        return db_explorer.QueryResult(
            columns=["body"], rows=[["x"]], row_count=1, execution_time=0.0
        )


@pytest.mark.parametrize(
    ("db_type", "sql"),
    [
        ("mssql", "SELECT TOP 100 * FROM [archive].[dbo].[notes]"),
        ("mysql", "SELECT * FROM `archive`.`notes` LIMIT 100"),
    ],
)
async def test_a_table_in_another_database_is_previewed_by_its_full_name(
    qtbot, tmp_path, monkeypatch, db_type, sql
):
    monkeypatch.setattr(db_notebook, "_store", db_notebook.DBNotebookStore(tmp_path / "nb.db"))
    server = _Server(db_type)
    window = database_panel._DatabaseWindow(server)
    qtbot.addWidget(window)
    tree = window._schema_tree
    await _settle(lambda: tree.database_names() == ["shop", "archive"])

    tree.topLevelItem(1).setExpanded(True)
    await _settle(lambda: tree.table_names("archive") == ["notes"])
    window._on_table_clicked(tree.topLevelItem(1).child(0), 0)
    await _settle(lambda: server.ran == [sql])

    assert window._sql_editor.toPlainText() == sql
    # Editing a cell of that result must write to the same table, not a namesake here.
    assert window._table_sql() == sql.split(" FROM ")[1].split(" LIMIT")[0]


async def test_postgres_explains_instead_of_running_a_query_that_cannot_work(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(db_notebook, "_store", db_notebook.DBNotebookStore(tmp_path / "nb.db"))
    server = _Server("postgresql")
    window = database_panel._DatabaseWindow(server)
    qtbot.addWidget(window)
    tree = window._schema_tree
    await _settle(lambda: tree.database_names() == ["shop", "archive"])
    tree.set_database_tables("archive", [NOTES])

    window._on_table_clicked(tree.topLevelItem(1).child(0), 0)
    assert "reads one database" in window._results_status.text()
    assert window._sql_editor.toPlainText() == "" and server.ran == []

    window._on_table_clicked(tree.topLevelItem(0).child(0), 0)  # its own database still works
    await _settle(lambda: server.ran == ['SELECT * FROM "cases" LIMIT 100'])
