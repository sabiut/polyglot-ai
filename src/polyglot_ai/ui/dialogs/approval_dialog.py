"""Approval dialog for tool calls requiring user confirmation."""

from __future__ import annotations

import json

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui.panels.diff_viewer import DiffViewer


class ApprovalDialog(QDialog):
    """Shows tool call details and asks for user approval."""

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

        self._approved = False

        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"Tool: <b>{tool_name}</b>")
        header.setStyleSheet("font-size: 14px; padding: 8px;")
        layout.addWidget(header)

        # Parse arguments
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {"raw": arguments}

        # Diff viewer or command preview
        self._diff_viewer = DiffViewer()

        if tool_name in ("file_write", "file_patch"):
            path = args.get("path", "unknown")
            info = QLabel(f"File: <code>{path}</code>")
            info.setStyleSheet("padding: 4px 8px;")
            layout.addWidget(info)

            old_content = current_content or "(new file)"
            new_content = args.get("content", args.get("patch", ""))
            self._diff_viewer.set_diff(old_content, new_content)
        elif tool_name == "shell_exec":
            command = args.get("command", "")
            workdir = args.get("workdir", "project root")
            info = QLabel(f"Working directory: <code>{workdir}</code>")
            info.setStyleSheet("padding: 4px 8px;")
            layout.addWidget(info)
            self._diff_viewer.set_command_preview(command)
        else:
            # Generic argument display
            self._diff_viewer.set_command_preview(json.dumps(args, indent=2))

        layout.addWidget(self._diff_viewer)

        # Buttons
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

    def _approve(self) -> None:
        self._approved = True
        self.accept()

    def _reject(self) -> None:
        self._approved = False
        self.reject()

    @property
    def approved(self) -> bool:
        return self._approved
