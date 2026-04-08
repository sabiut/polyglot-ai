"""Full task detail dialog — timeline + metadata + actions.

Renders everything the panels have recorded onto a task: title,
kind, state, branch/base_branch, description, modified files,
latest test/CI snapshots, and the full chronological timeline of
events. Also renders a read-only plan checklist reserved for a
future AI plan generator (``task.plan`` is empty until that lands).

Provides actions to change state, open the PR, and copy the task
as a markdown report (great for standups).

Opened by double-clicking a task card in the Tasks sidebar or the
Today landing page.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.task_manager import TaskManager
from polyglot_ai.core.tasks import Task, TaskKind, TaskNote, TaskState

logger = logging.getLogger(__name__)


_KIND_COLOURS: dict[TaskKind, str] = {
    TaskKind.FEATURE: "#4ec9b0",
    TaskKind.BUGFIX: "#f48771",
    TaskKind.INCIDENT: "#f44747",
    TaskKind.REFACTOR: "#9cdcfe",
    TaskKind.EXPLORE: "#e5a00d",
    TaskKind.CHORE: "#888888",
}


_STATE_COLOURS: dict[TaskState, str] = {
    TaskState.PLANNING: "#888888",
    TaskState.ACTIVE: "#4ec9b0",
    TaskState.REVIEW: "#9cdcfe",
    TaskState.BLOCKED: "#e5a00d",
    TaskState.DONE: "#666666",
    TaskState.ARCHIVED: "#444444",
}


# Map note kinds → display glyph + colour. Unknown kinds fall back
# to a neutral bullet.
_NOTE_GLYPHS: dict[str, tuple[str, str]] = {
    "created": ("✦", "#9cdcfe"),
    "state_changed": ("↯", "#888888"),
    "branch_created": ("⎇", "#9cdcfe"),
    "committed": ("✓", "#4ec9b0"),
    "pushed": ("⇡", "#4ec9b0"),
    "pr_opened": ("⇧", "#9cdcfe"),
    "tested": ("⚙", "#4ec9b0"),
    "review_clean": ("✓", "#4ec9b0"),
    "review_findings": ("!", "#e5a00d"),
    "review_failed": ("✗", "#f48771"),
    "ci_run": ("●", "#9cdcfe"),
    "ci_failure_imported": ("⚠", "#f48771"),
    "chat_started": ("✎", "#888888"),
    "ai_response": ("✎", "#888888"),
    "user_message": ("✎", "#aaaaaa"),
}


class TaskDetailDialog(QDialog):
    """Full-screen task detail viewer.

    Lifetime: opened on demand from the Tasks sidebar / Today panel
    via a double-click. Re-reads the task each time it's opened so
    edits made elsewhere are reflected.
    """

    def __init__(
        self,
        task: Task,
        manager: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self._manager = manager

        self.setWindowTitle(f"Task — {task.title}")
        self.setMinimumSize(720, 600)
        self.setStyleSheet("QDialog { background: #1e1e1e; }")

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        # ── Header: title, kind/state badges, branch ──
        header = QVBoxLayout()
        header.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        kind_dot = QLabel("●")
        kind_dot.setStyleSheet(
            f"color: {_KIND_COLOURS.get(self._task.kind, '#888')}; "
            "font-size: 14px; background: transparent;"
        )
        title_row.addWidget(kind_dot)

        title_lbl = QLabel(self._task.title)
        title_lbl.setStyleSheet(
            "color: #ffffff; font-size: 17px; font-weight: 700; background: transparent;"
        )
        title_lbl.setWordWrap(True)
        title_row.addWidget(title_lbl, stretch=1)
        header.addLayout(title_row)

        # Meta line: kind • state • branch • PR
        meta_parts: list[str] = [self._task.kind.value]
        meta_parts.append(self._task.state.value)
        if self._task.branch:
            meta_parts.append(f"⎇ {self._task.branch}")
        if self._task.pr_url:
            meta_parts.append(f"PR #{self._task.pr_number or '?'}")
        meta_lbl = QLabel("  ·  ".join(meta_parts))
        meta_lbl.setStyleSheet("color: #888; font-size: 11px; background: transparent;")
        header.addWidget(meta_lbl)

        layout.addLayout(header)

        # ── Description (read-only for now; editing is a v2 feature) ──
        if self._task.description:
            desc_label = QLabel("Description")
            desc_label.setStyleSheet(
                "color: #777; font-size: 10px; font-weight: 700; "
                "letter-spacing: 0.6px; background: transparent;"
            )
            layout.addWidget(desc_label)
            desc = QTextEdit()
            desc.setPlainText(self._task.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(140)
            desc.setStyleSheet(
                "QTextEdit { background: #252526; color: #d0d0d0; border: 1px solid #333; "
                "border-radius: 4px; padding: 8px 10px; font-size: 12px; }"
            )
            layout.addWidget(desc)

        # ── Plan checklist (read-only) ──
        if self._task.plan:
            plan_card = self._build_plan_card()
            if plan_card is not None:
                layout.addWidget(plan_card)

        # ── Stats card (test/CI snapshots, modified files count) ──
        stats_card = self._build_stats_card()
        if stats_card is not None:
            layout.addWidget(stats_card)

        # ── Timeline ──
        timeline_label = QLabel("Timeline")
        timeline_label.setStyleSheet(
            "color: #777; font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.6px; background: transparent; margin-top: 4px;"
        )
        layout.addWidget(timeline_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #333; background: #181818; "
            "border-radius: 4px; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        timeline_inner = QWidget()
        timeline_inner.setStyleSheet("background: #181818;")
        timeline_layout = QVBoxLayout(timeline_inner)
        timeline_layout.setContentsMargins(14, 12, 14, 12)
        timeline_layout.setSpacing(8)
        timeline_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Notes are stored append-only — render newest first.
        notes = list(reversed(self._task.notes))
        if not notes:
            empty = QLabel("No events yet. Activity from other panels shows up here.")
            empty.setStyleSheet(
                "color: #666; font-size: 11px; background: transparent; padding: 8px;"
            )
            timeline_layout.addWidget(empty)
        else:
            for note in notes:
                timeline_layout.addWidget(self._make_timeline_row(note))

        scroll.setWidget(timeline_inner)
        layout.addWidget(scroll, stretch=1)

        # ── Actions ──
        actions = QHBoxLayout()
        actions.setSpacing(8)

        if self._task.pr_url:
            open_pr_btn = self._mk_button("Open PR")
            open_pr_btn.clicked.connect(self._open_pr)
            actions.addWidget(open_pr_btn)

        copy_btn = self._mk_button("Copy as standup")
        copy_btn.setToolTip(
            "Copy a markdown summary of this task to the clipboard (handy for daily standups)"
        )
        copy_btn.clicked.connect(self._copy_as_standup)
        actions.addWidget(copy_btn)

        actions.addStretch()

        # State transitions: build a small group of state-change buttons
        # with friendlier labels than the raw enum values.
        state_labels: dict[TaskState, str] = {
            TaskState.ACTIVE: "Start work",
            TaskState.REVIEW: "Mark ready for review",
            TaskState.DONE: "Mark done",
            TaskState.BLOCKED: "Mark blocked",
            TaskState.PLANNING: "Back to planning",
        }
        for state in (
            TaskState.ACTIVE,
            TaskState.REVIEW,
            TaskState.DONE,
        ):
            if self._task.state == state:
                continue
            btn = self._mk_button(state_labels[state])
            btn.clicked.connect(lambda _=False, s=state: self._set_state(s))
            actions.addWidget(btn)

        close_btn = self._mk_button("Close", primary=True)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)

        layout.addLayout(actions)

    # ── Components ─────────────────────────────────────────────────

    def _build_plan_card(self) -> QFrame | None:
        """Render the task's plan steps as a read-only checklist.

        Plan steps are populated by the AI for FEATURE tasks (not yet
        wired to a generator — when the generator lands it will write
        into ``task.plan`` and this card will start showing content).
        Each step renders with a glyph reflecting its status:
        ``☐`` pending, ``◐`` in_progress, ``☑`` done, ``⊘`` skipped.
        """
        steps = self._task.plan
        if not steps:
            return None

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #252526; border: 1px solid #333; border-radius: 6px; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(4)

        header = QLabel("PLAN")
        header.setStyleSheet(
            "color: #777; font-size: 9px; font-weight: 700; "
            "letter-spacing: 0.6px; background: transparent;"
        )
        v.addWidget(header)

        glyphs = {
            "pending": ("☐", "#888"),
            "in_progress": ("◐", "#e5a00d"),
            "done": ("☑", "#4ec9b0"),
            "skipped": ("⊘", "#666"),
        }
        for step in steps:
            glyph, colour = glyphs.get(step.status, ("·", "#888"))
            row = QHBoxLayout()
            row.setSpacing(8)
            icon = QLabel(glyph)
            icon.setFixedWidth(16)
            icon.setStyleSheet(
                f"color: {colour}; font-size: 13px; background: transparent;"
            )
            row.addWidget(icon)
            text_colour = "#666" if step.status in ("done", "skipped") else "#d0d0d0"
            text = QLabel(step.text or "(untitled step)")
            text.setStyleSheet(
                f"color: {text_colour}; font-size: 12px; background: transparent;"
            )
            text.setWordWrap(True)
            row.addWidget(text, stretch=1)
            v.addLayout(row)
        return card

    def _build_stats_card(self) -> QFrame | None:
        """A horizontal row with test / CI / files / PR snapshots.

        Returns ``None`` when nothing's been recorded yet, so the
        layout doesn't render an empty card.
        """
        tr = self._task.last_test_run
        ci = self._task.last_ci_run
        files = self._task.modified_files
        if tr is None and ci is None and not files:
            return None

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #252526; border: 1px solid #333; border-radius: 6px; }"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(20)

        if tr is not None and tr.total > 0:
            colour = "#4ec9b0" if tr.all_green else "#f48771"
            layout.addWidget(
                self._stat_block(
                    "Tests",
                    f"{tr.passed}/{tr.total}",
                    colour,
                    sub=_relative_time(tr.timestamp),
                )
            )

        if ci is not None and ci.status:
            symbol_colour = {
                "success": ("✓", "#4ec9b0"),
                "failure": ("✗", "#f48771"),
                "in_progress": ("…", "#e5a00d"),
            }.get(ci.status, ("·", "#888"))
            layout.addWidget(
                self._stat_block(
                    "CI",
                    f"{symbol_colour[0]} {ci.status}",
                    symbol_colour[1],
                    sub=ci.workflow or _relative_time(ci.timestamp),
                )
            )

        if files:
            layout.addWidget(
                self._stat_block(
                    "Files touched",
                    str(len(files)),
                    "#9cdcfe",
                    sub=", ".join(files[:2]) + (" …" if len(files) > 2 else ""),
                )
            )

        if self._task.pr_url:
            layout.addWidget(
                self._stat_block(
                    "Pull request",
                    f"#{self._task.pr_number or '?'}",
                    "#9cdcfe",
                    sub="open",
                )
            )

        layout.addStretch()
        return card

    def _stat_block(self, label: str, value: str, colour: str, sub: str = "") -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            "color: #777; font-size: 9px; font-weight: 700; "
            "letter-spacing: 0.6px; background: transparent;"
        )
        v.addWidget(lbl)
        val = QLabel(value)
        val.setStyleSheet(
            f"color: {colour}; font-size: 14px; font-weight: 600; background: transparent;"
        )
        v.addWidget(val)
        if sub:
            sub_lbl = QLabel(sub)
            sub_lbl.setStyleSheet("color: #666; font-size: 10px; background: transparent;")
            sub_lbl.setWordWrap(True)
            v.addWidget(sub_lbl)
        return wrap

    def _make_timeline_row(self, note: TaskNote) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)

        glyph, colour = _NOTE_GLYPHS.get(note.kind, ("·", "#888888"))
        icon = QLabel(glyph)
        icon.setFixedWidth(18)
        icon.setStyleSheet(
            f"color: {colour}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        h.addWidget(icon)

        # Body: text + relative time on a single line
        body = QHBoxLayout()
        body.setSpacing(6)
        text_lbl = QLabel(note.text or note.kind)
        text_lbl.setStyleSheet("color: #d0d0d0; font-size: 12px; background: transparent;")
        text_lbl.setWordWrap(True)
        body.addWidget(text_lbl, stretch=1)
        time_lbl = QLabel(_relative_time(note.timestamp))
        time_lbl.setStyleSheet("color: #666; font-size: 10px; background: transparent;")
        body.addWidget(time_lbl)
        h.addLayout(body, stretch=1)
        return row

    def _mk_button(self, label: str, primary: bool = False) -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            btn.setStyleSheet(
                "QPushButton { background: #0e639c; color: white; border: none; "
                "border-radius: 4px; padding: 7px 18px; font-size: 12px; "
                "font-weight: 600; }"
                "QPushButton:hover { background: #1a8ae8; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
                "border-radius: 4px; padding: 6px 14px; font-size: 11px; }"
                "QPushButton:hover { background: #4a4a4a; }"
            )
        return btn

    # ── Actions ────────────────────────────────────────────────────

    def _set_state(self, state: TaskState) -> None:
        """Transition the task and close the dialog.

        We close instead of rebuilding in-place because Qt layout
        rebuild was leaving orphaned widgets (old state buttons stayed
        visible next to the new ones). The Tasks sidebar and the
        Today page both auto-refresh on ``task:state_changed``, so
        the user sees the new state immediately there; they can
        reopen the detail dialog to see the fresh timeline.
        """
        try:
            self._manager.set_active(self._task.id)
            self._manager.update_state(state)
        except Exception:
            logger.exception("task_detail: state change failed")
            return
        self.accept()

    def _open_pr(self) -> None:
        if not self._task.pr_url:
            return
        try:
            QDesktopServices.openUrl(QUrl(self._task.pr_url))
        except Exception:
            logger.exception("task_detail: could not open PR url")

    def _copy_as_standup(self) -> None:
        """Build a markdown summary and copy it to the clipboard.

        Format is intended to be paste-friendly into a daily standup
        Slack message: title, what got done, what's next, blockers.
        """
        lines: list[str] = []
        lines.append(f"**{self._task.title}** ({self._task.kind.value})")
        if self._task.branch:
            lines.append(f"Branch: `{self._task.branch}`")
        if self._task.pr_url:
            lines.append(f"PR: {self._task.pr_url}")
        lines.append("")
        # Recent timeline (last 8 events) as bullets.
        recent = list(reversed(self._task.notes))[:8]
        if recent:
            lines.append("Recent:")
            for note in recent:
                stamp = datetime.fromtimestamp(note.timestamp).strftime("%H:%M")
                lines.append(f"- {stamp}  {note.text}")
        if self._task.last_test_run and self._task.last_test_run.total:
            tr = self._task.last_test_run
            lines.append("")
            lines.append(f"Tests: {tr.passed}/{tr.total} passing")
        if self._task.last_ci_run and self._task.last_ci_run.status:
            lines.append(f"CI: {self._task.last_ci_run.status}")
        text = "\n".join(lines)
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(text)
            logger.info("task_detail: copied standup to clipboard")


# ── Helpers ─────────────────────────────────────────────────────────


def _relative_time(ts: float) -> str:
    if not ts:
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
