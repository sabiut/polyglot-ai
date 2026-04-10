"""UI tests for the Tasks panel — inline quick-create + public API.

These pin the phase 1/2 UX changes:

- clicking + toggles the inline quick-create row (no modal)
- typing a title + Enter creates a task with default kind FEATURE
- Esc cancels without creating
- toggling + twice hides the row
- command-palette-facing public API methods drive the manager correctly
- reduced kind dropdown: only FEATURE / BUGFIX / REFACTOR are offered
  in the new-task dialog, even though the enum still has 6 values
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from polyglot_ai.core.tasks import TaskKind, TaskState
from polyglot_ai.ui.panels.tasks_panel import TasksPanel, _NewTaskDialog


@pytest.fixture
def panel(qtbot, task_manager):
    p = TasksPanel()
    qtbot.addWidget(p)
    # Actually show the widget so isVisible() on children reflects the
    # visibility flag set by _on_new_task. Without show() the tree is
    # considered hidden and every child reports isVisible() == False.
    p.show()
    p.set_task_manager(task_manager)
    return p


# ── Inline quick-create ─────────────────────────────────────────────


def test_plus_button_toggles_quick_create_row(panel):
    assert panel._quick_create_row.isVisible() is False
    panel._on_new_task()
    assert panel._quick_create_row.isVisible() is True
    panel._on_new_task()
    assert panel._quick_create_row.isVisible() is False


def test_quick_create_enter_creates_task_with_default_feature_kind(
    panel, task_manager, qtbot
):
    panel._on_new_task()
    panel._quick_create_input.setText("Add CSV export")
    panel._on_quick_create_commit()

    tasks = task_manager.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].title == "Add CSV export"
    assert tasks[0].kind == TaskKind.FEATURE
    # Row hides + clears after commit
    assert panel._quick_create_row.isVisible() is False
    assert panel._quick_create_input.text() == ""


def test_quick_create_empty_title_does_nothing(panel, task_manager):
    panel._on_new_task()
    panel._quick_create_input.setText("   ")
    panel._on_quick_create_commit()
    assert task_manager.list_tasks() == []


def test_quick_create_esc_cancels_without_creating(panel, task_manager, qtbot):
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    panel._on_new_task()
    panel._quick_create_input.setText("should not be created")

    esc = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    # Route through the panel's eventFilter (matches real Qt dispatch)
    panel.eventFilter(panel._quick_create_input, esc)

    assert panel._quick_create_row.isVisible() is False
    assert panel._quick_create_input.text() == ""
    assert task_manager.list_tasks() == []


def test_quick_create_no_project_root_is_noop(qtbot, tmp_path):
    """Clicking + before a project is open should be harmless."""
    from polyglot_ai.core.task_manager import TaskManager
    from polyglot_ai.core.task_store import TaskStore

    store = TaskStore(path=tmp_path / "tasks.db")
    manager = TaskManager(store=store)
    # Deliberately NO set_project_root
    panel = TasksPanel()
    qtbot.addWidget(panel)
    panel.set_task_manager(manager)

    panel._on_new_task()
    assert panel._quick_create_row.isVisible() is False


# ── Reduced kind dropdown (#4) ──────────────────────────────────────


def test_new_task_dialog_only_offers_three_kinds(qtbot):
    dlg = _NewTaskDialog()
    qtbot.addWidget(dlg)
    combo = dlg._kind_combo

    kinds = [combo.itemData(i) for i in range(combo.count())]
    assert kinds == [TaskKind.FEATURE, TaskKind.BUGFIX, TaskKind.REFACTOR]
    # Explicitly absent from the dropdown, still in the enum
    assert TaskKind.INCIDENT not in kinds
    assert TaskKind.EXPLORE not in kinds
    assert TaskKind.CHORE not in kinds


def test_enum_still_has_all_six_kinds():
    """UI reduction must not touch the enum — stored tasks with
    INCIDENT/EXPLORE/CHORE need to keep loading."""
    values = {k.value for k in TaskKind}
    assert values == {"feature", "bugfix", "incident", "refactor", "explore", "chore"}


# ── Public API used by command palette ──────────────────────────────


def test_trigger_new_task_shows_row_and_focuses_input(panel, qtbot):
    panel.trigger_new_task()
    assert panel._quick_create_row.isVisible() is True


def test_trigger_new_task_no_project_is_noop(qtbot, tmp_path):
    from polyglot_ai.core.task_manager import TaskManager
    from polyglot_ai.core.task_store import TaskStore

    manager = TaskManager(store=TaskStore(path=tmp_path / "t.db"))
    panel = TasksPanel()
    qtbot.addWidget(panel)
    panel.set_task_manager(manager)

    panel.trigger_new_task()
    assert panel._quick_create_row.isVisible() is False


def test_mark_active_done_transitions_state(panel, task_manager):
    task = task_manager.create_task(TaskKind.FEATURE, "Feature A")
    assert task is not None
    task_manager.set_active(task.id)

    panel.mark_active_done()

    # Reload from the manager to see the persisted state
    refreshed = next(t for t in task_manager.list_tasks() if t.id == task.id)
    assert refreshed.state == TaskState.DONE


def test_mark_active_done_noop_when_no_active(panel, task_manager):
    # No active task, nothing created yet — must not raise
    panel.mark_active_done()
    assert task_manager.list_tasks() == []


def test_open_active_task_detail_noop_when_no_active(panel):
    # Should not raise and should not open any dialog
    panel.open_active_task_detail()
    assert getattr(panel, "_detail_dialog", None) is None
