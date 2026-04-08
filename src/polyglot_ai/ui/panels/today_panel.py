"""Today landing page — answers "what should I do right now?".

Renders three top-level widgets:

1. **Active Tasks** — cards for ACTIVE / PLANNING / REVIEW tasks
   in the open project, click to activate.
2. **Attention** — failed CI runs on the user's branches and
   any open PRs, populated via the ``gh`` CLI when available.
3. **Quick Actions** — keyboard-friendly buttons that fire the
   most-used commands (run all tests, new task, refresh CI, etc.)

This is the dashboard the spec describes: every existing panel
already lives in the sidebar, and the Today page surfaces a
glanceable summary plus jump-off points so a returning user knows
what to do next without clicking around.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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

logger = logging.getLogger(__name__)


_KIND_COLOURS: dict[TaskKind, str] = {
    TaskKind.FEATURE: "#4ec9b0",
    TaskKind.BUGFIX: "#f48771",
    TaskKind.INCIDENT: "#f44747",
    TaskKind.REFACTOR: "#9cdcfe",
    TaskKind.EXPLORE: "#e5a00d",
    TaskKind.CHORE: "#888888",
}


class TodayPanel(QWidget):
    """The morning dashboard. Sits at the top of the activity bar.

    Re-renders on task list/state changes, when a project is opened,
    and the first time the panel becomes visible. There is no
    background polling timer — press the refresh button or re-open
    the project to force a fresh fetch.
    """

    # Re-render request signal — used by background workers (gh fetch)
    # to come back onto the GUI thread safely.
    _attention_loaded = pyqtSignal(list)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        standalone: bool = False,
    ) -> None:
        """Create the Today dashboard.

        ``standalone=True`` is used by ``_on_expand`` to build the
        "open in a separate window" copy — in that mode the expand
        button is hidden.
        """
        super().__init__(parent)
        self._task_manager: TaskManager | None = None
        self._event_bus = None
        self._project_root: Path | None = None
        self._attention_items: list[_AttentionItem] = []
        # Task-derived attention rows computed synchronously in
        # ``_refresh_attention_async`` and then merged with the gh
        # worker's results, so the user sees task health rows even
        # when gh is slow or broken.
        self._pending_task_attention: list[_AttentionItem] = []
        self._standalone = standalone
        self._standalone_window: QWidget | None = None

        self._attention_loaded.connect(self._on_attention_loaded)

        self._setup_ui()

    # ── Wiring ──────────────────────────────────────────────────────

    def set_task_manager(self, manager: TaskManager) -> None:
        self._task_manager = manager
        self._refresh_active_tasks()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        event_bus.subscribe(EVT_TASK_LIST_CHANGED, lambda **_: self._refresh_active_tasks())
        event_bus.subscribe(EVT_TASK_CHANGED, lambda **_: self._refresh_active_tasks())
        event_bus.subscribe("project:opened", self._on_project_opened)
        event_bus.subscribe("project_refreshed", self._on_project_opened)

    def _on_project_opened(self, **kwargs) -> None:
        path = kwargs.get("path", "")
        if path:
            try:
                self._project_root = Path(path)
            except Exception:
                logger.debug("today_panel: bad project path", exc_info=True)
        self._refresh_active_tasks()
        self._refresh_attention_async()

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().showEvent(event)
        # First time the panel becomes visible, kick off attention fetch.
        self._refresh_active_tasks()
        if not self._attention_items:
            self._refresh_attention_async()

    # ── UI scaffolding ──────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(38)
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #333;")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 0, 8, 0)

        title = QLabel("TODAY")
        title.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #ddd; "
            "letter-spacing: 1px; background: transparent;"
        )
        h.addWidget(title)

        self._date_label = QLabel("")
        self._date_label.setStyleSheet(
            "font-size: 11px; color: #888; background: transparent; margin-left: 8px;"
        )
        h.addWidget(self._date_label)
        h.addStretch()

        refresh_btn = self._icon_btn(self._draw_refresh_icon(), "Refresh dashboard")
        refresh_btn.clicked.connect(self._refresh_all)
        h.addWidget(refresh_btn)

        if not self._standalone:
            expand_btn = self._icon_btn(self._draw_expand_icon(), "Open in a separate window")
            expand_btn.clicked.connect(self._on_expand)
            h.addWidget(expand_btn)

        layout.addWidget(header)

        # Scrollable body so the dashboard works in any sidebar width.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #1e1e1e; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        body = QWidget()
        body.setStyleSheet("background: #1e1e1e;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(14)

        # ── Active Tasks card ──
        body_layout.addWidget(self._make_section_label("Active tasks"))
        self._active_tasks_container = QWidget()
        self._active_tasks_container.setStyleSheet("background: transparent;")
        self._active_tasks_layout = QVBoxLayout(self._active_tasks_container)
        self._active_tasks_layout.setContentsMargins(0, 0, 0, 0)
        self._active_tasks_layout.setSpacing(6)
        body_layout.addWidget(self._wrap_card(self._active_tasks_container))

        # ── Attention card ──
        body_layout.addWidget(self._make_section_label("Attention"))
        self._attention_container = QWidget()
        self._attention_container.setStyleSheet("background: transparent;")
        self._attention_layout = QVBoxLayout(self._attention_container)
        self._attention_layout.setContentsMargins(0, 0, 0, 0)
        self._attention_layout.setSpacing(6)
        body_layout.addWidget(self._wrap_card(self._attention_container))

        # ── Quick Actions card ──
        body_layout.addWidget(self._make_section_label("Quick actions"))
        actions_container = QWidget()
        actions_container.setStyleSheet("background: transparent;")
        actions_grid = QVBoxLayout(actions_container)
        actions_grid.setContentsMargins(0, 0, 0, 0)
        actions_grid.setSpacing(6)
        # Build the action buttons in two rows of three.
        actions = [
            ("▶  Run all tests", self._action_run_tests),
            ("✨  New task", self._action_new_task),
            ("⟳  Refresh CI", self._action_refresh_ci),
            ("⎇  Source control", self._action_open_git),
            ("📊  Database", self._action_open_database),
            ("💬  Chat", self._action_open_chat),
        ]
        row = QHBoxLayout()
        row.setSpacing(6)
        for i, (label, handler) in enumerate(actions):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: #2a2d2e; color: #ddd; border: 1px solid #3a3a3a; "
                "border-radius: 4px; padding: 7px 10px; font-size: 11px; text-align: left; }"
                "QPushButton:hover { background: #094771; border-color: #0e639c; color: #fff; }"
            )
            btn.clicked.connect(handler)
            row.addWidget(btn, stretch=1)
            if (i + 1) % 3 == 0:
                actions_grid.addLayout(row)
                row = QHBoxLayout()
                row.setSpacing(6)
        if row.count() > 0:
            actions_grid.addLayout(row)
        body_layout.addWidget(self._wrap_card(actions_container))

        body_layout.addStretch()

        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        # Set the date string
        self._date_label.setText(datetime.now().strftime("%A, %b %d"))

    # ── Helpers (chrome) ────────────────────────────────────────────

    def _wrap_card(self, inner: QWidget) -> QWidget:
        """Wrap a content widget in a card frame with consistent styling."""
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: #252526; border: 1px solid #333; border-radius: 6px; }"
        )
        wrap_layout = QVBoxLayout(frame)
        wrap_layout.setContentsMargins(12, 10, 12, 10)
        wrap_layout.setSpacing(0)
        wrap_layout.addWidget(inner)
        return frame

    def _make_section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: #777; font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.8px; background: transparent;"
        )
        return lbl

    def _icon_btn(self, icon: QIcon, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("todayHdrBtn")
        btn.setIcon(icon)
        btn.setFixedSize(24, 24)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#todayHdrBtn { background: transparent; border: none; }"
            "#todayHdrBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        return btn

    @staticmethod
    def _draw_refresh_icon() -> QIcon:
        from PyQt6.QtCore import QRectF

        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawArc(QRectF(3, 3, 10, 10), 60 * 16, 280 * 16)
        p.drawLine(12, 2, 12, 6)
        p.drawLine(12, 6, 8, 6)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_expand_icon() -> QIcon:
        """Box-with-arrow glyph for the open-in-window button."""
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        # Small window outline
        p.drawRect(2, 5, 9, 9)
        # Diagonal arrow pointing up-right
        p.drawLine(7, 9, 14, 2)
        p.drawLine(9, 2, 14, 2)
        p.drawLine(14, 2, 14, 7)
        p.end()
        return QIcon(pm)

    def _on_expand(self) -> None:
        """Open the Today dashboard in a standalone, larger window.

        Creates a fresh ``TodayPanel`` bound to the same task manager
        and event bus so both views stay in sync. The new window is a
        child of the main window (so it stays with the app group) but
        floats independently and can be resized / maximised.
        """
        # If a previous standalone window was opened and then closed,
        # the ``destroyed`` lambda below SHOULD have reset this back
        # to ``None``, but in practice the signal can fire late (or
        # the Python reference can outlive the C++ object). Probe
        # the existing window with a method that touches the C++
        # side — if that raises ``RuntimeError`` or the window isn't
        # visible, treat it as gone and fall through to building a
        # fresh one. Otherwise we'd silently no-op on every click.
        existing = getattr(self, "_standalone_window", None)
        if existing is not None:
            alive = False
            try:
                alive = existing.isVisible()
            except RuntimeError:
                # "wrapped C/C++ object has been deleted"
                alive = False
            except Exception:
                logger.debug("today_panel: stale standalone window", exc_info=True)
                alive = False
            if alive:
                try:
                    existing.raise_()
                    existing.activateWindow()
                    return
                except Exception:
                    logger.debug("today_panel: could not raise standalone window", exc_info=True)
            # Stale or dead — drop the reference and build a new one.
            self._standalone_window = None

        win = QWidget(self.window(), Qt.WindowType.Window)
        win.setWindowTitle("Today — Polyglot AI")
        win.resize(1100, 780)
        win.setStyleSheet("QWidget { background: #1e1e1e; }")
        inner = TodayPanel(parent=win, standalone=True)
        if self._task_manager is not None:
            inner.set_task_manager(self._task_manager)
        if self._event_bus is not None:
            inner.set_event_bus(self._event_bus)
        # Pre-seed the project root so the attention widget runs
        # immediately (normally it waits for a project:opened event).
        if self._project_root is not None:
            inner._project_root = self._project_root
            inner._refresh_attention_async()
        wlayout = QVBoxLayout(win)
        wlayout.setContentsMargins(0, 0, 0, 0)
        wlayout.addWidget(inner)
        win.show()
        self._standalone_window = win
        win.destroyed.connect(lambda _=None: setattr(self, "_standalone_window", None))

    # ── Active Tasks rendering ──────────────────────────────────────

    def _refresh_active_tasks(self) -> None:
        # Clear
        while self._active_tasks_layout.count():
            item = self._active_tasks_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if self._task_manager is None or self._task_manager.project_root is None:
            self._active_tasks_layout.addWidget(
                self._empty_label("Open a project to see your tasks here.")
            )
            return

        tasks = self._task_manager.list_tasks(
            state_filter=[TaskState.ACTIVE, TaskState.PLANNING, TaskState.REVIEW]
        )
        if not tasks:
            empty = self._empty_label(
                "No active tasks yet. Use the Tasks sidebar (clipboard icon) to create one."
            )
            self._active_tasks_layout.addWidget(empty)
            return

        # Show up to 5; the user can open the Tasks sidebar for the rest.
        for task in tasks[:5]:
            self._active_tasks_layout.addWidget(self._make_task_row(task))
        if len(tasks) > 5:
            more = self._empty_label(f"+{len(tasks) - 5} more — see Tasks sidebar")
            self._active_tasks_layout.addWidget(more)

    def _make_task_row(self, task: Task) -> QWidget:
        return _TaskRow(
            task,
            on_click=self._activate_task,
            on_double_click=self._open_task_detail,
        )

    def _activate_task(self, task_id: str) -> None:
        if self._task_manager is None:
            return
        self._task_manager.set_active(task_id)

    def _open_task_detail(self, task_id: str) -> None:
        if self._task_manager is None:
            return
        self._task_manager.set_active(task_id)
        task = self._task_manager.active
        if task is None:
            return
        try:
            from polyglot_ai.ui.dialogs.task_detail_dialog import TaskDetailDialog

            dlg = TaskDetailDialog(task, self._task_manager, parent=self.window())
            dlg.exec()
        except Exception:
            logger.exception("today_panel: could not open task detail dialog")

    def open_task(self, task_id: str) -> None:
        """Public entry point used by attention-row action buttons."""
        self._open_task_detail(task_id)

    # ── Attention rendering ─────────────────────────────────────────

    def _refresh_attention_async(self) -> None:
        """Spawn a background thread that runs gh queries and emits a signal."""
        if self._project_root is None or not self._project_root.is_dir():
            return
        # Start with task-derived attention rows — these don't need
        # gh or any network call. Even if gh isn't installed or the
        # worker fails the user still sees blocked/stale/failing
        # tasks.
        task_rows = self._task_attention_items()
        if shutil.which("gh") is None:
            rows = list(task_rows)
            rows.append(
                _AttentionItem(
                    severity="info",
                    text="Install GitHub CLI (gh) to see PRs and CI status here.",
                )
            )
            self._on_attention_loaded(rows)
            return

        cwd = str(self._project_root)
        # Stash the task rows on the instance so the worker can
        # prepend them to whatever gh returns.
        self._pending_task_attention = task_rows
        thread = threading.Thread(
            target=self._gh_attention_worker,
            args=(cwd,),
            daemon=True,
            name="today_attention",
        )
        thread.start()
        return

    def _task_attention_items(self) -> list[_AttentionItem]:
        """Translate task health into attention rows.

        Blocked and NEEDS_ATTENTION tasks render as errors; stale
        tasks render as warnings. Keeps the Today page useful even
        without any GitHub integration.
        """
        if self._task_manager is None:
            return []
        from polyglot_ai.core.task_health import HealthLevel, compute_health

        rows: list[_AttentionItem] = []
        severity_map = {
            HealthLevel.BLOCKED: "error",
            HealthLevel.NEEDS_ATTENTION: "error",
            HealthLevel.STALE: "warn",
        }
        for task in self._task_manager.list_tasks():
            health = compute_health(task)
            if not health.attention:
                continue
            severity = severity_map.get(health.level, "warn")
            text = f"{task.title} — {health.label}"
            rows.append(
                _AttentionItem(
                    severity=severity,
                    text=text,
                    action_label="Open",
                    action_data={"kind": "task", "value": task.id},
                    tooltip=health.reason,
                )
            )
        return rows

    def _legacy_refresh_attention_unused(self) -> None:
        """Placeholder replaced by the new entry point above."""
        return

        cwd = str(self._project_root)
        thread = threading.Thread(
            target=self._gh_attention_worker,
            args=(cwd,),
            daemon=True,
            name="today_attention",
        )
        thread.start()

    def _gh_attention_worker(self, cwd: str) -> None:
        """Background worker — runs gh queries and emits results.

        Always emits, even on failure, so the UI never hangs in a
        loading state.
        """
        items: list[_AttentionItem] = []
        try:
            # 1. My open PRs in this repo (any branch).
            try:
                pr_out = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--author",
                        "@me",
                        "--state",
                        "open",
                        "--json",
                        "number,title,url,headRefName",
                        "--limit",
                        "5",
                    ],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if pr_out.returncode == 0 and pr_out.stdout.strip():
                    prs = json.loads(pr_out.stdout)
                    for pr in prs:
                        items.append(
                            _AttentionItem(
                                severity="info",
                                text=f"PR #{pr['number']}: {pr['title']}",
                                action_label="Open",
                                action_data={"kind": "url", "value": pr["url"]},
                            )
                        )
                elif pr_out.returncode != 0:
                    # Don't silently swallow gh failures — a broken or
                    # unauthenticated gh should look different from
                    # "nothing to show", or the user will never realise
                    # the panel is degraded.
                    stderr = (pr_out.stderr or "").strip().splitlines()
                    detail = stderr[-1] if stderr else f"exit {pr_out.returncode}"
                    items.append(
                        _AttentionItem(
                            severity="warn",
                            text=f"Could not fetch PRs: {detail[:120]}",
                        )
                    )
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                logger.warning("today_panel: gh pr list failed: %s", e)
                items.append(
                    _AttentionItem(
                        severity="warn",
                        text=f"Could not fetch PRs: {e}",
                    )
                )

            # 2. Recent failed CI runs in this repo.
            try:
                ci_out = subprocess.run(
                    [
                        "gh",
                        "run",
                        "list",
                        "--status",
                        "failure",
                        "--limit",
                        "3",
                        "--json",
                        "databaseId,name,headBranch,createdAt,url",
                    ],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if ci_out.returncode == 0 and ci_out.stdout.strip():
                    runs = json.loads(ci_out.stdout)
                    for run in runs:
                        items.append(
                            _AttentionItem(
                                severity="error",
                                text=(
                                    f"CI failed: {run.get('name', 'workflow')} on "
                                    f"{run.get('headBranch', '?')}"
                                ),
                                action_label="Investigate",
                                action_data={
                                    "kind": "investigate_ci",
                                    "value": run,
                                },
                            )
                        )
                elif ci_out.returncode != 0:
                    stderr = (ci_out.stderr or "").strip().splitlines()
                    detail = stderr[-1] if stderr else f"exit {ci_out.returncode}"
                    items.append(
                        _AttentionItem(
                            severity="warn",
                            text=f"Could not fetch CI runs: {detail[:120]}",
                        )
                    )
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                logger.warning("today_panel: gh run list failed: %s", e)
                items.append(
                    _AttentionItem(
                        severity="warn",
                        text=f"Could not fetch CI runs: {e}",
                    )
                )
        except Exception:
            logger.exception("today_panel: attention worker crashed")
        # Prepend task-derived rows (blocked, stale, failing) so they
        # appear at the top regardless of what gh returned. The list
        # was snapshot on the GUI thread before the worker started so
        # no cross-thread access is needed here.
        combined = list(self._pending_task_attention) + items
        self._attention_loaded.emit(combined)

    def _on_attention_loaded(self, items: list) -> None:
        self._attention_items = items
        # Clear current
        while self._attention_layout.count():
            it = self._attention_layout.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        if not items:
            self._attention_layout.addWidget(
                self._empty_label("Nothing needs your attention right now. Nice.")
            )
            return
        for item in items:
            self._attention_layout.addWidget(_AttentionRow(item, panel=self))

    # ── Action handlers ─────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._refresh_active_tasks()
        self._refresh_attention_async()

    def _action_run_tests(self) -> None:
        win = self.window()
        test_panel = getattr(win, "test_panel", None)
        if test_panel is None:
            return
        try:
            self._switch_sidebar(test_panel)
            test_panel._on_run_all()
        except Exception:
            logger.exception("today_panel: run tests action failed")

    def _action_new_task(self) -> None:
        win = self.window()
        tasks_panel = getattr(win, "tasks_panel", None)
        if tasks_panel is None:
            return
        try:
            self._switch_sidebar(tasks_panel)
            tasks_panel._on_new_task()
        except Exception:
            logger.exception("today_panel: new task action failed")

    def _action_refresh_ci(self) -> None:
        win = self.window()
        cicd_panel = getattr(win, "cicd_panel", None)
        if cicd_panel is None:
            return
        try:
            self._show_right_tab(cicd_panel)
            cicd_panel._refresh_runs()
        except Exception:
            logger.exception("today_panel: refresh CI action failed")

    def _action_open_git(self) -> None:
        win = self.window()
        git_panel = getattr(win, "git_panel", None)
        if git_panel is not None:
            self._switch_sidebar(git_panel)

    def _action_open_database(self) -> None:
        win = self.window()
        db_panel = getattr(win, "database_panel", None)
        if db_panel is not None:
            self._switch_sidebar(db_panel)

    def _action_open_chat(self) -> None:
        win = self.window()
        chat = getattr(win, "chat_panel", None)
        if chat is not None:
            self._show_right_tab(chat)

    # ── Sidebar / right-tab helpers ─────────────────────────────────

    def _switch_sidebar(self, target_widget: QWidget) -> None:
        win = self.window()
        stack = getattr(win, "_sidebar_stack", None)
        if stack is None:
            return
        try:
            stack.setCurrentWidget(target_widget)
            stack.show()
        except Exception:
            logger.exception("today_panel: could not switch sidebar")

    def _show_right_tab(self, target_widget: QWidget) -> None:
        win = self.window()
        right_tabs = getattr(win, "_right_tabs", None)
        if right_tabs is None:
            return
        try:
            idx = right_tabs.indexOf(target_widget)
            if idx >= 0:
                right_tabs.setCurrentIndex(idx)
        except Exception:
            logger.exception("today_panel: could not switch right tab")

    # ── Public hook for the attention "Investigate" action ──────────

    def investigate_ci_failure(self, run: dict) -> None:
        """Forward the failed CI run to the CI panel's incident creator."""
        win = self.window()
        cicd_panel = getattr(win, "cicd_panel", None)
        if cicd_panel is None:
            return
        try:
            cicd_panel._create_incident_from_run(run)
        except Exception:
            logger.exception("today_panel: investigate CI failed")

    # ── Empty label helper ──────────────────────────────────────────

    def _empty_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #888; font-size: 11px; padding: 4px 0; background: transparent;")
        return lbl


# ── Internal data classes for the attention list ──────────────────


class _AttentionItem:
    """Plain holder so the worker thread can ship typed rows back."""

    def __init__(
        self,
        severity: str,
        text: str,
        action_label: str | None = None,
        action_data: dict | None = None,
        tooltip: str = "",
    ) -> None:
        self.severity = severity  # "error" | "warn" | "info"
        self.text = text
        self.action_label = action_label
        self.action_data = action_data
        self.tooltip = tooltip


class _AttentionRow(QWidget):
    """Single row in the attention list with an optional action button."""

    def __init__(
        self,
        item: _AttentionItem,
        panel: TodayPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._panel = panel
        self.setStyleSheet("background: transparent;")
        if item.tooltip:
            self.setToolTip(item.tooltip)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        icon_glyph = {"error": "🔴", "warn": "🟡", "info": "ℹ"}.get(item.severity, "·")
        icon = QLabel(icon_glyph)
        icon.setFixedWidth(18)
        icon.setStyleSheet("font-size: 11px; background: transparent;")
        layout.addWidget(icon)

        text = QLabel(item.text)
        text.setWordWrap(True)
        text.setStyleSheet("color: #ddd; font-size: 11px; background: transparent;")
        if item.tooltip:
            text.setToolTip(item.tooltip)
        layout.addWidget(text, stretch=1)

        if item.action_label:
            btn = QPushButton(item.action_label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: #094771; color: #fff; border: none; "
                "border-radius: 3px; padding: 3px 10px; font-size: 10px; font-weight: 600; }"
                "QPushButton:hover { background: #1a8ae8; }"
            )
            btn.clicked.connect(self._on_action)
            layout.addWidget(btn)

    def _on_action(self) -> None:
        data = self._item.action_data or {}
        kind = data.get("kind")
        value = data.get("value")
        if kind == "url" and value:
            self._open_url(value)
        elif kind == "investigate_ci" and isinstance(value, dict):
            self._panel.investigate_ci_failure(value)
        elif kind == "task" and isinstance(value, str):
            # Activate the task and open its detail dialog so the
            # user can see the timeline, change state, or unblock.
            self._panel.open_task(value)

    def _open_url(self, url: str) -> None:
        try:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            logger.exception("today_panel: could not open url")


# ── Active task row ────────────────────────────────────────────────


class _TaskRow(QWidget):
    """A compact one-line task row for the Today dashboard."""

    def __init__(self, task: Task, on_click, on_double_click=None) -> None:
        super().__init__()
        self._task = task
        self._on_click = on_click
        self._on_double_click = on_double_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to activate · double-click to open details")
        self.setStyleSheet(
            "_TaskRow { background: transparent; border-radius: 4px; }"
            "_TaskRow:hover { background: #2a2d2e; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(8)

        # Kind dot
        dot = QLabel("●")
        dot.setStyleSheet(
            f"color: {_KIND_COLOURS.get(task.kind, '#888')}; font-size: 11px; "
            "background: transparent;"
        )
        layout.addWidget(dot)

        # Title
        title = QLabel(task.title)
        title.setStyleSheet("color: #e0e0e0; font-size: 12px; background: transparent;")
        layout.addWidget(title, stretch=1)

        # Status meta
        meta_parts: list[str] = [task.kind.value]
        if task.last_test_run and task.last_test_run.total > 0:
            tr = task.last_test_run
            meta_parts.append(f"{tr.passed}/{tr.total}")
        if task.last_ci_run and task.last_ci_run.status:
            symbol = {
                "success": "✓",
                "failure": "✗",
                "in_progress": "…",
            }.get(task.last_ci_run.status, "·")
            meta_parts.append(f"CI {symbol}")
        meta = QLabel("  ·  ".join(meta_parts))
        meta.setStyleSheet("color: #888; font-size: 10px; background: transparent;")
        layout.addWidget(meta)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._task.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 — Qt override
        if self._on_double_click and event.button() == Qt.MouseButton.LeftButton:
            self._on_double_click(self._task.id)
        super().mouseDoubleClickEvent(event)
