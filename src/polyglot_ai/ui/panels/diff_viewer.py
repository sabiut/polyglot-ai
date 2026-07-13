"""Side-by-side diff viewer for file approval flow."""

from __future__ import annotations

import difflib

from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


class DiffViewer(QWidget):
    """Side-by-side diff viewer showing old (left) and new (right) content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Make the viewer claim every spare vertical pixel its parent
        # gives it. Without this the outer QWidget keeps the default
        # ``Preferred/Preferred`` size policy and the parent dialog
        # leaves unused space scattered around the diff instead of
        # growing it. Same goes for horizontal expansion.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter()

        # Left panel (original)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Original")
        left_label.setStyleSheet(
            f"font-weight: bold; padding: 4px; "
            f"background-color: {tc.get('bg_surface_raised')}; color: {tc.get('diff_del_fg')};"
        )
        left_layout.addWidget(left_label)
        self._left_editor = QPlainTextEdit()
        self._left_editor.setReadOnly(True)
        self._left_editor.setFont(QFont("Monospace", 11))
        self._left_editor.setStyleSheet(
            f"background-color: {tc.get('bg_base')}; color: {tc.get('text_primary')}; border: none;"
        )
        left_layout.addWidget(self._left_editor)
        splitter.addWidget(left_container)

        # Right panel (proposed)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("Proposed")
        right_label.setStyleSheet(
            f"font-weight: bold; padding: 4px; "
            f"background-color: {tc.get('bg_surface_raised')}; color: {tc.get('diff_add_fg')};"
        )
        right_layout.addWidget(right_label)
        self._right_editor = QPlainTextEdit()
        self._right_editor.setReadOnly(True)
        self._right_editor.setFont(QFont("Monospace", 11))
        self._right_editor.setStyleSheet(
            f"background-color: {tc.get('bg_base')}; color: {tc.get('text_primary')}; border: none;"
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

        # Stretch=1 so the splitter (and the QPlainTextEdits inside it)
        # actually fills the DiffViewer top-to-bottom.
        layout.addWidget(splitter, 1)

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
        fmt_removed.setBackground(QColor(tc.get("bg_diff_del")))

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace"):
                for line_num in range(i1, i2):
                    block = self._left_editor.document().findBlockByNumber(line_num)
                    cursor.setPosition(block.position())
                    # ``select(LineUnderCursor)`` already spans the
                    # whole logical line; no need for an explicit
                    # move-to-end first. (The old code called
                    # ``moveToEndOfBlock`` which is a Qt 5 / PyQt5
                    # name and doesn't exist in PyQt6.)
                    cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt_removed)

    def _highlight_added(self, old_lines: list[str], new_lines: list[str]) -> None:
        """Highlight added lines in the right editor."""
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        cursor = self._right_editor.textCursor()
        fmt_added = QTextCharFormat()
        fmt_added.setBackground(QColor(tc.get("bg_diff_add")))

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("insert", "replace"):
                for line_num in range(j1, j2):
                    block = self._right_editor.document().findBlockByNumber(line_num)
                    cursor.setPosition(block.position())
                    cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt_added)

    def set_command_preview(self, command: str, label: str = "Command to execute:") -> None:
        """Display a command (or any action body) for approval.

        ``label`` is shown in the left pane and ``command`` in the
        right. The default label is kept for backwards compatibility
        with shell-style approvals; pass a tool-specific phrase
        ("File to delete:", "Directory to create:", etc.) for other
        tools so the dialog framing matches the action.
        """
        self._left_editor.setPlainText(label)
        self._right_editor.setPlainText(command)
