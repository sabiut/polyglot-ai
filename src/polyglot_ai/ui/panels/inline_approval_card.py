"""Inline tool-approval card rendered directly in the chat stream.

A framed card with a coloured edge so a pending decision stands out
from the surrounding transcript: what the AI wants to do (title), the
exact command or path (monospace), a one-line hint, and the
Details… / Reject / Approve buttons. Dangerous shell commands get a
warning edge and hint.

After the decision the buttons and hint collapse away and the card
becomes a compact record — "Approved" / "Rejected" with the same
detail line — so the transcript still shows what happened.
"""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


# Per-tool wording: (kind badge, title, detail). ``detail`` is the
# command / path shown in monospace under the title.
def _describe(tool_name: str, args: dict) -> tuple[str, str, str]:
    path = args.get("path", "")
    if tool_name == "file_write":
        return "FILE", "Write file", path
    if tool_name == "file_patch":
        return "FILE", "Patch file", path
    if tool_name == "file_delete":
        return "FILE", "Delete file", path
    if tool_name == "dir_create":
        return "FILE", "Create directory", path
    if tool_name == "dir_delete":
        recursive = args.get("recursive", False)
        return "FILE", "Delete directory (recursive)" if recursive else "Delete directory", path
    if tool_name == "shell_exec":
        return "SHELL", "Run command", args.get("command", "")
    if tool_name == "git_commit":
        return "GIT", "Commit", args.get("message", "")
    if tool_name == "aws_cli":
        return "AWS", "Run AWS command", "aws " + str(args.get("command", ""))
    return "TOOL", f"Run {tool_name}", ""


def _elide(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class InlineApprovalCard(QFrame):
    """An inline approval card that lives in the chat stream."""

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
        # Kept for the Details… escalation — the rich ApprovalDialog
        # renders a side-by-side diff / full command preview from it.
        self._tool_name = tool_name
        self._arguments = arguments
        self._current_content = current_content
        self._decided_already = False

        try:
            args: dict = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {"raw": arguments}

        kind, title, detail = _describe(tool_name, args)

        # Shell commands that the sandbox classifies as dangerous get
        # a warning edge, title and hint so the risk is visible at a
        # glance before the user clicks.
        self._dangerous = False
        if tool_name == "shell_exec":
            from polyglot_ai.core.sandbox import Sandbox

            self._dangerous = Sandbox.is_dangerous_command(args.get("command") or "")
        elif tool_name == "aws_cli":
            from polyglot_ai.core import aws_cli

            self._dangerous = aws_cli.classify(str(args.get("command") or "")) == "destructive"

        self.setObjectName("approvalCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_frame(tc.get("accent_warning") if self._dangerous else tc.get("accent_primary"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 10, 8)
        outer.setSpacing(4)

        # ── Header: badge + title ──
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        badge = QLabel(kind)
        badge.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; background: {tc.get('bg_surface_overlay')}; "
            f"border: 1px solid {tc.get('border_secondary')}; border-radius: 3px; "
            f"padding: 0 5px; font-size: {tc.FONT_XS}px; font-weight: 700; letter-spacing: 0.5px;"
        )
        head.addWidget(badge)

        title_colour = tc.get("accent_warning") if self._dangerous else tc.get("text_heading")
        self._label = QLabel(title)
        self._label.setStyleSheet(
            f"color: {title_colour}; font-size: {tc.FONT_MD}px; font-weight: 600; "
            "background: transparent;"
        )
        head.addWidget(self._label, 1)
        outer.addLayout(head)

        # ── Detail: the command / path in monospace ──
        self._detail = QLabel(_elide(detail))
        self._detail.setStyleSheet(
            f"color: {tc.get('text_primary')}; background: {tc.get('bg_inline_code')}; "
            f"border-radius: 3px; padding: 3px 6px; font-size: {tc.FONT_SM}px; "
            "font-family: 'JetBrains Mono', 'Fira Code', monospace;"
        )
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setVisible(bool(detail))
        outer.addWidget(self._detail)

        # ── Footer: hint + buttons ──
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 2, 0, 0)
        foot.setSpacing(6)

        hint_text = (
            "Potentially destructive — review before approving."
            if self._dangerous
            else "Needs your approval."
        )
        self._hint = QLabel(hint_text)
        self._hint.setStyleSheet(
            f"color: {tc.get('accent_warning') if self._dangerous else tc.get('text_muted')}; "
            f"font-size: {tc.FONT_XS}px; background: transparent;"
        )
        foot.addWidget(self._hint, 1)

        btn_style = (
            "QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 3px 12px; font-size: {tc.FONT_SM}px; "
            "font-weight: 600; }}"
            "QPushButton:hover {{ background: {hover}; }}"
        )

        self._details_btn = QPushButton("Details…")
        self._details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._details_btn.setToolTip("Preview the full change before deciding")
        self._details_btn.setStyleSheet(
            btn_style.format(
                bg="transparent",
                fg=tc.get("text_secondary"),
                border=tc.get("border_input"),
                hover=tc.get("bg_hover"),
            )
        )
        self._details_btn.clicked.connect(self._show_details)
        foot.addWidget(self._details_btn)

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reject_btn.setStyleSheet(
            btn_style.format(
                bg="transparent",
                fg=tc.get("accent_error"),
                border=tc.get("border_feedback_neg"),
                hover=tc.get("bg_feedback_neg"),
            )
        )
        self._reject_btn.clicked.connect(lambda: self._finalise(False))
        foot.addWidget(self._reject_btn)

        self._approve_btn = QPushButton("Approve")
        self._approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_btn.setDefault(True)
        self._approve_btn.setStyleSheet(
            btn_style.format(
                bg=tc.get("accent_primary"),
                fg=tc.get("text_on_accent"),
                border=tc.get("accent_primary"),
                hover=tc.get("accent_primary_hover"),
            )
        )
        self._approve_btn.clicked.connect(lambda: self._finalise(True))
        foot.addWidget(self._approve_btn)
        outer.addLayout(foot)

    def _apply_frame(self, edge_colour: str, *, muted: bool = False) -> None:
        bg = tc.get("bg_surface") if muted else tc.get("bg_card")
        self.setStyleSheet(
            f"#approvalCard {{ background: {bg}; border: 1px solid {tc.get('border_card')}; "
            f"border-left: 3px solid {edge_colour}; border-radius: 6px; }}"
        )

    # ── Decision plumbing ──────────────────────────────────────────

    def _show_details(self) -> None:
        """Escalate to the rich ApprovalDialog (diff / command preview).

        The dialog's Approve/Reject buttons resolve this card too;
        merely closing the preview leaves the card pending.
        """
        if self._decided_already:
            return
        from polyglot_ai.ui.dialogs.approval_dialog import ApprovalDialog

        dlg = ApprovalDialog(self._tool_name, self._arguments, self._current_content, self.window())
        dlg.exec()
        if dlg.explicitly_decided:
            self._finalise(dlg.approved)

    def _finalise(self, approved: bool) -> None:
        """Collapse to a compact decision record."""
        if self._decided_already:
            return
        self._decided_already = True
        self._approve_btn.hide()
        self._reject_btn.hide()
        self._details_btn.hide()
        self._hint.hide()
        colour = tc.get("accent_success_muted") if approved else tc.get("accent_error")
        mark = "✓" if approved else "✗"
        word = "Approved" if approved else "Rejected"
        self._label.setText(f"{mark} {word} — {self._label.text()}")
        self._label.setStyleSheet(
            f"color: {colour}; font-size: {tc.FONT_MD}px; font-weight: 600; "
            "background: transparent;"
        )
        self._apply_frame(colour, muted=True)
        self.decided.emit(approved)

    def force_decision(self, approved: bool) -> None:
        """Programmatically resolve the card (e.g. on conversation switch)."""
        self._finalise(approved)
