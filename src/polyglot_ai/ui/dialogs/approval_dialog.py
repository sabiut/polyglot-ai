"""Approval dialog for tool calls requiring user confirmation."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui.panels.diff_viewer import DiffViewer


class ApprovalDialog(QDialog):
    """Shows tool call details and asks for user approval.

    Layout strategy: only use the side-by-side :class:`DiffViewer`
    when there is an *actual* diff (``file_write`` and ``file_patch``).
    For deletes, directory operations, shell commands, and unknown
    tools, render a single full-width read-only :class:`QPlainTextEdit`
    so the dialog isn't half-empty and the user sees the relevant
    payload at full readable width.
    """

    def __init__(
        self,
        tool_name: str,
        arguments: str,
        current_content: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Approve: {tool_name}")
        self.setMinimumSize(700, 500)
        self.resize(900, 600)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._approved = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # ── Header: tool name ──
        header = QLabel(f"Tool: <b>{tool_name}</b>")
        header.setStyleSheet("font-size: 14px;")
        layout.addWidget(header)

        # Parse arguments once for every branch.
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {"raw": arguments}

        # ── Body: branch on tool kind ──
        body_widget = self._build_body(tool_name, args, current_content)
        layout.addWidget(body_widget, 1)

        # ── Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        reject_btn = QPushButton("Reject")
        reject_btn.setStyleSheet("background-color: #5a1d1d; padding: 8px 20px;")
        reject_btn.clicked.connect(self._reject)
        btn_layout.addWidget(reject_btn)

        approve_btn = QPushButton("Approve")
        approve_btn.setStyleSheet("background-color: #0e639c; padding: 8px 20px;")
        approve_btn.clicked.connect(self._approve)
        btn_layout.addWidget(approve_btn)

        layout.addLayout(btn_layout)

    # ── Body builders ──────────────────────────────────────────────

    def _build_body(
        self,
        tool_name: str,
        args: dict,
        current_content: str | None,
    ) -> QWidget:
        """Return the central widget for the dialog body.

        ``file_write`` / ``file_patch`` use the side-by-side diff
        viewer because they have a meaningful before/after. Every
        other tool uses a single full-width pane labelled with what
        the tool is about to do.
        """
        if tool_name in ("file_write", "file_patch"):
            return self._build_diff_body(args, current_content)
        if tool_name == "shell_exec":
            return self._build_shell_body(args)
        if tool_name == "file_delete":
            return self._build_file_delete_body(args, current_content)
        if tool_name == "dir_create":
            return self._build_dir_create_body(args)
        if tool_name == "dir_delete":
            return self._build_dir_delete_body(args, current_content)
        return self._build_generic_body(args)

    def _build_diff_body(self, args: dict, current_content: str | None) -> QWidget:
        path = args.get("path", "unknown")
        wrap, layout = self._wrap_with_label(f"File: <code>{path}</code>", colour="#9cdcfe")
        diff_viewer = DiffViewer()
        old_content = current_content or "(new file)"
        new_content = args.get("content", args.get("patch", ""))
        diff_viewer.set_diff(old_content, new_content)
        layout.addWidget(diff_viewer, 1)
        return wrap

    def _build_shell_body(self, args: dict) -> QWidget:
        command = args.get("command", "")
        workdir = args.get("workdir", "project root")
        wrap, layout = self._wrap_with_label(f"Command in <code>{workdir}</code>", colour="#9cdcfe")
        layout.addWidget(self._mono_pane(command), 1)
        return wrap

    def _build_file_delete_body(
        self,
        args: dict,
        current_content: str | None,
    ) -> QWidget:
        path = args.get("path", "unknown")
        wrap, layout = self._wrap_with_label(
            f"<b>Will permanently delete file:</b> <code>{path}</code>",
            colour="#f48771",
        )
        body = current_content if current_content is not None else "(file content unavailable)"
        editor = self._mono_pane(body)
        layout.addWidget(QLabel("File contents that will be lost:"), 0)
        layout.addWidget(editor, 1)
        return wrap

    def _build_dir_create_body(self, args: dict) -> QWidget:
        path = args.get("path", "unknown")
        wrap, layout = self._wrap_with_label(
            f"<b>Will create directory:</b> <code>{path}</code>",
            colour="#4ec9b0",
        )
        editor = self._mono_pane(
            f"{path}\n\nThe directory (and any missing parent directories) will be created."
        )
        layout.addWidget(editor, 1)
        return wrap

    def _build_dir_delete_body(
        self,
        args: dict,
        current_content: str | None,
    ) -> QWidget:
        path = args.get("path", "unknown")
        recursive = args.get("recursive", False)
        warn = (
            "Will RECURSIVELY delete directory and everything inside"
            if recursive
            else "Will delete directory"
        )
        wrap, layout = self._wrap_with_label(
            f"<b>{warn}:</b> <code>{path}</code>",
            colour="#f48771",
        )
        # ``current_content`` is repurposed by the chat panel to carry
        # a directory listing for dir_delete (top N entries plus a
        # truncation marker). When absent we fall back to the path.
        body = current_content if current_content is not None else f"{path}/"
        layout.addWidget(QLabel("Contents that will be removed:"), 0)
        layout.addWidget(self._mono_pane(body), 1)
        return wrap

    def _build_generic_body(self, args: dict) -> QWidget:
        wrap, layout = self._wrap_with_label("Tool arguments", colour="#9cdcfe")
        layout.addWidget(self._mono_pane(json.dumps(args, indent=2)), 1)
        return wrap

    # ── Body helpers ───────────────────────────────────────────────

    @staticmethod
    def _wrap_with_label(label_html: str, colour: str) -> tuple[QWidget, QVBoxLayout]:
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        info = QLabel(label_html)
        info.setStyleSheet(f"padding: 2px 0; color: {colour};")
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)
        return wrap, layout

    @staticmethod
    def _mono_pane(text: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(QFont("Monospace", 11))
        editor.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; }"
        )
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        editor.setPlainText(text)
        return editor

    # ── Buttons ────────────────────────────────────────────────────

    def _approve(self) -> None:
        self._approved = True
        self.accept()

    def _reject(self) -> None:
        self._approved = False
        self.reject()

    @property
    def approved(self) -> bool:
        return self._approved
