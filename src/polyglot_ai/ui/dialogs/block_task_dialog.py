"""Small modal that captures a reason when blocking a task.

The task manager's :meth:`block_task` refuses an empty reason — this
dialog is the UI counterpart. Called from the Tasks sidebar's
context menu and the task detail dialog's state-change buttons so
the user can't silently move a task to BLOCKED without saying why.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


class BlockTaskDialog(QDialog):
    """Prompt for a blocker reason. Returns the trimmed text or ``""``."""

    def __init__(
        self,
        task_title: str,
        current_reason: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Block task")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"QDialog {{ background: {tc.get('bg_base')}; }}")
        self.setModal(True)

        self._reason: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        header = QLabel(f"Block <b>{task_title}</b>")
        header.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_LG}px; font-weight: 600; background: transparent;"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        hint = QLabel(
            "What's blocking it? The reason shows on the card and in the "
            "task timeline so future-you (or the rest of your team) can "
            "tell at a glance what's needed to unblock it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        layout.addWidget(hint)

        self._reason_edit = QPlainTextEdit()
        self._reason_edit.setPlaceholderText("e.g. waiting on API contract from backend team")
        self._reason_edit.setPlainText(current_reason)
        self._reason_edit.setMaximumHeight(120)
        self._reason_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_secondary')}; border-radius: 4px; "
            f"padding: 7px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QPlainTextEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._reason_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._reason_edit)

        # Action row.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('text_disabled')}; border-radius: 4px; "
            f"padding: 6px 14px; font-size: {tc.FONT_MD}px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        self._confirm = QPushButton("Block task")
        self._confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm.setDefault(True)
        # Disabled until the user types a non-empty reason so the
        # dialog can't accidentally submit an empty string.
        self._confirm.setEnabled(bool(current_reason.strip()))
        self._confirm.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_warning')}; color: {tc.get('text_on_accent')}; border: none; "
            f"border-radius: 4px; padding: 6px 18px; font-size: {tc.FONT_MD}px; "
            f"font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_warning_hover')}; }}"
            f"QPushButton:disabled {{ background: {tc.get('bg_hover')}; color: {tc.get('text_tertiary')}; }}"
        )
        self._confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm)
        layout.addLayout(btn_row)

        self._reason_edit.setFocus()

    # ── Handlers ───────────────────────────────────────────────────

    def _on_text_changed(self) -> None:
        self._confirm.setEnabled(bool(self._reason_edit.toPlainText().strip()))

    def _on_confirm(self) -> None:
        reason = self._reason_edit.toPlainText().strip()
        if not reason:
            # Should never fire because the button is disabled on
            # empty — belt-and-braces.
            return
        self._reason = reason
        self.accept()

    # ── Public accessor ────────────────────────────────────────────

    @property
    def reason(self) -> str:
        return self._reason
