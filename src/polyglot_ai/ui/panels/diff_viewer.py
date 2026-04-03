"""Side-by-side diff viewer for file approval flow."""

from __future__ import annotations

import difflib

from PyQt6.QtGui import QColor, QFont, QTextCharFormat
from PyQt6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class DiffViewer(QWidget):
    """Side-by-side diff viewer showing old (left) and new (right) content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter()

        # Left panel (original)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Original")
        left_label.setStyleSheet(
            "font-weight: bold; padding: 4px; background-color: #2d2d2d; color: #f44747;"
        )
        left_layout.addWidget(left_label)
        self._left_editor = QPlainTextEdit()
        self._left_editor.setReadOnly(True)
        self._left_editor.setFont(QFont("Monospace", 11))
        self._left_editor.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; border: none;"
        )
        left_layout.addWidget(self._left_editor)
        splitter.addWidget(left_container)

        # Right panel (proposed)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("Proposed")
        right_label.setStyleSheet(
            "font-weight: bold; padding: 4px; background-color: #2d2d2d; color: #4ec9b0;"
        )
        right_layout.addWidget(right_label)
        self._right_editor = QPlainTextEdit()
        self._right_editor.setReadOnly(True)
        self._right_editor.setFont(QFont("Monospace", 11))
        self._right_editor.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; border: none;"
        )
        right_layout.addWidget(self._right_editor)
        splitter.addWidget(right_container)

        # Sync scrolling
        self._left_editor.verticalScrollBar().valueChanged.connect(
            self._right_editor.verticalScrollBar().setValue
        )
        self._right_editor.verticalScrollBar().valueChanged.connect(
            self._left_editor.verticalScrollBar().setValue
        )

        layout.addWidget(splitter)

    def set_diff(self, old_content: str, new_content: str) -> None:
        """Display a diff between old and new content with line highlighting."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        # Generate unified diff for context
        list(difflib.unified_diff(old_lines, new_lines, lineterm=""))

        # Highlight lines
        self._left_editor.setPlainText(old_content)
        self._right_editor.setPlainText(new_content)

        # Apply highlighting
        self._highlight_removed(old_lines, new_lines)
        self._highlight_added(old_lines, new_lines)

    def _highlight_removed(self, old_lines: list[str], new_lines: list[str]) -> None:
        """Highlight removed lines in the left editor."""
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        cursor = self._left_editor.textCursor()
        fmt_removed = QTextCharFormat()
        fmt_removed.setBackground(QColor("#5a1d1d"))

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace"):
                for line_num in range(i1, i2):
                    block = self._left_editor.document().findBlockByNumber(line_num)
                    cursor.setPosition(block.position())
                    cursor.moveToEndOfBlock()
                    cursor.select(cursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt_removed)

    def _highlight_added(self, old_lines: list[str], new_lines: list[str]) -> None:
        """Highlight added lines in the right editor."""
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        cursor = self._right_editor.textCursor()
        fmt_added = QTextCharFormat()
        fmt_added.setBackground(QColor("#1a3a2a"))

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("insert", "replace"):
                for line_num in range(j1, j2):
                    block = self._right_editor.document().findBlockByNumber(line_num)
                    cursor.setPosition(block.position())
                    cursor.moveToEndOfBlock()
                    cursor.select(cursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt_added)

    def set_command_preview(self, command: str) -> None:
        """Display a command for approval (not a diff)."""
        self._left_editor.setPlainText("Command to execute:")
        self._right_editor.setPlainText(command)
