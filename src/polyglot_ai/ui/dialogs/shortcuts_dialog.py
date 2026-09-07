"""Help → Keyboard Shortcuts: every registered action, grouped by category."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.action_registry import ActionRegistry
from polyglot_ai.ui import theme_colors as tc


class ShortcutsDialog(QDialog):
    """Read-only table of actions and their keybindings.

    Reads from the same :class:`ActionRegistry` the command palette
    uses, so the two can never disagree about what a key does. Actions
    without a binding are listed too — they're reachable from the
    palette (Ctrl+Shift+P), which the footer points out.
    """

    def __init__(self, registry: ActionRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setMinimumSize(560, 520)
        self.resize(600, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter actions…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Category", "Action", "Shortcut"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setStyleSheet(
            f"QTableWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; font-size: {tc.FONT_SM}px; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_tertiary')}; padding: 4px 8px; border: none; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )
        layout.addWidget(self._table, 1)

        actions = sorted(registry.get_all(), key=lambda a: (a.category, a.label.lower()))
        self._table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            cat = QTableWidgetItem(action.category)
            cat.setForeground(QColor(tc.get("text_tertiary")))
            self._table.setItem(row, 0, cat)
            self._table.setItem(row, 1, QTableWidgetItem(action.label))
            key = QTableWidgetItem(action.shortcut or "—")
            key.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not action.shortcut:
                key.setForeground(QColor(tc.get("text_muted")))
            self._table.setItem(row, 2, key)
        self._table.resizeRowsToContents()

        footer = QHBoxLayout()
        hint = QLabel(
            "Actions without a shortcut are available from the Command Palette (Ctrl+Shift+P)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;")
        footer.addWidget(hint, 1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self._table.rowCount()):
            haystack = " ".join(
                (self._table.item(row, col).text() if self._table.item(row, col) else "")
                for col in range(3)
            ).lower()
            self._table.setRowHidden(row, bool(needle) and needle not in haystack)
