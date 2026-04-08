"""Tests for the task model, store, and manager."""

from __future__ import annotations

import pytest

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.task_manager import (
    EVT_TASK_CHANGED,
    EVT_TASK_LIST_CHANGED,
    EVT_TASK_NOTE_ADDED,
    EVT_TASK_STATE_CHANGED,
    EVT_TASK_WRITE_FAILED,
    TaskManager,
)
from polyglot_ai.core.task_store import TaskStore
from polyglot_ai.core.tasks import (
    CIRunSnapshot,
    PlanStep,
    Task,
    TaskKind,
    TaskNote,
    TaskState,
    TestRunSnapshot,
)


@pytest.fixture
def store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite")


@pytest.fixture
def manager(tmp_path, store) -> TaskManager:
    bus = EventBus()
    m = TaskManager(store=store, event_bus=bus)
    m.set_project_root(tmp_path)
    return m


# ── Model ───────────────────────────────────────────────────────────


def test_task_new_generates_id_and_created_note(tmp_path):
    t = Task.new(str(tmp_path), TaskKind.FEATURE, "title", "desc")
    assert t.id
    assert t.kind == TaskKind.FEATURE
    assert t.state == TaskState.PLANNING
    assert len(t.notes) == 1 and t.notes[0].kind == "created"


def test_test_run_snapshot_all_green():
    snap = TestRunSnapshot(passed=3, failed=0, skipped=1)
    assert snap.total == 4
    assert snap.all_green is True
    assert TestRunSnapshot(passed=0, failed=1).all_green is False
    assert TestRunSnapshot().all_green is False  # zero total isn't green


# ── Store ───────────────────────────────────────────────────────────


def test_store_save_and_get_round_trip(store, tmp_path):
    t = Task.new(str(tmp_path), TaskKind.BUGFIX, "fix it")
    t.branch = "fix/x"
    t.plan = [PlanStep(text="step1", status="done")]
    t.last_test_run = TestRunSnapshot(passed=2, failed=0, skipped=0, timestamp=1.0)
    t.last_ci_run = CIRunSnapshot(status="success", workflow="ci.yml")
    t.modified_files = ["a.py", "b.py"]
    t.notes.append(TaskNote(timestamp=2.0, kind="committed", text="abc"))
    store.save(t)

    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.title == "fix it"
    assert loaded.branch == "fix/x"
    assert loaded.kind == TaskKind.BUGFIX
    assert loaded.plan[0].text == "step1"
    assert loaded.plan[0].status == "done"
    assert loaded.last_test_run.passed == 2
    assert loaded.last_ci_run.status == "success"
    assert loaded.modified_files == ["a.py", "b.py"]
    assert len(loaded.notes) == 2  # created + committed


def test_store_full_field_round_trip(store, tmp_path):
    """Exhaustive round-trip: every field on Task and TaskNote.

    Pinned so that the next person adding a field can't silently
    drop data by forgetting to extend ``save`` or ``_row_to_task``.
    """
    t = Task.new(str(tmp_path), TaskKind.FEATURE, "full round trip", "with description")
    t.branch = "feat/full"
    t.base_branch = "main"
    t.pr_url = "https://example.com/pr/99"
    t.pr_number = 99
    t.chat_session_id = "1234"
    t.plan = [
        PlanStep(text="step 1", status="done", files=["a.py"], ai_notes="note"),
        PlanStep(text="step 2", status="pending"),
    ]
    t.modified_files = ["a.py", "b.py", "c/d.py"]
    t.last_test_run = TestRunSnapshot(passed=9, failed=1, skipped=2, timestamp=11.0)
    t.last_ci_run = CIRunSnapshot(
        status="failure", workflow="release.yml", url="https://ci/1", timestamp=22.0
    )
    t.notes.append(
        TaskNote(
            timestamp=33.0,
            kind="committed",
            text="commit abc",
            data={"sha": "abc"},
            source="user",
            category="git",
        )
    )
    t.acceptance_criteria = ["Tests green", "Docs updated", "Reviewer approved"]
    t.blocked_reason = ""
    t.priority = "high"
    t.blocked_by = ["dep-1", "dep-2"]
    t.state = TaskState.REVIEW
    store.save(t)

    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.id == t.id
    assert loaded.project_root == t.project_root
    assert loaded.kind == TaskKind.FEATURE
    assert loaded.title == "full round trip"
    assert loaded.description == "with description"
    assert loaded.state == TaskState.REVIEW
    assert loaded.branch == "feat/full"
    assert loaded.base_branch == "main"
    assert loaded.pr_url == "https://example.com/pr/99"
    assert loaded.pr_number == 99
    assert loaded.chat_session_id == "1234"
    assert len(loaded.plan) == 2
    assert loaded.plan[0].text == "step 1"
    assert loaded.plan[0].status == "done"
    assert loaded.plan[0].files == ["a.py"]
    assert loaded.plan[0].ai_notes == "note"
    assert loaded.modified_files == ["a.py", "b.py", "c/d.py"]
    assert loaded.last_test_run.passed == 9
    assert loaded.last_test_run.failed == 1
    assert loaded.last_test_run.skipped == 2
    assert loaded.last_ci_run.status == "failure"
    assert loaded.last_ci_run.workflow == "release.yml"
    assert loaded.last_ci_run.url == "https://ci/1"
    committed_note = [n for n in loaded.notes if n.kind == "committed"][0]
    assert committed_note.text == "commit abc"
    assert committed_note.data == {"sha": "abc"}
    assert committed_note.source == "user"
    assert committed_note.category == "git"
    assert loaded.acceptance_criteria == ["Tests green", "Docs updated", "Reviewer approved"]
    assert loaded.priority == "high"
    assert loaded.blocked_by == ["dep-1", "dep-2"]
    assert loaded.blocked_reason == ""
    assert loaded.created_at == t.created_at


def test_store_defaults_for_missing_workflow_json(store, tmp_path):
    """Opening a pre-v2 row (workflow_json NULL) falls back cleanly."""
    import sqlite3

    t = Task.new(str(tmp_path), TaskKind.CHORE, "legacy")
    store.save(t)
    # Simulate an old row that was written before the v2 migration
    # by nulling out the workflow column directly.
    with sqlite3.connect(store._path) as c:
        c.execute("UPDATE tasks SET workflow_json = NULL WHERE id = ?", (t.id,))
        c.commit()

    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.acceptance_criteria == []
    assert loaded.blocked_reason == ""
    assert loaded.priority == ""
    assert loaded.blocked_by == []


def test_store_old_notes_without_source_and_category(store, tmp_path):
    """An older row with a note missing the new fields should load."""
    import sqlite3

    t = Task.new(str(tmp_path), TaskKind.BUGFIX, "legacy notes")
    store.save(t)
    # Overwrite notes_json with a v1-style payload lacking source/category.
    legacy_note = '[{"timestamp": 1.0, "kind": "committed", "text": "old", "data": {}}]'
    with sqlite3.connect(store._path) as c:
        c.execute("UPDATE tasks SET notes_json = ? WHERE id = ?", (legacy_note, t.id))
        c.commit()

    loaded = store.get(t.id)
    assert loaded is not None
    assert len(loaded.notes) == 1
    assert loaded.notes[0].text == "old"
    assert loaded.notes[0].source == "system"  # defaulted
    assert loaded.notes[0].category == ""  # defaulted


def test_store_list_filters_and_excludes_archived(store, tmp_path):
    p = str(tmp_path)
    t1 = Task.new(p, TaskKind.FEATURE, "t1")
    t1.state = TaskState.ACTIVE
    t2 = Task.new(p, TaskKind.FEATURE, "t2")
    t2.state = TaskState.DONE
    t3 = Task.new(p, TaskKind.FEATURE, "t3")
    t3.state = TaskState.ARCHIVED
    for t in (t1, t2, t3):
        store.save(t)

    listed = store.list_tasks(p)
    assert {t.title for t in listed} == {"t1", "t2"}  # archived excluded by default

    listed_all = store.list_tasks(p, include_archived=True)
    assert {t.title for t in listed_all} == {"t1", "t2", "t3"}

    listed_active = store.list_tasks(p, state_filter=[TaskState.ACTIVE])
    assert [t.title for t in listed_active] == ["t1"]


def test_store_list_scoped_to_project(store, tmp_path):
    a = str(tmp_path / "a")
    b = str(tmp_path / "b")
    store.save(Task.new(a, TaskKind.FEATURE, "in-a"))
    store.save(Task.new(b, TaskKind.FEATURE, "in-b"))
    assert [t.title for t in store.list_tasks(a)] == ["in-a"]
    assert [t.title for t in store.list_tasks(b)] == ["in-b"]


def test_store_delete(store, tmp_path):
    t = Task.new(str(tmp_path), TaskKind.CHORE, "x")
    store.save(t)
    assert store.get(t.id) is not None
    store.delete(t.id)
    assert store.get(t.id) is None


def test_store_corrupt_json_falls_back(store, tmp_path):
    """A corrupt JSON column should not crash the loader."""
    t = Task.new(str(tmp_path), TaskKind.FEATURE, "x")
    store.save(t)
    import sqlite3

    with sqlite3.connect(store._path) as c:
        c.execute("UPDATE tasks SET notes_json = ? WHERE id = ?", ("not-json", t.id))
        c.commit()
    loaded = store.get(t.id)
    assert loaded is not None and loaded.notes == []


# ── Manager ─────────────────────────────────────────────────────────


def test_manager_create_sets_active_and_emits(manager):
    bus = manager._bus
    seen: list[str] = []
    bus.subscribe(EVT_TASK_CHANGED, lambda **_: seen.append("changed"))
    bus.subscribe(EVT_TASK_LIST_CHANGED, lambda **_: seen.append("list"))

    t = manager.create_task(TaskKind.FEATURE, "hello")
    assert t is not None
    assert manager.active is t
    assert "changed" in seen and "list" in seen


def test_manager_rejects_empty_title(manager):
    assert manager.create_task(TaskKind.FEATURE, "  ") is None
    assert manager.active is None


def test_manager_requires_project_root(store):
    m = TaskManager(store=store)
    assert m.create_task(TaskKind.FEATURE, "x") is None


def test_manager_update_state_emits_and_persists(manager):
    bus = manager._bus
    events: list[tuple] = []
    bus.subscribe(
        EVT_TASK_STATE_CHANGED,
        lambda old_state=None, new_state=None, **_: events.append((old_state, new_state)),
    )
    t = manager.create_task(TaskKind.FEATURE, "x")
    manager.update_state(TaskState.ACTIVE)
    assert events == [(TaskState.PLANNING, TaskState.ACTIVE)]
    # noop when state unchanged
    manager.update_state(TaskState.ACTIVE)
    assert len(events) == 1
    # persisted state-change note
    reloaded = manager._store.get(t.id)
    assert reloaded.state == TaskState.ACTIVE
    assert any(n.kind == "state_changed" for n in reloaded.notes)


def test_manager_update_active_whitelists_fields(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    manager.update_active(branch="feat/x", evil="payload")
    assert manager.active.branch == "feat/x"
    assert not hasattr(manager.active, "evil")


def test_manager_add_note_persists_and_emits(manager):
    bus = manager._bus
    notes: list[TaskNote] = []
    bus.subscribe(EVT_TASK_NOTE_ADDED, lambda note=None, **_: notes.append(note))
    manager.create_task(TaskKind.FEATURE, "x")
    manager.add_note("committed", "abc123", data={"sha": "abc"})
    assert notes and notes[0].kind == "committed"
    assert any(n.kind == "committed" for n in manager.active.notes)


def test_manager_archive_clears_active_and_hides_from_list(manager):
    t = manager.create_task(TaskKind.FEATURE, "x")
    manager.archive(t.id)
    assert manager.active is None
    assert manager.list_tasks() == []


def test_manager_delete_clears_active(manager):
    t = manager.create_task(TaskKind.FEATURE, "x")
    manager.delete(t.id)
    assert manager.active is None
    assert manager._store.get(t.id) is None


def test_manager_set_project_root_auto_activates_recent(tmp_path, store):
    bus = EventBus()
    m1 = TaskManager(store=store, event_bus=bus)
    m1.set_project_root(tmp_path)
    t = m1.create_task(TaskKind.FEATURE, "auto")
    m1.update_state(TaskState.ACTIVE)

    # Fresh manager pointed at the same project should pick it up.
    m2 = TaskManager(store=store, event_bus=EventBus())
    m2.set_project_root(tmp_path)
    assert m2.active is not None and m2.active.id == t.id


def test_manager_set_active_unknown_id_is_noop(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    before = manager.active
    manager.set_active("does-not-exist")
    assert manager.active is before


def test_manager_listener_exception_does_not_propagate(manager):
    """A broken subscriber must not break the emit pipeline."""

    def bad(**_):
        raise RuntimeError("boom")

    manager._bus.subscribe(EVT_TASK_CHANGED, bad)
    # Should not raise.
    manager.create_task(TaskKind.FEATURE, "x")


# ── Store signature + upsert ────────────────────────────────────────


def test_store_save_returns_bool(store, tmp_path):
    task = Task.new(str(tmp_path), TaskKind.FEATURE, "x")
    assert store.save(task) is True


def test_store_save_upsert_preserves_created_at(store, tmp_path):
    """Saving the same id twice updates the row in place and keeps created_at."""
    task = Task.new(str(tmp_path), TaskKind.FEATURE, "first")
    original_created = task.created_at
    store.save(task)

    task.title = "second"
    task.updated_at = task.created_at + 100
    store.save(task)

    loaded = store.get(task.id)
    assert loaded.title == "second"
    assert loaded.created_at == original_created
    assert loaded.updated_at == task.created_at + 100


def test_store_list_orders_by_updated_at_desc(store, tmp_path):
    p = str(tmp_path)
    a = Task.new(p, TaskKind.FEATURE, "a")
    b = Task.new(p, TaskKind.FEATURE, "b")
    c = Task.new(p, TaskKind.FEATURE, "c")
    a.updated_at, b.updated_at, c.updated_at = 1000.0, 3000.0, 2000.0
    for t in (a, b, c):
        store.save(t)
    titles = [t.title for t in store.list_tasks(p)]
    assert titles == ["b", "c", "a"]


@pytest.mark.parametrize(
    "column",
    ["plan_json", "notes_json", "modified_files_json", "last_test_run_json", "last_ci_run_json"],
)
def test_store_corrupt_json_every_column_falls_back(store, tmp_path, column):
    """A garbled JSON blob in any column must not crash list_tasks."""
    import sqlite3

    task = Task.new(str(tmp_path), TaskKind.FEATURE, "x")
    task.plan = [PlanStep(text="s")]
    task.last_test_run = TestRunSnapshot(passed=1)
    task.last_ci_run = CIRunSnapshot(status="success")
    task.modified_files = ["a.py"]
    store.save(task)

    with sqlite3.connect(store._path) as c:
        c.execute(f"UPDATE tasks SET {column} = ? WHERE id = ?", ("not-json", task.id))
        c.commit()

    # list_tasks should still work and the task should still load.
    listed = store.list_tasks(str(tmp_path))
    assert len(listed) == 1


def test_store_row_to_task_unknown_enum_does_not_crash_list(store, tmp_path):
    """A row with an unknown TaskKind/TaskState value is skipped, not fatal."""
    import sqlite3

    good = Task.new(str(tmp_path), TaskKind.FEATURE, "good")
    store.save(good)

    # Insert a second row with an invalid state value directly.
    bad_task = Task.new(str(tmp_path), TaskKind.FEATURE, "bad")
    store.save(bad_task)
    with sqlite3.connect(store._path) as c:
        c.execute("UPDATE tasks SET state = ? WHERE id = ?", ("not_a_state", bad_task.id))
        c.commit()

    listed = store.list_tasks(str(tmp_path))
    # Good row survives; bad row is skipped, not a crash.
    titles = [t.title for t in listed]
    assert "good" in titles
    assert "bad" not in titles


# ── Manager rollback-on-failure paths ───────────────────────────────


class _FailingStore:
    """Store wrapper that forces the next N ``save()`` calls to fail."""

    def __init__(self, real_store, fail_saves: int = 1) -> None:
        self._real = real_store
        self._fail_saves = fail_saves

    def save(self, task):
        if self._fail_saves > 0:
            self._fail_saves -= 1
            return False
        return self._real.save(task)

    def delete(self, task_id):
        return self._real.delete(task_id)

    def get(self, task_id):
        return self._real.get(task_id)

    def list_tasks(self, *a, **kw):
        return self._real.list_tasks(*a, **kw)


def _subscribe_write_failures(bus):
    events: list[tuple] = []
    bus.subscribe(
        EVT_TASK_WRITE_FAILED,
        lambda operation=None, task_id=None, **_: events.append((operation, task_id)),
    )
    return events


def test_manager_create_failure_does_not_set_active(tmp_path, store):
    bus = EventBus()
    failing = _FailingStore(store, fail_saves=1)
    m = TaskManager(store=failing, event_bus=bus)
    m.set_project_root(tmp_path)
    events = _subscribe_write_failures(bus)

    t = m.create_task(TaskKind.FEATURE, "x")
    assert t is None
    assert m.active is None
    assert len(events) == 1
    assert events[0][0] == "create"


def test_manager_update_state_rolls_back_on_failure(tmp_path, store):
    bus = EventBus()
    m = TaskManager(store=store, event_bus=bus)
    m.set_project_root(tmp_path)
    task = m.create_task(TaskKind.FEATURE, "x")
    assert task.state == TaskState.PLANNING
    notes_before = len(task.notes)

    # Now swap in a failing store and try to transition state.
    m._store = _FailingStore(store, fail_saves=1)
    events = _subscribe_write_failures(bus)
    m.update_state(TaskState.ACTIVE)

    assert m.active.state == TaskState.PLANNING, "state should have rolled back"
    assert len(m.active.notes) == notes_before, "state_changed note should have rolled back"
    assert events and events[0][0] == "state_change"


def test_manager_update_active_rolls_back_all_fields_on_failure(tmp_path, store):
    bus = EventBus()
    m = TaskManager(store=store, event_bus=bus)
    m.set_project_root(tmp_path)
    task = m.create_task(TaskKind.FEATURE, "x")
    original_branch = task.branch
    original_updated = task.updated_at

    m._store = _FailingStore(store, fail_saves=1)
    events = _subscribe_write_failures(bus)
    m.update_active(branch="feat/new", pr_number=42)

    assert m.active.branch == original_branch
    assert m.active.pr_number is None
    assert m.active.updated_at == original_updated
    assert events and events[0][0] == "update"


def test_manager_add_note_rolls_back_on_failure(tmp_path, store):
    bus = EventBus()
    m = TaskManager(store=store, event_bus=bus)
    m.set_project_root(tmp_path)
    task = m.create_task(TaskKind.FEATURE, "x")
    notes_before = len(task.notes)
    original_updated = task.updated_at

    m._store = _FailingStore(store, fail_saves=1)
    events = _subscribe_write_failures(bus)
    m.add_note("committed", "abc")

    assert len(m.active.notes) == notes_before
    assert m.active.updated_at == original_updated
    assert events and events[0][0] == "add_note"


# ── Timestamp correctness ───────────────────────────────────────────


def test_manager_add_note_stamps_with_new_updated_at(manager):
    """Regression: earlier code stamped notes with the PREVIOUS updated_at."""
    import time

    manager.create_task(TaskKind.FEATURE, "x")
    before = manager.active.updated_at
    time.sleep(0.01)
    manager.add_note("committed", "sha")

    note = manager.active.notes[-1]
    assert note.kind == "committed"
    assert note.timestamp > before
    assert note.timestamp == manager.active.updated_at


def test_manager_update_state_note_timestamp_matches_updated_at(manager):
    import time

    manager.create_task(TaskKind.FEATURE, "x")
    time.sleep(0.01)
    manager.update_state(TaskState.ACTIVE)

    state_notes = [n for n in manager.active.notes if n.kind == "state_changed"]
    assert state_notes
    assert state_notes[-1].timestamp == manager.active.updated_at


# ── block_task + source tagging ─────────────────────────────────────


def test_manager_block_task_requires_non_empty_reason(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    assert manager.block_task("") is False
    assert manager.block_task("   ") is False
    assert manager.active.state == TaskState.PLANNING
    assert manager.active.blocked_reason == ""


def test_manager_block_task_no_active(tmp_path, store):
    m = TaskManager(store=store, event_bus=EventBus())
    m.set_project_root(tmp_path)
    assert m.block_task("anything") is False


def test_manager_block_task_sets_state_reason_and_note(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    assert manager.block_task("waiting on reviewer") is True
    assert manager.active.state == TaskState.BLOCKED
    assert manager.active.blocked_reason == "waiting on reviewer"
    blocked_notes = [n for n in manager.active.notes if n.kind == "blocked"]
    assert blocked_notes
    assert "waiting on reviewer" in blocked_notes[-1].text
    assert blocked_notes[-1].source == "user"
    assert blocked_notes[-1].category == "workflow"


def test_manager_unblock_via_update_state_clears_reason(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    manager.block_task("parked for now")
    manager.update_state(TaskState.ACTIVE)
    assert manager.active.state == TaskState.ACTIVE
    assert manager.active.blocked_reason == ""


def test_manager_update_state_source_reaches_note(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    manager.update_state(TaskState.ACTIVE, source="automation", reason="branch created")
    state_notes = [n for n in manager.active.notes if n.kind == "state_changed"]
    assert state_notes[-1].source == "automation"
    assert state_notes[-1].category == "workflow"
    assert "branch created" in state_notes[-1].text


def test_manager_add_note_source_and_category(manager):
    manager.create_task(TaskKind.FEATURE, "x")
    manager.add_note("committed", "abc", source="user", category="git")
    recent = manager.active.notes[-1]
    assert recent.source == "user"
    assert recent.category == "git"


def test_manager_block_task_rollback_on_save_failure(tmp_path, store):
    bus = EventBus()
    m = TaskManager(store=store, event_bus=bus)
    m.set_project_root(tmp_path)
    m.create_task(TaskKind.FEATURE, "x")
    m._store = _FailingStore(store, fail_saves=1)
    events = _subscribe_write_failures(bus)

    assert m.block_task("nope") is False
    assert m.active.state == TaskState.PLANNING
    assert m.active.blocked_reason == ""
    assert events and events[0][0] == "block"
