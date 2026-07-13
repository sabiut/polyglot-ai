"""Plan panel — dedicated tab for viewing and executing structured plans."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.ai.plan_models import Plan, PlanStatus, PlanStepStatus
from polyglot_ai.ui import theme, theme_colors as tc
from polyglot_ai.ui.widgets.plan_step_card import PlanStepCard

logger = logging.getLogger(__name__)


class PlanPanel(QWidget):
    """Dedicated panel for viewing and executing structured plans."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan: Plan | None = None
        self._step_cards: list[PlanStepCard] = []
        self._on_execute = None  # Callback set by chat panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header bar ──
        self._header = QWidget()
        self._header.setFixedHeight(44)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(16, 0, 16, 0)

        self._title = QLabel("PLAN")
        header_layout.addWidget(self._title)

        self._progress_label = QLabel("")
        header_layout.addWidget(self._progress_label)
        header_layout.addStretch()

        # Approve All button
        self._approve_btn = QPushButton("Approve All")
        self._approve_btn.setFixedHeight(28)
        self._approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_btn.clicked.connect(self._on_approve_all)
        self._approve_btn.hide()
        header_layout.addWidget(self._approve_btn)

        # Execute button
        self._execute_btn = QPushButton("▶ Execute")
        self._execute_btn.setFixedHeight(28)
        self._execute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._execute_btn.clicked.connect(self._on_execute_clicked)
        self._execute_btn.hide()
        header_layout.addWidget(self._execute_btn)

        # Pause button
        self._pause_btn = QPushButton("⏸ Pause")
        self._pause_btn.setFixedHeight(28)
        self._pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.hide()
        header_layout.addWidget(self._pause_btn)

        layout.addWidget(self._header)

        # ── Progress bar ──
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        # ── Content area ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(8)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Welcome message
        self._welcome = QLabel(
            "No active plan.\n\n"
            "Enable Plan mode and send a request to generate\n"
            "a structured implementation plan."
        )
        self._welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(self._welcome)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)

        self._apply_theme_styles()
        theme.connect_theme_changed(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        """(Re-)apply token-based styles to the static chrome."""
        self._header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        self._title.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; "
            "font-weight: bold; letter-spacing: 1px;"
        )
        self._progress_label.setStyleSheet(
            f"color: {tc.get('accent_success_muted')}; font-size: {tc.FONT_MD}px;"
        )
        self._approve_btn.setStyleSheet(f"""
            QPushButton {{
                background: {tc.get("bg_feedback_pos")}; color: {tc.get("accent_success_muted")};
                font-size: {tc.FONT_SM}px; font-weight: 600;
                border: none; border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: {tc.get("bg_feedback_pos_hover")}; }}
        """)
        self._execute_btn.setStyleSheet(f"""
            QPushButton {{
                background: {tc.get("accent_primary")}; color: white;
                font-size: {tc.FONT_SM}px; font-weight: 600;
                border: none; border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: {tc.get("accent_primary_hover")}; }}
        """)
        self._pause_btn.setStyleSheet(f"""
            QPushButton {{
                background: {tc.get("bg_feedback_warn")}; color: {tc.get("accent_warning")};
                font-size: {tc.FONT_SM}px; font-weight: 600;
                border: none; border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: {tc.get("bg_feedback_warn_hover")}; }}
        """)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {tc.get("bg_base")}; border: none; }}
            QProgressBar::chunk {{ background: {tc.get("accent_success_muted")}; }}
        """)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {tc.get("bg_base")}; }}
            QScrollBar:vertical {{
                background: transparent; width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {tc.get("scrollbar_thumb")}; border-radius: 4px; min-height: 30px;
            }}
        """)
        self._welcome.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_BASE}px; padding: 40px;"
        )

    def set_plan(self, plan: Plan) -> None:
        """Display a plan with its steps."""
        self._plan = plan
        self._welcome.hide()
        self._clear_steps()

        # Summary card
        summary_card = QWidget()
        summary_card.setStyleSheet(
            f"QWidget {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; border-radius: 8px; }}"
        )
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(14, 12, 14, 12)
        summary_layout.setSpacing(4)

        plan_title = QLabel(plan.title)
        plan_title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {tc.get('text_heading')}; "
            "background: transparent; border: none;"
        )
        plan_title.setWordWrap(True)
        summary_layout.addWidget(plan_title)

        if plan.summary:
            plan_summary = QLabel(plan.summary[:200])
            plan_summary.setWordWrap(True)
            plan_summary.setStyleSheet(
                f"font-size: {tc.FONT_MD}px; color: {tc.get('text_secondary')}; "
                "background: transparent; border: none;"
            )
            summary_layout.addWidget(plan_summary)

        stats = QLabel(f"{len(plan.steps)} steps · {plan.status.value}")
        stats.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_muted')}; "
            "background: transparent; border: none;"
        )
        summary_layout.addWidget(stats)

        self._content_layout.addWidget(summary_card)

        # Step cards
        for step in plan.steps:
            card = PlanStepCard(step)
            card.approve_clicked.connect(self._on_step_approve)
            card.skip_clicked.connect(self._on_step_skip)
            card.retry_clicked.connect(self._on_step_retry)
            self._step_cards.append(card)
            self._content_layout.addWidget(card)

        self._content_layout.addStretch()
        self._update_buttons()

    def update_plan(self) -> None:
        """Refresh UI for the current plan state."""
        if not self._plan:
            return

        # Update progress
        progress = self._plan.progress
        self._progress_label.setText(f"{self._plan.completed_count}/{self._plan.total_count} steps")
        self._progress_bar.setValue(int(progress * 100))
        self._progress_bar.show()

        # Update step cards
        for i, card in enumerate(self._step_cards):
            if i < len(self._plan.steps):
                card.update_step(self._plan.steps[i])

        self._update_buttons()

    def _update_buttons(self) -> None:
        if not self._plan:
            return

        is_draft = self._plan.status == PlanStatus.DRAFT
        is_approved = self._plan.status == PlanStatus.APPROVED
        is_executing = self._plan.status == PlanStatus.EXECUTING
        is_paused = self._plan.status == PlanStatus.PAUSED

        self._approve_btn.setVisible(is_draft)
        self._execute_btn.setVisible(is_draft or is_approved or is_paused)
        self._pause_btn.setVisible(is_executing)

        if is_paused:
            self._execute_btn.setText("▶ Resume")
        else:
            self._execute_btn.setText("▶ Execute")

    def _clear_steps(self) -> None:
        for card in self._step_cards:
            self._content_layout.removeWidget(card)
            card.deleteLater()
        self._step_cards.clear()
        # Remove all items except welcome
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def _on_approve_all(self) -> None:
        if self._plan:
            self._plan.approve_all()
            self.update_plan()

    def _on_execute_clicked(self) -> None:
        if self._plan:
            if self._plan.status == PlanStatus.DRAFT:
                self._plan.approve_all()
            if self._on_execute:
                self._on_execute(self._plan)

    def _on_pause(self) -> None:
        if self._plan and self._plan.status == PlanStatus.EXECUTING:
            self._plan.status = PlanStatus.PAUSED
            self._update_buttons()

    def _on_step_approve(self, index: int) -> None:
        if self._plan and index < len(self._plan.steps):
            self._plan.steps[index].status = PlanStepStatus.APPROVED
            self.update_plan()

    def _on_step_skip(self, index: int) -> None:
        if self._plan and index < len(self._plan.steps):
            self._plan.steps[index].status = PlanStepStatus.SKIPPED
            self.update_plan()

    def _on_step_retry(self, index: int) -> None:
        if self._plan and index < len(self._plan.steps):
            self._plan.steps[index].status = PlanStepStatus.APPROVED
            self.update_plan()
            if self._on_execute:
                self._on_execute(self._plan)
