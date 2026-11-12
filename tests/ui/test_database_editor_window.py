"""The SQL editor window: schema tree, history rows, header, refresh."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtGui import QFontInfo  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core import db_notebook  # noqa: E402
from polyglot_ai.core.db_dialects import build_url, describe_target  # noqa: E402
from polyglot_ai.core.db_explorer import ColumnInfo, DatabaseConnection, TableInfo  # noqa: E402
from polyglot_ai.ui.panels import database_panel  # noqa: E402
from polyglot_ai.ui.panels.database_widgets import (  # noqa: E402
    ElidedLabel,
    HistoryDelegate,
    SchemaTree,
    history_meta,
    history_preview,
)

TABLES = [
    TableInfo(
        name="cases",
        columns=[
            ColumnInfo("id", "int", nullable=False, primary_key=True),
            ColumnInfo("client", "character varying(255)", nullable=False, primary_key=False),
            ColumnInfo("opened_at", "timestamp without time zone", True, False),
        ],
    ),
    TableInfo(name="notes", columns=[ColumnInfo("body", "text", True, False)]),
]


async def _settle(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting for the window"
        await asyncio.sleep(0.01)
        QApplication.processEvents()


# ── schema tree ─────────────────────────────────────────────────────


def test_types_stay_in_view_and_names_keep_most_of_a_narrow_pane(qtbot):
    """The name column was fixed at 200px in a 220px pane: types were off-screen."""
    tree = SchemaTree()
    qtbot.addWidget(tree)
    tree.resize(220, 300)
    tree.show()
    tree.set_tables(TABLES)

    header = tree.header()
    viewport = tree.viewport().width()
    assert header.sectionSize(0) + header.sectionSize(1) <= viewport
    assert header.sectionSize(1) >= 40
    # Long PostgreSQL type names don't squeeze the names out.
    assert header.sectionSize(0) >= viewport * 0.5

    tree.resize(600, 300)
    QApplication.processEvents()
    # All of the type, unless this machine's font makes even that too wide.
    share = int(tree.viewport().width() * tree.TYPE_SHARE)
    assert header.sectionSize(1) == min(tree.sizeHintForColumn(1) + 12, share)


def test_columns_show_type_key_and_nullability(qtbot):
    tree = SchemaTree()
    qtbot.addWidget(tree)
    tree.set_tables(TABLES)

    assert tree.table_names() == ["cases", "notes"]
    cases = tree.topLevelItem(0)
    assert cases.text(1) == "3 cols" and tree.topLevelItem(1).text(1) == "1 col"
    assert cases.isExpanded()
    assert (cases.child(0).text(0), cases.child(0).text(1)) == ("id", "int PK")
    assert cases.child(0).toolTip(0) == "id — int, primary key, not null"
    assert cases.child(2).toolTip(1) == "opened_at — timestamp without time zone, nullable"


def test_expand_arrows_are_drawn(qtbot):
    """The app styles ::branch, which turns Qt's own arrows off."""
    from polyglot_ai.ui.panels.database_widgets import schema_tree_style

    style = schema_tree_style()
    for state in ("closed", "open"):
        rule = style.split(f"branch:has-children:{state}")[1].split("}")[0]
        path = rule.split("url(")[1].split(")")[0]
        assert os.path.getsize(path) > 0


# ── history rows ────────────────────────────────────────────────────


def _entry(**kwargs):
    base = dict(sql="SELECT 1", executed_at=0.0, duration_ms=0, row_count=-1, error=None)
    return SimpleNamespace(**{**base, **kwargs})


def test_history_line_says_when_and_how_it_went():
    now = time.mktime((2026, 11, 12, 15, 0, 0, 0, 0, -1))
    today = time.mktime((2026, 11, 12, 14, 32, 0, 0, 0, -1))
    earlier = time.mktime((2026, 11, 3, 9, 5, 0, 0, 0, -1))

    ok = _entry(executed_at=today, row_count=1234, duration_ms=3)
    assert history_meta(ok, now) == "14:32 · 1,234 rows · 3 ms"
    assert history_meta(_entry(executed_at=today, row_count=1), now) == "14:32 · 1 row"
    assert history_meta(_entry(executed_at=earlier, row_count=0), now).startswith("03 Nov 09:05")


def test_failed_query_shows_its_reason():
    """It used to be a bare ✗."""
    now = time.mktime((2026, 11, 12, 15, 0, 0, 0, 0, -1))
    failed = _entry(
        executed_at=now, error="Incorrect syntax near 'cases'.\n  (error 102)", row_count=-1
    )
    assert history_meta(failed, now) == "15:00 · Failed: Incorrect syntax near 'cases'. (error 102)"


def test_history_preview_is_the_sql_not_its_header_comment():
    assert history_preview("-- open cases\n\nSELECT *\n  FROM cases") == "SELECT * FROM cases"
    assert history_preview("-- only a comment") == "-- only a comment"


# ── the window ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = db_notebook.DBNotebookStore(tmp_path / "nb.db")
    monkeypatch.setattr(db_notebook, "_store", store)
    return store


def _server_connection(db_type="mssql", read_only=True, **options):
    url = build_url(db_type, "db.example.com", 1433, "sa", "s3cr3t/p@ss", "demo")
    return DatabaseConnection(
        "demo-prod", db_type, url, read_only=read_only, options={"url_encoded": True, **options}
    )


async def test_header_says_which_connection_where_and_whether_it_can_write(qtbot, store):
    conn = _server_connection(tunnel={"kind": "ssh", "host": "bastion", "user": "ec2-user"})
    window = database_panel._DatabaseWindow(conn)
    qtbot.addWidget(window)

    assert window._conn_label.text() == "demo-prod"
    assert window._type_badge.text() == "MSSQL"
    where = window._target_label.full_text()
    assert where == "sa@db.example.com:1433/demo  ·  SSH via ec2-user@bastion"
    assert "s3cr3t" not in where
    assert window._access_badge.text() == "READ-ONLY"

    conn.read_only = False
    window.set_connection(conn)
    assert window._access_badge.text() == "WRITE ACCESS"


async def test_editor_is_fixed_width_and_its_example_fits_the_engine(qtbot, store):
    """The app-wide font rule had it showing SQL in the UI's sans-serif."""
    from polyglot_ai.ui import theme

    app = QApplication.instance()
    previous = app.styleSheet()
    app.setStyleSheet(theme._generate_qss())
    try:
        window = database_panel._DatabaseWindow(_server_connection())
        qtbot.addWidget(window)
        window.show()
        QApplication.processEvents()
        if sys.platform != "win32":
            # The offscreen platform on the Windows CI runner resolves none
            # of the families, so there is nothing to check there.
            assert QFontInfo(window._sql_editor.font()).fixedPitch()
        assert "SELECT TOP 100 * FROM [table_name]" in window._sql_editor.placeholderText()
        assert window._highlighter._brackets is True

        window.set_connection(_server_connection("postgresql"))
        assert window._sql_editor.placeholderText().endswith('FROM "table_name" LIMIT 100')
        assert window._highlighter._brackets is False
    finally:
        app.setStyleSheet(previous)


async def test_history_rows_carry_the_reason_for_a_failure(qtbot, store):
    conn = _server_connection()
    store.add_history("demo-prod", "SELECT * FROM cases", row_count=4, duration_ms=3)
    store.add_history("demo-prod", "SELECT * FORM cases", error="Incorrect syntax near 'FORM'.")
    window = database_panel._DatabaseWindow(conn)
    qtbot.addWidget(window)

    history = window._history_list
    assert isinstance(history.itemDelegate(), HistoryDelegate)
    failed, ok = history.item(0), history.item(1)
    assert failed.text() == "SELECT * FORM cases"  # no ✗, no cut-off suffix
    assert failed.data(HistoryDelegate.FAILED_ROLE) is True
    assert "Failed: Incorrect syntax near 'FORM'." in failed.data(HistoryDelegate.META_ROLE)
    assert "Incorrect syntax near 'FORM'." in failed.toolTip()
    assert ok.data(HistoryDelegate.META_ROLE).endswith("4 rows · 3 ms")
    # Two lines tall, and painting them doesn't raise.
    assert history.sizeHintForRow(0) > history.fontMetrics().height() * 1.6
    window.resize(900, 600)
    window.grab()


async def test_refresh_button_picks_up_a_new_table(qtbot, store, tmp_path):
    path = tmp_path / "a.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE apples (id INTEGER PRIMARY KEY)")
    conn = DatabaseConnection("local", "sqlite", str(path))
    assert (await conn.connect())[0]
    window = database_panel._DatabaseWindow(conn)
    qtbot.addWidget(window)
    try:
        await _settle(lambda: window._schema_tree.table_names() == ["apples"])
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE bananas (id INTEGER PRIMARY KEY)")

        window._refresh_btn.click()
        await _settle(lambda: window._schema_tree.table_names() == ["apples", "bananas"])
        assert window._results_status.text() == "Schema reloaded — 2 tables"
        assert window._refresh_btn.isEnabled()
    finally:
        await conn.disconnect()


async def test_refresh_reopens_a_dropped_connection(qtbot, store, tmp_path):
    path = tmp_path / "a.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE apples (id INTEGER PRIMARY KEY)")
    conn = DatabaseConnection("local", "sqlite", str(path))
    window = database_panel._DatabaseWindow(conn)
    qtbot.addWidget(window)
    try:
        assert not conn.is_connected
        window._refresh_btn.click()
        await _settle(lambda: window._schema_tree.table_names() == ["apples"])
        assert conn.is_connected
    finally:
        await conn.disconnect()


async def test_full_error_is_in_the_status_tooltip(qtbot, store):
    from polyglot_ai.core.db_explorer import QueryResult

    window = database_panel._DatabaseWindow(_server_connection())
    qtbot.addWidget(window)
    long_error = "relation does not exist: " + "x" * 200
    window._show_results(QueryResult.from_error(long_error))
    assert window._results_status.toolTip() == long_error
    assert len(window._results_status.text()) < len(long_error)

    window._show_results(QueryResult(columns=["a"], rows=[[1]], row_count=1, execution_time=0.1))
    assert window._results_status.toolTip() == ""


# ── small pieces ────────────────────────────────────────────────────


def test_describe_target_never_shows_the_password():
    url = build_url("postgresql", "db.example.com", 5432, "app", "p@ss/word", "orders")
    assert describe_target("postgresql", url, {"url_encoded": True}) == (
        "app@db.example.com:5432/orders"
    )
    assert describe_target("sqlite", "/data/app.db") == "/data/app.db"
    assert describe_target("mysql", "mysql://localhost:3306/") == "localhost:3306"


def test_elided_label_does_not_set_the_window_width(qtbot):
    label = ElidedLabel(
        "sa@a-very-long-host-name.cluster-abc.eu-west-1.rds.amazonaws.com:1433/demo"
    )
    qtbot.addWidget(label)
    assert label.minimumSizeHint().width() == 0
    label.resize(120, 20)
    label.show()
    QApplication.processEvents()
    assert label.text() != label.full_text() and "…" in label.text()
    assert label.toolTip() == label.full_text()
