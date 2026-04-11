"""UI tests for the TaskDetailDialog — rename + empty state + non-modal.

Pins the phase 1 changes to the detail dialog:

- non-modal (setModal(False)) so the user can keep it open alongside
  other panels
- ⛶ maximize button toggles between normal and maximized
- "CHECKLIST" replaces "PLAN" as the section label
- when a task has no checklist AND a plan_generator is wired, the
  empty-state primary-action card is rendered
- without a plan_generator there's no generate card and no bottom
  regenerate button
- with a plan, the bottom "Regenerate checklist" button appears
  instead of the empty-state card
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel

from polyglot_ai.core.tasks import PlanStep, TaskKind
from polyglot_ai.ui.dialogs.task_detail_dialog import TaskDetailDialog


class _FakePlanGenerator:
    """Stand-in for the real PlanGenerator; never actually called."""

    async def generate(self, task):  # pragma: no cover — behaviour unused
        raise NotImplementedError


def _labels_text(widget) -> list[str]:
    """Return all QLabel text strings found anywhere under ``widget``."""
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


@pytest.fixture
def task_with_no_plan(task_manager):
    t = task_manager.create_task(TaskKind.FEATURE, "Add CSV export")
    assert t is not None
    return t


# ── Non-modal + window flags ────────────────────────────────────────


def test_dialog_is_non_modal(qtbot, task_with_no_plan, task_manager):
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)
    assert dlg.isModal() is False


def test_maximize_button_exists(qtbot, task_with_no_plan, task_manager):
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)
    assert dlg._max_btn is not None
    assert dlg._max_btn.text() == "⛶"


def test_toggle_maximize_flips_window_state(qtbot, task_with_no_plan, task_manager):
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.isMaximized() is False
    dlg._toggle_maximize()
    # Offscreen platform may not honour showMaximized the same way a
    # real WM does, so check the state via windowState rather than
    # pixels — flipping Qt.WindowMaximized is enough for our wiring.
    from PyQt6.QtCore import Qt

    assert bool(dlg.windowState() & Qt.WindowState.WindowMaximized) is True
    dlg._toggle_maximize()
    assert bool(dlg.windowState() & Qt.WindowState.WindowMaximized) is False


# ── Empty-state primary action (#6/#7) ──────────────────────────────


def test_no_plan_no_generator_shows_no_empty_card(qtbot, task_with_no_plan, task_manager):
    """Without a plan_generator the empty-state card should NOT render.
    Users without a configured AI provider wouldn't get anything to
    click, so we hide the affordance entirely."""
    assert task_manager.plan_generator is None
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)
    texts = _labels_text(dlg)
    # Neither the empty-state blurb nor the card header should appear
    assert not any("No checklist yet" in t for t in texts)


def test_no_plan_with_generator_shows_prominent_empty_card(qtbot, task_with_no_plan, task_manager):
    task_manager.set_plan_generator(_FakePlanGenerator())
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)

    texts = _labels_text(dlg)
    # The card's header is "CHECKLIST" (reused by both empty and
    # populated cards). The blurb + primary button identify empty state.
    assert any("CHECKLIST" in t for t in texts)
    assert any("No checklist yet" in t for t in texts)
    # _plan_btn is bound by _build_empty_checklist_card
    assert dlg._plan_btn is not None
    assert "Generate checklist" in dlg._plan_btn.text()


def test_with_plan_shows_populated_card_and_regenerate_button(qtbot, task_manager):
    task = task_manager.create_task(TaskKind.FEATURE, "Wire up auth")
    task_manager.set_active(task.id)
    task_manager.set_plan(
        [
            PlanStep(text="Add login route"),
            PlanStep(text="Add logout route"),
        ]
    )
    task_manager.set_plan_generator(_FakePlanGenerator())
    fresh = next(t for t in task_manager.list_tasks() if t.id == task.id)

    dlg = TaskDetailDialog(fresh, task_manager)
    qtbot.addWidget(dlg)

    texts = _labels_text(dlg)
    # Populated plan card header
    assert any(t == "CHECKLIST" for t in texts)
    # Steps rendered
    assert any("Add login route" in t for t in texts)
    assert any("Add logout route" in t for t in texts)
    # No "empty" blurb
    assert not any("No checklist yet" in t for t in texts)
    # Regenerate button in the action row
    assert dlg._plan_btn is not None
    assert dlg._plan_btn.text() == "Regenerate checklist"


# ── Rename audit: no "PLAN" leaking into UI text ────────────────────


def test_no_plan_leaks_into_dialog_labels(qtbot, task_with_no_plan, task_manager):
    task_manager.set_plan_generator(_FakePlanGenerator())
    dlg = TaskDetailDialog(task_with_no_plan, task_manager)
    qtbot.addWidget(dlg)

    texts = _labels_text(dlg)
    joined = " ".join(texts)
    # Lowercased spot checks for the old vocabulary. We allow
    # "Checklist" to coexist freely; we just forbid the literal
    # "Generate plan" / "Regenerate plan" / "PLAN" header.
    assert "Generate plan" not in joined
    assert "Regenerate plan" not in joined
    # The header should read "CHECKLIST", not "PLAN"
    header_labels = [t for t in texts if t == "PLAN"]
    assert header_labels == []
