"""Inline tool-approval row rendered directly in the chat stream.

A minimalist row that matches the existing tool-status label style:
one short italic gray description (e.g. ``Delete file: app.py``)
and two small Approve / Reject buttons next to it. No frame, no
border, no body preview — just enough for the user to see what
the AI wants to do and click yes or no without leaving the chat.

After the decision, the buttons are replaced with a small status
suffix (``— Approved`` / ``— Rejected``) so the transcript still
records what happened.
"""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

logger = logging.getLogger(__name__)


# Per-tool wording for the inline description. Keep it short — this
# is a single italic gray line, not a card. Anything not listed here
# falls through to the generic ``Run <tool_name>`` form.
def _describe(tool_name: str, args: dict) -> str:
    path = args.get("path", "")
    if tool_name == "file_write":
        return f"Write file: {path}" if path else "Write file"
    if tool_name == "file_patch":
        return f"Patch file: {path}" if path else "Patch file"
    if tool_name == "file_delete":
        return f"Delete file: {path}" if path else "Delete file"
    if tool_name == "dir_create":
        return f"Create directory: {path}" if path else "Create directory"
    if tool_name == "dir_delete":
        recursive = args.get("recursive", False)
        verb = "Delete directory (recursive)" if recursive else "Delete directory"
        return f"{verb}: {path}" if path else verb
    if tool_name == "shell_exec":
        command = args.get("command", "")
        if len(command) > 80:
            command = command[:77] + "…"
        return f"Run: {command}" if command else "Run command"
    if tool_name == "git_commit":
        message = args.get("message", "")
        if len(message) > 60:
            message = message[:57] + "…"
        return f"Commit: {message}" if message else "Commit"
    return f"Run {tool_name}"


class InlineApprovalCard(QWidget):
    """A tiny inline approval row that lives in the chat stream.

    The class name is kept for backwards compat with the chat panel
    wiring even though this is no longer a "card" — just a single
    horizontal row.
    """

    #: Emitted exactly once with the user's decision (True=approve).
    decided = pyqtSignal(bool)

    def __init__(
        self,
        tool_name: str,
        arguments: str,
        current_content: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        del current_content  # not used in the minimalist row design
        self._decided_already = False

        try:
            args: dict = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {"raw": arguments}

        description = _describe(tool_name, args)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(8)

        self._label = QLabel(f"  {description}")
        self._label.setStyleSheet(
            "color: #888; font-size: 12px; font-style: italic; "
            "padding: 2px 0; background: transparent;"
        )
        self._label.setWordWrap(True)
        row.addWidget(self._label, 1)

        # Compact buttons styled to fit the chat aesthetic — small,
        # subtle, no frame around them.
        btn_style = (
            "QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border}; "
            "border-radius: 3px; padding: 2px 10px; font-size: 11px; "
            "font-weight: 600; }}"
            "QPushButton:hover {{ background: {hover}; }}"
        )

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reject_btn.setStyleSheet(
            btn_style.format(
                bg="transparent",
                fg="#f48771",
                border="#5a1d1d",
                hover="#3c1a1a",
            )
        )
        self._reject_btn.clicked.connect(lambda: self._finalise(False))
        row.addWidget(self._reject_btn)

        self._approve_btn = QPushButton("Approve")
        self._approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_btn.setDefault(True)
        self._approve_btn.setStyleSheet(
            btn_style.format(
                bg="#0e639c",
                fg="white",
                border="#0e639c",
                hover="#1a8ae8",
            )
        )
        self._approve_btn.clicked.connect(lambda: self._finalise(True))
        row.addWidget(self._approve_btn)

    # ── Decision plumbing ──────────────────────────────────────────

    def _finalise(self, approved: bool) -> None:
        """Hide the buttons and append a status suffix to the label."""
        if self._decided_already:
            return
        self._decided_already = True
        self._approve_btn.hide()
        self._reject_btn.hide()
        suffix_colour = "#4ec9b0" if approved else "#f48771"
        suffix = "Approved" if approved else "Rejected"
        # Re-render the label with the existing description plus a
        # coloured suffix so the transcript still shows what was
        # decided after the fact.
        existing = self._label.text().strip()
        self._label.setText(f"  {existing} — <span style='color: {suffix_colour};'>{suffix}</span>")
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self.decided.emit(approved)

    def force_decision(self, approved: bool) -> None:
        """Programmatically resolve the row (e.g. on conversation switch)."""
        self._finalise(approved)
