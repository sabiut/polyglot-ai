"""Tasks sidebar panel — the new first-class organising unit.

Lists active / planning / review / done tasks for the current
project, grouped by state. Click a task to make it active. The new
task button opens a styled dialog where the user picks a kind and
types a title.

This is the foundation panel for the workflow concept. The chat, git,
tests, review, and CI panels subscribe to the TaskManager's
``task:changed`` event and re-scope themselves to whatever task is
active.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.task_manager import (
    EVT_TASK_CHANGED,
    EVT_TASK_LIST_CHANGED,
    TaskManager,
)
from polyglot_ai.core.tasks import Task, TaskKind, TaskState
from polyglot_ai.ui import theme
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels import shared_icons
from polyglot_ai.ui.widgets.icon_button import make_icon_button

logger = logging.getLogger(__name__)


# Display order in the sidebar — most actionable states at the top.
_GROUP_ORDER: list[tuple[str, list[TaskState]]] = [
    ("ACTIVE", [TaskState.ACTIVE]),
    ("PLANNING", [TaskState.PLANNING]),
    ("REVIEW", [TaskState.REVIEW]),
    ("BLOCKED", [TaskState.BLOCKED]),
    ("DONE (recent)", [TaskState.DONE]),
]


_KIND_COLOURS: dict[TaskKind, str] = {
    TaskKind.FEATURE: "#4ec9b0",
    TaskKind.BUGFIX: "#f48771",
    TaskKind.INCIDENT: "#f44747",
    TaskKind.REFACTOR: "#9cdcfe",
    TaskKind.EXPLORE: "#e5a00d",
    TaskKind.CHORE: "#888888",
}


class TasksPanel(QWidget):
    """Sidebar panel listing tasks for the current project."""

    # Re-emit on the GUI thread to keep refresh fast.
    _refresh_requested = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        standalone: bool = False,
    ) -> None:
        """Create the Tasks panel.

        ``standalone=True`` is passed by ``_on_expand`` when creating
        the "open in a separate window" copy. In that mode the expand
        button is hidden (no point — we're already expanded).
        """
        super().__init__(parent)
        self._task_manager: TaskManager | None = None
        self._event_bus = None
        self._standalone = standalone
        self._standalone_window: QWidget | None = None

        self._refresh_requested.connect(self._do_refresh)

        self._setup_ui()
        theme.connect_theme_changed(self._apply_theme_styles)

    # ── Wiring ──────────────────────────────────────────────────────

    def set_task_manager(self, manager: TaskManager) -> None:
        self._task_manager = manager
        self._refresh_requested.emit()

    def set_event_bus(self, event_bus) -> None:
        """Subscribe to task lifecycle events so the panel auto-refreshes."""
        self._event_bus = event_bus
        event_bus.subscribe(EVT_TASK_LIST_CHANGED, lambda **_: self._refresh_requested.emit())
        event_bus.subscribe(EVT_TASK_CHANGED, lambda **_: self._refresh_requested.emit())

    # ── Public API (command palette) ────────────────────────────────
    #
    # These entry points let the command palette drive the Tasks
    # panel without having to know its internals. Keeping them thin
    # and tolerant of "project not open yet" means palette actions
    # never crash; at worst they're no-ops.

    def trigger_new_task(self) -> None:
        """Open the inline quick-create row and focus the title input."""
        if self._task_manager is None or self._task_manager.project_root is None:
            return
        if not self._quick_create_row.isVisible():
            self._quick_create_row.setVisible(True)
        self._quick_create_input.setFocus()
        self._quick_create_input.selectAll()

    def trigger_new_task_dialog(self) -> None:
        """Open the full _NewTaskDialog (kind + description) directly."""
        if self._task_manager is None or self._task_manager.project_root is None:
            return
        dlg = _NewTaskDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        kind, title, description = dlg.get_values()
        if not title:
            return
        self._task_manager.create_task(kind, title, description)

    def open_active_task_detail(self) -> None:
        """Open the detail dialog for the currently active task, if any."""
        if self._task_manager is None or self._task_manager.active is None:
            return
        self._on_open_detail(self._task_manager.active.id)

    def mark_active_done(self) -> None:
        """Transition the active task to DONE."""
        if self._task_manager is None or self._task_manager.active is None:
            return
        try:
            self._task_manager.update_state(TaskState.DONE)
        except Exception:
            logger.exception("tasks_panel: could not mark active task done")
            try:
                from PyQt6.QtWidgets import QMessageBox

                QMessageBox.warning(self, "Error", "Could not mark the active task as done.")
            except Exception:
                pass

    def block_active_task(self) -> None:
        """Prompt for a blocker reason and mark the active task BLOCKED."""
        if self._task_manager is None or self._task_manager.active is None:
            return
        self._prompt_block(self._task_manager.active)

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        self._header = header
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 0, 6, 0)
        h.setSpacing(2)

        title = QLabel("TASKS")
        self._title_label = title
        h.addWidget(title)

        self._summary_label = QLabel("")
        h.addWidget(self._summary_label)
        h.addStretch()

        new_btn = make_icon_button(shared_icons.draw_plus_icon(), "New task")
        new_btn.clicked.connect(self._on_new_task)
        h.addWidget(new_btn)

        refresh_btn = make_icon_button(shared_icons.draw_refresh_icon(), "Refresh task list")
        refresh_btn.clicked.connect(lambda: self._refresh_requested.emit())
        h.addWidget(refresh_btn)

        if not self._standalone:
            expand_btn = make_icon_button(
                shared_icons.draw_popout_icon(), "Open in a separate window"
            )
            expand_btn.clicked.connect(self._on_expand)
            h.addWidget(expand_btn)

        layout.addWidget(header)

        # Inline quick-create row. Hidden by default; toggled by the
        # + header button. Enter creates a task with default kind
        # FEATURE; Esc cancels. A small "more…" button opens the full
        # _NewTaskDialog for users who want to set kind/description.
        # Replacing the modal-only flow here was the biggest UX friction
        # point — most tasks are created with just a title.
        self._quick_create_row = QWidget()
        self._quick_create_row.setVisible(False)
        qc_layout = QHBoxLayout(self._quick_create_row)
        qc_layout.setContentsMargins(12, 8, 6, 8)
        qc_layout.setSpacing(6)

        self._quick_create_input = QLineEdit()
        self._quick_create_input.setPlaceholderText("Task title — Enter to create, Esc to cancel")
        self._quick_create_input.returnPressed.connect(self._on_quick_create_commit)
        # Catch Esc at the key-press level so we can close the row
        # without triggering the QLineEdit's default clear-selection.
        self._quick_create_input.installEventFilter(self)
        qc_layout.addWidget(self._quick_create_input, stretch=1)

        more_btn = QPushButton("⋯")
        more_btn.setFixedSize(22, 22)
        more_btn.setToolTip("More options (kind, description)")
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn = more_btn
        more_btn.clicked.connect(self._on_quick_create_more)
        qc_layout.addWidget(more_btn)

        layout.addWidget(self._quick_create_row)

        # Scrollable list of grouped task cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._scroll = scroll
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 6, 8, 8)
        self._content_layout.setSpacing(4)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        layout.addWidget(scroll, stretch=1)

        # Empty state
        self._empty = QLabel(
            "No tasks yet.\n\nClick the + button above to create one.\n"
            "A task ties together your branch, commits, tests,\n"
            "and chat conversation in one place."
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.hide()
        layout.addWidget(self._empty)

        self._apply_theme_styles()

    def _apply_theme_styles(self) -> None:
        self._header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        self._title_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            f"letter-spacing: 0.5px; background: transparent;"
        )
        self._summary_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_success_muted')}; "
            f"background: transparent; margin-left: 6px;"
        )
        self._quick_create_row.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        self._quick_create_input.setStyleSheet(
            f"QLineEdit {{ background: {tc.get('bg_base')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_primary')}; border-radius: 4px; "
            f"padding: 5px 8px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._more_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {tc.get('text_tertiary')}; "
            f"border: 1px solid {tc.get('border_primary')}; border-radius: 3px; "
            f"font-size: {tc.FONT_LG}px; }}"
            f"QPushButton:hover {{ color: {tc.get('text_primary')}; "
            f"border-color: {tc.get('border_input')}; }}"
        )
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}"
            f"QScrollBar:vertical {{ width: 8px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ background: {tc.get('scrollbar_thumb')}; "
            f"border-radius: 4px; }}"
        )
        self._content.setStyleSheet(f"background: {tc.get('bg_base')};")
        self._empty.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_MD}px; "
            f"padding: 24px; background: {tc.get('bg_base')};"
        )
        if self._task_manager is not None:
            self._refresh_requested.emit()

    def _on_expand(self) -> None:
        """Open the Tasks view in a standalone, larger window.

        Creates a fresh ``TasksPanel`` bound to the same task manager
        and event bus so both views stay in sync (they re-render on
        the same ``task:changed`` / ``task:list_changed`` events).
        """
        # Keep a reference on ``self`` so the window isn't GC'd the
        # instant this method returns. Re-opening raises the existing
        # window instead of creating a second one. Probe the C++
        # object via ``isVisible()`` so we don't no-op on a closed
        # window whose ``destroyed`` signal hasn't fired yet.
        existing = getattr(self, "_standalone_window", None)
        if existing is not None:
            alive = False
            try:
                alive = existing.isVisible()
            except RuntimeError:
                alive = False
            except Exception:
                logger.debug("tasks_panel: stale standalone window", exc_info=True)
                alive = False
            if alive:
                try:
                    existing.raise_()
                    existing.activateWindow()
                    return
                except Exception:
                    logger.debug("tasks_panel: could not raise standalone window", exc_info=True)
            self._standalone_window = None
        from polyglot_ai.ui.panels.tasks_panel import TasksPanel

        win = QWidget(self.window(), Qt.WindowType.Window)
        win.setWindowTitle("Tasks")
        win.resize(900, 700)
        win.setStyleSheet(f"QWidget {{ background: {tc.get('bg_base')}; }}")
        inner = TasksPanel(parent=win, standalone=True)
        if self._task_manager is not None:
            inner.set_task_manager(self._task_manager)
        if self._event_bus is not None:
            inner.set_event_bus(self._event_bus)
        wlayout = QVBoxLayout(win)
        wlayout.setContentsMargins(0, 0, 0, 0)
        wlayout.addWidget(inner)
        win.show()
        self._standalone_window = win
        # Clear the reference when the window closes so the user can
        # re-open it later.
        win.destroyed.connect(lambda _=None: setattr(self, "_standalone_window", None))

    # ── Refresh ─────────────────────────────────────────────────────

    def _do_refresh(self) -> None:
        """Rebuild the task list from the manager."""
        # Clear existing children
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if self._task_manager is None or self._task_manager.project_root is None:
            self._summary_label.setText("")
            self._empty.show()
            self._empty.setText("No project open.\n\nOpen a folder via File → Open Project.")
            return

        all_tasks = self._task_manager.list_tasks()
        if not all_tasks:
            self._summary_label.setText("")
            self._empty.show()
            self._empty.setText(
                "No tasks yet.\n\nClick the + button above to create one.\n"
                "A task ties together your branch, commits, tests,\n"
                "and chat conversation in one place."
            )
            return

        self._empty.hide()
        active_id = self._task_manager.active.id if self._task_manager.active else None

        # Group by display state.
        by_state: dict[TaskState, list[Task]] = {}
        for task in all_tasks:
            by_state.setdefault(task.state, []).append(task)

        total = 0
        for label, states in _GROUP_ORDER:
            tasks_in_group = [t for s in states for t in by_state.get(s, [])]
            if not tasks_in_group:
                continue
            self._content_layout.addWidget(self._make_group_header(label))
            for task in tasks_in_group:
                self._content_layout.addWidget(
                    self._make_task_card(task, is_active=task.id == active_id)
                )
                total += 1
        self._summary_label.setText(f"{total} task{'s' if total != 1 else ''}")

    def _make_group_header(self, label: str) -> QWidget:
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; font-weight: 600; "
            f"letter-spacing: 0.6px; background: transparent; "
            f"padding: 8px 4px 4px 4px;"
        )
        return lbl

    def _make_task_card(self, task: Task, is_active: bool) -> QWidget:
        card = _TaskCard(task, is_active)
        card.activate_requested.connect(self._on_activate)
        card.menu_requested.connect(self._on_card_menu)
        card.detail_requested.connect(self._on_open_detail)
        return card

    # ── Actions ─────────────────────────────────────────────────────

    def _on_activate(self, task_id: str) -> None:
        if self._task_manager is None:
            return
        self._task_manager.set_active(task_id)

    def _on_open_detail(self, task_id: str) -> None:
        """Open the full task detail dialog (also activates the task first)."""
        if self._task_manager is None:
            return
        self._task_manager.set_active(task_id)
        task = self._task_manager.active
        if task is None:
            return
        try:
            from polyglot_ai.ui.dialogs.task_detail_dialog import TaskDetailDialog

            # Non-modal: show() instead of exec() so the user can keep
            # the detail window open alongside the rest of the app
            # (chat, review, git). We stash it on ``self`` so Python
            # doesn't garbage-collect the QDialog the moment this
            # method returns. Any existing open dialog is replaced.
            existing = getattr(self, "_detail_dialog", None)
            if existing is not None:
                try:
                    existing.close()
                except RuntimeError:
                    pass  # Widget already destroyed by Qt
            dlg = TaskDetailDialog(task, self._task_manager, parent=self.window())
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.destroyed.connect(lambda _=None: setattr(self, "_detail_dialog", None))
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self._detail_dialog = dlg
        except Exception:
            logger.exception("tasks_panel: could not open task detail dialog")

    def _on_card_menu(self, task_id: str, global_pos) -> None:
        if self._task_manager is None:
            return
        task = next((t for t in self._task_manager.list_tasks() if t.id == task_id), None)
        if task is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_menu')}; }}"
            f"QMenu::item {{ padding: 5px 18px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )
        activate = menu.addAction("Make active")
        open_detail = menu.addAction("Open details…")
        menu.addSeparator()
        # State transitions
        for state in TaskState:
            if state == task.state or state == TaskState.ARCHIVED:
                continue
            action = menu.addAction(f"Move to: {state.value}")
            action.setData(("state", state))
        menu.addSeparator()
        archive = menu.addAction("Archive")
        delete = menu.addAction("Delete")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == activate:
            self._task_manager.set_active(task_id)
        elif chosen == open_detail:
            self._on_open_detail(task_id)
        elif chosen == archive:
            self._task_manager.archive(task_id)
        elif chosen == delete:
            self._task_manager.delete(task_id)
        elif chosen.data() and chosen.data()[0] == "state":
            target_state = chosen.data()[1]
            self._task_manager.set_active(task_id)
            # Moving to BLOCKED goes through the dedicated dialog so
            # the user is forced to supply a reason. Everything else
            # flows through the plain state update.
            if target_state == TaskState.BLOCKED:
                self._prompt_block(task)
            else:
                self._task_manager.update_state(target_state)

    def _prompt_block(self, task: Task) -> None:
        """Show the block-reason dialog and apply the result."""
        if self._task_manager is None:
            return
        from polyglot_ai.ui.dialogs.block_task_dialog import BlockTaskDialog

        dlg = BlockTaskDialog(task.title, current_reason=task.blocked_reason, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        reason = dlg.reason
        if not reason:
            return
        self._task_manager.block_task(reason)

    def _on_new_task(self) -> None:
        """Toggle the inline quick-create row.

        Clicking + once shows the row and focuses the title input.
        Clicking again (or pressing Esc) hides it. This replaces the
        previous modal-dialog-on-every-click flow, which was the
        biggest friction point for users creating multiple tasks
        per day.
        """
        if self._task_manager is None or self._task_manager.project_root is None:
            return
        if self._quick_create_row.isVisible():
            self._quick_create_row.setVisible(False)
            self._quick_create_input.clear()
            return
        self._quick_create_row.setVisible(True)
        self._quick_create_input.setFocus()

    def _on_quick_create_commit(self) -> None:
        """Create a task from the inline row. Default kind = FEATURE."""
        if self._task_manager is None:
            return
        title = self._quick_create_input.text().strip()
        if not title:
            return
        self._task_manager.create_task(TaskKind.FEATURE, title, "")
        self._quick_create_input.clear()
        self._quick_create_row.setVisible(False)

    def _on_quick_create_more(self) -> None:
        """Open the full _NewTaskDialog pre-filled with the typed title.

        Lets users who DO want to set a specific kind or description
        escalate from the inline row without losing what they've typed.
        """
        if self._task_manager is None or self._task_manager.project_root is None:
            return
        prefill = self._quick_create_input.text().strip()
        self._quick_create_row.setVisible(False)
        self._quick_create_input.clear()
        dlg = _NewTaskDialog(self)
        if prefill:
            dlg._title_edit.setText(prefill)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        kind, title, description = dlg.get_values()
        if not title:
            return
        self._task_manager.create_task(kind, title, description)

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Catch Esc on the quick-create input to hide the row."""
        from PyQt6.QtCore import QEvent

        if (
            obj is self._quick_create_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._quick_create_row.setVisible(False)
            self._quick_create_input.clear()
            return True
        return super().eventFilter(obj, event)


# ── Task card widget ────────────────────────────────────────────────


class _TaskCard(QWidget):
    """Single clickable card representing a task in the sidebar list."""

    activate_requested = pyqtSignal(str)
    menu_requested = pyqtSignal(str, object)  # task_id, global_pos
    detail_requested = pyqtSignal(str)  # task_id (double-click)

    def __init__(self, task: Task, is_active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task = task
        self._is_active = is_active
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        border = tc.get("accent_primary") if is_active else tc.get("bg_card")
        bg = tc.get("bg_active") if is_active else tc.get("bg_surface")
        self.setStyleSheet(
            f"_TaskCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        # Title row: kind dot + title
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        kind_dot = QLabel("●")
        kind_dot.setStyleSheet(
            f"color: {_KIND_COLOURS.get(task.kind, tc.get('text_tertiary'))}; "
            f"font-size: {tc.FONT_SM}px; "
            f"background: transparent;"
        )
        kind_dot.setToolTip(task.kind.value)
        title_row.addWidget(kind_dot)

        title_lbl = QLabel(task.title)
        title_lbl.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_MD}px; font-weight: {'600' if is_active else '500'}; "
            f"background: transparent;"
        )
        title_lbl.setWordWrap(True)
        title_row.addWidget(title_lbl, stretch=1)

        # Health badge: one small coloured chip at the end of the
        # title row. Uses the short ``badge`` label (never the full
        # reason) so a 200-char blocker message can't push the task
        # title off the card. The tooltip carries ``label + reason``
        # so hovering still shows the full context.
        from polyglot_ai.core.task_health import compute_health

        health = compute_health(task)
        health_badge = QLabel(health.badge)
        health_badge.setToolTip(
            f"{health.label}\n{health.reason}" if health.reason else health.label
        )
        health_badge.setStyleSheet(
            f"color: {health.colour}; font-size: {tc.FONT_XS}px; font-weight: 600; "
            f"background: rgba(0,0,0,0.35); border: 1px solid {health.colour}; "
            f"border-radius: 3px; padding: 1px 6px;"
        )
        # Cap the chip width and disable word-wrap so even a future
        # long badge label is structurally incapable of overlapping
        # the task title. ``setMaximumWidth`` on the QLabel combined
        # with ``stretch=0`` on ``addWidget`` keeps the chip compact.
        health_badge.setMaximumWidth(110)
        health_badge.setWordWrap(False)
        title_row.addWidget(health_badge, 0)
        layout.addLayout(title_row)

        # Meta row: kind, branch, age, test/CI status, blocker
        meta_parts: list[str] = [task.kind.value]
        if task.branch:
            meta_parts.append(f"⎇ {task.branch}")
        if task.last_test_run and task.last_test_run.total > 0:
            tr = task.last_test_run
            meta_parts.append(f"{tr.passed}/{tr.total} tests")
        if task.last_ci_run and task.last_ci_run.status:
            ci = task.last_ci_run.status
            symbol = {"success": "✓", "failure": "✗", "in_progress": "…"}.get(ci, "·")
            meta_parts.append(f"CI {symbol}")
        if task.state == TaskState.BLOCKED and task.blocked_reason:
            # Trim so a long reason doesn't blow out the card width.
            reason = task.blocked_reason
            if len(reason) > 50:
                reason = reason[:47] + "…"
            meta_parts.append(f"blocked: {reason}")
        meta_parts.append(_relative_time(task.updated_at))
        meta_lbl = QLabel("  ·  ".join(meta_parts))
        meta_lbl.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        layout.addWidget(meta_lbl)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate_requested.emit(self._task.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.detail_requested.emit(self._task.id)
        super().mouseDoubleClickEvent(event)

    def _on_context_menu(self, pos) -> None:
        self.menu_requested.emit(self._task.id, self.mapToGlobal(pos))


# ── New task dialog ─────────────────────────────────────────────────


class _NewTaskDialog(QDialog):
    """Styled dialog for creating a new task. Picks a kind + title + description."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New task")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background: {tc.get('bg_base')}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QLabel("Create a new task")
        header.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_LG}px; "
            f"font-weight: 600; background: transparent;"
        )
        layout.addWidget(header)

        # Kind picker
        kind_label = QLabel("Kind")
        kind_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; "
            f"font-weight: 600; background: transparent;"
        )
        layout.addWidget(kind_label)

        # Reduced set of primary kinds for the dropdown. The full enum
        # stays intact so old tasks with INCIDENT/EXPLORE/CHORE still
        # render, but we only offer the three that cover ~95% of real
        # usage. EXPLORE and CHORE are rare enough that typing a title
        # is faster than picking from a long list.
        self._kind_combo = QComboBox()
        for kind in (TaskKind.FEATURE, TaskKind.BUGFIX, TaskKind.REFACTOR):
            colour = _KIND_COLOURS.get(kind, tc.get("text_tertiary"))
            self._kind_combo.addItem(f"●  {kind.value.capitalize()}", kind)
            self._kind_combo.setItemData(
                self._kind_combo.count() - 1,
                QColor(colour),
                Qt.ItemDataRole.ForegroundRole,
            )
        self._kind_combo.setStyleSheet(
            f"QComboBox {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_menu')}; "
            f"border-radius: 4px; padding: 6px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QComboBox:hover {{ border-color: {tc.get('accent_primary')}; }}"
            f"QComboBox QAbstractItemView {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_primary')}; "
            f"selection-background-color: {tc.get('bg_active')}; "
            f"border: 1px solid {tc.get('border_menu')}; }}"
        )
        layout.addWidget(self._kind_combo)

        # Title
        title_label = QLabel("Title")
        title_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"background: transparent; margin-top: 4px;"
        )
        layout.addWidget(title_label)
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("e.g. Add CSV export to user reports")
        self._title_edit.setStyleSheet(
            f"QLineEdit {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 4px; padding: 7px 10px; font-size: {tc.FONT_BASE}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        layout.addWidget(self._title_edit)

        # Description (optional)
        desc_label = QLabel("Description (optional)")
        desc_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"background: transparent; margin-top: 4px;"
        )
        layout.addWidget(desc_label)
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText(
            "What are you trying to achieve? The AI will use this for context."
        )
        self._desc_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 4px; padding: 7px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QPlainTextEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._desc_edit.setMaximumHeight(120)
        layout.addWidget(self._desc_edit)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; "
            f"border-radius: 4px; padding: 6px 14px; font-size: {tc.FONT_MD}px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        create = QPushButton("Create")
        create.setCursor(Qt.CursorShape.PointingHandCursor)
        create.setDefault(True)
        create.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; "
            f"border-radius: 4px; padding: 6px 18px; font-size: {tc.FONT_MD}px; "
            f"font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        create.clicked.connect(self.accept)
        btn_row.addWidget(create)
        layout.addLayout(btn_row)

        self._title_edit.setFocus()

    def get_values(self) -> tuple[TaskKind, str, str]:
        kind = self._kind_combo.currentData() or TaskKind.FEATURE
        return (
            kind,
            self._title_edit.text().strip(),
            self._desc_edit.toPlainText().strip(),
        )


# ── Helpers ─────────────────────────────────────────────────────────


def _relative_time(ts: float) -> str:
    """Render a timestamp as a short relative string ('3m ago', '2h ago')."""
    if ts <= 0:
        return ""
    delta = datetime.now().timestamp() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)}d ago"
    return datetime.fromtimestamp(ts).strftime("%b %d")


# Keep imports happy when other modules look for Path-related helpers later.
__all__ = ["TasksPanel"]
_ = Path  # noqa: F841 — reserved for future filesystem-aware features
