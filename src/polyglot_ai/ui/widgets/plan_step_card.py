"""Plan step card widget — shows a single step with status and actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.ai.plan_models import PlanStep, PlanStepStatus

# Status → (icon, border color, text color)
_STATUS_STYLE = {
    PlanStepStatus.PENDING: ("○", "#666", "#999"),
    PlanStepStatus.APPROVED: ("◉", "#569cd6", "#569cd6"),
    PlanStepStatus.SKIPPED: ("⊘", "#555", "#666"),
    PlanStepStatus.IN_PROGRESS: ("◐", "#e5a00d", "#e5a00d"),
    PlanStepStatus.COMPLETED: ("✔", "#4ec9b0", "#4ec9b0"),
    PlanStepStatus.FAILED: ("✗", "#f44747", "#f44747"),
}

_BTN_STYLE = """
    QPushButton {{
        background: {bg}; color: {fg}; font-size: 11px; font-weight: 600;
        border: none; border-radius: 4px; padding: 3px 10px;
    }}
    QPushButton:hover {{ background: {hover}; }}
"""


class PlanStepCard(QWidget):
    """A single step in a plan — shows status, title, files, and action buttons."""

    approve_clicked = pyqtSignal(int)  # step index
    skip_clicked = pyqtSignal(int)
    retry_clicked = pyqtSignal(int)

    def __init__(self, step: PlanStep, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._step = step
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        icon, border_color, _ = _STATUS_STYLE.get(step.status, ("○", "#666", "#999"))

        self.setStyleSheet(
            f"QWidget#stepCard {{ background: #252526; border-left: 3px solid {border_color}; "
            f"border-radius: 6px; }}"
        )
        self.setObjectName("stepCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Row 1: status icon + step number + title + action buttons
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._status_icon = QLabel(icon)
        self._status_icon.setFixedWidth(18)
        self._status_icon.setStyleSheet(
            f"color: {_STATUS_STYLE[step.status][2]}; font-size: 14px; background: transparent;"
        )
        top_row.addWidget(self._status_icon)

        step_num = QLabel(f"{step.index + 1}.")
        step_num.setFixedWidth(20)
        step_num.setStyleSheet(
            "color: #888; font-size: 12px; font-weight: bold; background: transparent;"
        )
        top_row.addWidget(step_num)

        title_label = QLabel(step.title)
        title_label.setStyleSheet(
            "color: #e0e0e0; font-size: 13px; font-weight: 600; background: transparent;"
        )
        title_label.setWordWrap(True)
        top_row.addWidget(title_label, stretch=1)

        # Action buttons
        self._btn_container = QWidget()
        self._btn_container.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(self._btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        if step.status == PlanStepStatus.PENDING:
            approve_btn = QPushButton("Approve")
            approve_btn.setStyleSheet(
                _BTN_STYLE.format(bg="#0e4429", fg="#4ec9b0", hover="#1a5c3a")
            )
            approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            approve_btn.clicked.connect(lambda: self.approve_clicked.emit(step.index))
            btn_layout.addWidget(approve_btn)

            skip_btn = QPushButton("Skip")
            skip_btn.setStyleSheet(_BTN_STYLE.format(bg="#333", fg="#999", hover="#444"))
            skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            skip_btn.clicked.connect(lambda: self.skip_clicked.emit(step.index))
            btn_layout.addWidget(skip_btn)

        elif step.status == PlanStepStatus.FAILED:
            retry_btn = QPushButton("Retry")
            retry_btn.setStyleSheet(_BTN_STYLE.format(bg="#5c1a1a", fg="#f44747", hover="#7a2a2a"))
            retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            retry_btn.clicked.connect(lambda: self.retry_clicked.emit(step.index))
            btn_layout.addWidget(retry_btn)

        top_row.addWidget(self._btn_container)
        layout.addLayout(top_row)

        # Row 2: files affected (as chips)
        if step.files_affected:
            files_row = QHBoxLayout()
            files_row.setSpacing(4)
            files_row.setContentsMargins(26, 0, 0, 0)
            for f in step.files_affected[:5]:
                chip = QLabel(f)
                chip.setStyleSheet(
                    "background: #1a2733; color: #569cd6; font-size: 11px; "
                    "padding: 1px 6px; border-radius: 3px; font-family: monospace;"
                )
                files_row.addWidget(chip)
            files_row.addStretch()
            layout.addLayout(files_row)

        # Row 3: description (compact)
        if step.description and step.description != step.title:
            desc = QLabel(step.description[:150])
            desc.setWordWrap(True)
            desc.setStyleSheet(
                "color: #888; font-size: 11px; background: transparent; padding-left: 26px;"
            )
            layout.addWidget(desc)

        # Row 4: result (after execution)
        if step.result and step.status in (PlanStepStatus.COMPLETED, PlanStepStatus.FAILED):
            result_color = "#4ec9b0" if step.status == PlanStepStatus.COMPLETED else "#f44747"
            result = QLabel(step.result[:100])
            result.setWordWrap(True)
            result.setStyleSheet(
                f"color: {result_color}; font-size: 11px; background: transparent; "
                f"padding-left: 26px; font-style: italic;"
            )
            layout.addWidget(result)

    def update_step(self, step: PlanStep) -> None:
        """Update the step and refresh display."""
        self._step = step
        icon, border_color, text_color = _STATUS_STYLE.get(step.status, ("○", "#666", "#999"))
        self._status_icon.setText(icon)
        self._status_icon.setStyleSheet(
            f"color: {text_color}; font-size: 14px; background: transparent;"
        )
        self.setStyleSheet(
            f"QWidget#stepCard {{ background: #252526; border-left: 3px solid {border_color}; "
            f"border-radius: 6px; }}"
        )
