"""Changing or deleting a saved connection: the manager and the notebook."""

from __future__ import annotations

from polyglot_ai.core.db_explorer import DatabaseManager
from polyglot_ai.core.db_notebook import DBNotebookStore


def _manager() -> DatabaseManager:
    manager = DatabaseManager()
    manager.add_connection_sync("first", "sqlite", "/tmp/first.db")
    manager.add_connection_sync(
        "second", "postgresql", "postgresql://u:p@h:5432/d", options={"ssl": "prefer"}
    )
    manager.add_connection_sync("third", "sqlite", "/tmp/third.db")
    return manager


def test_rename_and_read_only_keep_the_same_connection():
    manager = _manager()
    before = manager.get_connection("second")
    current, retired = manager.replace_connection_sync(
        "second",
        "renamed",
        "postgresql",
        "postgresql://u:p@h:5432/d",
        read_only=False,
        options={"ssl": "prefer"},
    )
    assert current is before and retired is None
    assert (current.name, current.read_only) == ("renamed", False)
    assert manager.connection_names == ["first", "renamed", "third"]
    assert manager.get_connection("second") is None


def test_changed_details_make_a_new_connection_in_the_same_place():
    manager = _manager()
    before = manager.get_connection("second")
    current, retired = manager.replace_connection_sync(
        "second",
        "second",
        "postgresql",
        "postgresql://u:p@other-host:5432/d",
        read_only=True,
        options={"ssl": "prefer"},
    )
    assert retired is before and current is not before
    assert current._connection_string == "postgresql://u:p@other-host:5432/d"
    assert manager.get_connection("second") is current
    assert manager.connection_names == ["first", "second", "third"]


def test_changed_options_alone_count_as_changed_details():
    manager = _manager()
    _current, retired = manager.replace_connection_sync(
        "second",
        "second",
        "postgresql",
        "postgresql://u:p@h:5432/d",
        options={"ssl": "require"},
    )
    assert retired is not None


def test_notebook_follows_a_rename(tmp_path):
    store = DBNotebookStore(tmp_path / "notebook.db")
    store.add_history("old", "SELECT 1")
    store.save_snippet("old", "count", "SELECT count(*) FROM t")
    store.save_snippet("other", "count", "SELECT 2")
    # Left behind under the new name by a connection that no longer exists.
    store.save_snippet("new", "count", "SELECT stale")

    store.rename_connection("old", "new")

    assert [h.sql for h in store.list_history("new")] == ["SELECT 1"]
    assert store.list_history("old") == [] and store.list_snippets("old") == []
    assert [s.sql for s in store.list_snippets("new")] == ["SELECT count(*) FROM t"]
    assert [s.sql for s in store.list_snippets("other")] == ["SELECT 2"]


def test_dropping_a_connection_hands_it_back_and_keeps_the_others_in_order():
    manager = _manager()
    second = manager.get_connection("second")
    assert manager.drop_connection_sync("second") is second
    assert manager.connection_names == ["first", "third"]
    assert manager.drop_connection_sync("second") is None


def test_notebook_counts_and_forgets_one_connection_only(tmp_path):
    store = DBNotebookStore(tmp_path / "nb.db")
    for sql in ("SELECT 1", "SELECT 2", "SELECT 3"):
        store.add_history("gone", sql)
    store.save_snippet("gone", "count", "SELECT count(*) FROM t")
    store.add_history("kept", "SELECT 9")
    store.save_snippet("kept", "count", "SELECT 8")

    assert store.counts("gone") == (3, 1)
    assert store.counts("never-used") == (0, 0)

    store.forget_connection("gone")

    assert store.counts("gone") == (0, 0)
    assert store.list_history("gone") == [] and store.list_snippets("gone") == []
    assert [h.sql for h in store.list_history("kept")] == ["SELECT 9"]
    assert [s.sql for s in store.list_snippets("kept")] == ["SELECT 8"]
