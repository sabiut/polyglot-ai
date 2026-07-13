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
from polyglot_ai.ui import theme_colors as tc

# Status → (icon, border color token, text color token)
_STATUS_STYLE = {
    PlanStepStatus.PENDING: ("○", "plan_pending", "text_secondary"),
    PlanStepStatus.APPROVED: ("◉", "plan_approved", "plan_approved"),
    PlanStepStatus.SKIPPED: ("⊘", "plan_skipped", "text_muted"),
    PlanStepStatus.IN_PROGRESS: ("◐", "plan_in_progress", "plan_in_progress"),
    PlanStepStatus.COMPLETED: ("✔", "plan_completed", "plan_completed"),
    PlanStepStatus.FAILED: ("✗", "plan_failed", "plan_failed"),
}

_BTN_STYLE = """
    QPushButton {{
        background: {bg}; color: {fg}; font-size: {fs}px; font-weight: 600;
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

        icon, border_token, _ = _STATUS_STYLE.get(
            step.status, ("○", "plan_pending", "text_secondary")
        )
        border_color = tc.get(border_token)

        self.setStyleSheet(
            f"QWidget#stepCard {{ background: {tc.get('bg_surface')}; "
            f"border-left: 3px solid {border_color}; border-radius: 6px; }}"
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
            f"color: {tc.get(_STATUS_STYLE[step.status][2])}; font-size: {tc.FONT_LG}px; "
            f"background: transparent;"
        )
        top_row.addWidget(self._status_icon)

        step_num = QLabel(f"{step.index + 1}.")
        step_num.setFixedWidth(20)
        step_num.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_MD}px; "
            f"font-weight: bold; background: transparent;"
        )
        top_row.addWidget(step_num)

        title_label = QLabel(step.title)
        title_label.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_BASE}px; "
            f"font-weight: 600; background: transparent;"
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
                _BTN_STYLE.format(
                    bg=tc.get("bg_feedback_pos"),
                    fg=tc.get("accent_success_muted"),
                    hover="#1a5c3a",
                    fs=tc.FONT_SM,
                )
            )
            approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            approve_btn.clicked.connect(lambda: self.approve_clicked.emit(step.index))
            btn_layout.addWidget(approve_btn)

            skip_btn = QPushButton("Skip")
            skip_btn.setStyleSheet(
                _BTN_STYLE.format(
                    bg=tc.get("border_secondary"),
                    fg=tc.get("text_secondary"),
                    hover=tc.get("border_menu"),
                    fs=tc.FONT_SM,
                )
            )
            skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            skip_btn.clicked.connect(lambda: self.skip_clicked.emit(step.index))
            btn_layout.addWidget(skip_btn)

        elif step.status == PlanStepStatus.FAILED:
            retry_btn = QPushButton("Retry")
            retry_btn.setStyleSheet(
                _BTN_STYLE.format(
                    bg=tc.get("bg_feedback_neg"),
                    fg=tc.get("accent_error"),
                    hover="#7a2a2a",
                    fs=tc.FONT_SM,
                )
            )
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
                    f"background: #1a2733; color: {tc.get('accent_info')}; "
                    f"font-size: {tc.FONT_SM}px; "
                    f"padding: 1px 6px; border-radius: 3px; font-family: monospace;"
                )
                files_row.addWidget(chip)
            files_row.addStretch()
            layout.addLayout(files_row)

        # Row 3: description (compact)
        if step.description and step.description != step.title:
            desc = QLabel(step.description[:150])
            desc.setWordWrap(True)
            desc.setStyleSheet(
                f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; "
                f"background: transparent; padding-left: 26px;"
            )
            layout.addWidget(desc)

        # Row 4: result (after execution)
        if step.result and step.status in (PlanStepStatus.COMPLETED, PlanStepStatus.FAILED):
            result_color = tc.get(
                "plan_completed" if step.status == PlanStepStatus.COMPLETED else "plan_failed"
            )
            result = QLabel(step.result[:100])
            result.setWordWrap(True)
            result.setStyleSheet(
                f"color: {result_color}; font-size: {tc.FONT_SM}px; background: transparent; "
                f"padding-left: 26px; font-style: italic;"
            )
            layout.addWidget(result)

    def update_step(self, step: PlanStep) -> None:
        """Update the step and refresh display."""
        self._step = step
        icon, border_token, text_token = _STATUS_STYLE.get(
            step.status, ("○", "plan_pending", "text_secondary")
        )
        self._status_icon.setText(icon)
        self._status_icon.setStyleSheet(
            f"color: {tc.get(text_token)}; font-size: {tc.FONT_LG}px; background: transparent;"
        )
        self.setStyleSheet(
            f"QWidget#stepCard {{ background: {tc.get('bg_surface')}; "
            f"border-left: 3px solid {tc.get(border_token)}; border-radius: 6px; }}"
        )
