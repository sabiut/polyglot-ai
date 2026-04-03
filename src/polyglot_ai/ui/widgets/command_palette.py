"""Command palette — Ctrl+Shift+P quick-access dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.action_registry import ActionRegistry
from polyglot_ai.ui import theme_colors as tc


class CommandPalette(QDialog):
    """VS Code-style command palette overlay."""

    def __init__(self, registry: ActionRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._registry = registry
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(560)
        self.setMaximumHeight(400)

        self._setup_ui()
        self._populate("")

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        container = QWidget()
        container.setObjectName("palette_container")
        container.setStyleSheet(f"""
            #palette_container {{
                background: {tc.get("bg_surface")};
                border: 1px solid {tc.get("border_primary")};
                border-radius: {tc.RADIUS_MD}px;
            }}
        """)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(4)

        # Search input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command...")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {tc.get("bg_input")}; color: {tc.get("text_heading")};
                border: 1px solid {tc.get("border_input")};
                border-radius: {tc.RADIUS_SM}px; padding: 8px 12px;
                font-size: {tc.FONT_LG}px;
            }}
            QLineEdit:focus {{ border: 1px solid {tc.get("border_focus")}; }}
        """)
        self._input.textChanged.connect(self._on_filter_changed)
        inner.addWidget(self._input)

        # Results list
        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {tc.get("bg_surface")}; border: none;
                color: {tc.get("text_primary")}; font-size: {tc.FONT_BASE}px; outline: none;
            }}
            QListWidget::item {{
                padding: 6px 12px; border: none; border-radius: 3px;
            }}
            QListWidget::item:selected {{
                background: {tc.get("bg_active")}; color: {tc.get("text_on_accent")};
            }}
            QListWidget::item:hover:!selected {{
                background: {tc.get("bg_hover_subtle")};
            }}
        """)
        self._list.itemActivated.connect(self._on_item_activated)
        inner.addWidget(self._list)

        layout.addWidget(container)

    def _populate(self, query: str) -> None:
        self._list.clear()
        actions = self._registry.search(query)
        for action in actions[:30]:
            text = action.label
            if action.shortcut:
                text = f"{action.label}    {action.shortcut}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, action.action_id)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_filter_changed(self, text: str) -> None:
        self._populate(text)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        action_id = item.data(Qt.ItemDataRole.UserRole)
        self.close()
        self._registry.execute(action_id)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self._list.currentItem()
            if current:
                self._on_item_activated(current)
        elif key == Qt.Key.Key_Down:
            row = self._list.currentRow()
            if row < self._list.count() - 1:
                self._list.setCurrentRow(row + 1)
        elif key == Qt.Key.Key_Up:
            row = self._list.currentRow()
            if row > 0:
                self._list.setCurrentRow(row - 1)
        else:
            super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._input.setFocus()
        self._input.clear()
        self._populate("")
        # Center horizontally in parent
        if self.parent():
            parent = self.parent()
            px = parent.mapToGlobal(parent.rect().center())
            self.move(px.x() - self.width() // 2, px.y() - self.height() // 2 - 50)
