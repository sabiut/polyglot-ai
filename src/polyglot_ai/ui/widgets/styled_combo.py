"""Professional styled combo box with two-line items and checkmark."""

from __future__ import annotations

import tempfile
from typing import Any

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)


class TwoLineDelegate(QStyledItemDelegate):
    """Custom delegate that draws two-line items: name + description, with checkmark."""

    def __init__(self, combo: QComboBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._combo = combo

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_current = index.row() == self._combo.currentIndex()

        # Background
        if is_selected or is_hovered:
            painter.setBrush(QColor("#3e3e40"))
            painter.setPen(Qt.PenStyle.NoPen)
            bg_rect = rect.adjusted(4, 1, -4, -1)
            painter.drawRoundedRect(bg_rect, 6, 6)

        # Get data
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        description = index.data(Qt.ItemDataRole.UserRole + 1) or ""

        # Check if this is a separator/header
        is_header = text.startswith("──") or index.data(Qt.ItemDataRole.UserRole + 2)

        if is_header:
            # Draw as section header
            painter.setPen(QColor("#666666"))
            font = QFont("sans-serif", 8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                rect.adjusted(14, 0, 0, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            painter.restore()
            return

        # Name (first line)
        name_font = QFont("sans-serif", 9)
        name_font.setWeight(QFont.Weight.Medium)
        painter.setFont(name_font)
        painter.setPen(QColor("#e0e0e0"))

        if description:
            name_rect = QRect(rect.x() + 14, rect.y() + 5, rect.width() - 44, 18)
        else:
            name_rect = QRect(rect.x() + 14, rect.y(), rect.width() - 44, rect.height())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        # Description (second line)
        if description:
            desc_font = QFont("sans-serif", 8)
            painter.setFont(desc_font)
            painter.setPen(QColor("#777777"))
            desc_rect = QRect(rect.x() + 14, rect.y() + 22, rect.width() - 44, 16)
            painter.drawText(desc_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, description)

        # Checkmark for current selection
        if is_current:
            painter.setPen(QPen(QColor("#569cd6"), 2.0, cap=Qt.PenCapStyle.RoundCap))
            cx = rect.right() - 22
            cy = rect.y() + rect.height() // 2
            painter.drawLine(cx - 4, cy, cx - 1, cy + 3)
            painter.drawLine(cx - 1, cy + 3, cx + 5, cy - 4)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        is_header = text.startswith("──") or index.data(Qt.ItemDataRole.UserRole + 2)
        if is_header:
            return QSize(option.rect.width(), 24)
        description = index.data(Qt.ItemDataRole.UserRole + 1)
        if description:
            return QSize(option.rect.width(), 40)
        return QSize(option.rect.width(), 30)


class StyledComboBox(QComboBox):
    """A polished dark combo box with two-line items, descriptions, and checkmarks."""

    _DARK_PAL = None  # Shared palette

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(False)

        # Dark palette for everything
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.Text, QColor("#e0e0e0"))
        pal.setColor(QPalette.ColorRole.Window, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
        pal.setColor(QPalette.ColorRole.Button, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e0"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#3e3e40"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.Light, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.Midlight, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.Mid, QColor("#444444"))
        pal.setColor(QPalette.ColorRole.Dark, QColor("#2b2b2d"))
        pal.setColor(QPalette.ColorRole.Shadow, QColor("#1a1a1a"))
        self._pal = pal
        self.setPalette(pal)

        # Custom popup view
        popup = QListView()
        popup.setPalette(pal)
        popup.setStyleSheet("""
            QListView {
                background-color: #2b2b2d;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 10px;
                padding: 6px 2px;
                outline: none;
            }
            QListView::item {
                background: transparent;
                border: none;
            }
        """)
        self.setView(popup)

        # Two-line delegate
        self._delegate = TwoLineDelegate(self, popup)
        popup.setItemDelegate(self._delegate)

        # Arrow icon
        arrow_path = self._make_arrow()
        self.setStyleSheet(f"""
            QComboBox {{
                font-size: 13px;
                padding: 4px 28px 4px 12px;
                background-color: #3e3e40;
                color: #b0b0b0;
                border: none;
                border-radius: 8px;
            }}
            QComboBox:hover {{
                background-color: #4e4e50;
                color: #ffffff;
            }}
            QComboBox::drop-down {{
                border: none; width: 24px;
                subcontrol-origin: padding;
                subcontrol-position: center right;
            }}
            QComboBox::down-arrow {{
                image: url({arrow_path});
                width: 12px; height: 12px;
            }}
        """)

    def showPopup(self) -> None:
        """Override to force dark styling on the popup container every time it opens."""
        super().showPopup()
        # The popup is wrapped in a QFrame container — find and style it
        popup = self.view()
        if popup:
            popup.setPalette(self._pal)
            container = popup.window()
            if container and container is not self.window():
                container.setWindowFlags(
                    container.windowFlags()
                    | Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.NoDropShadowWindowHint
                )
                container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                container.setStyleSheet(
                    "background-color: #2b2b2d; border: 1px solid #444; border-radius: 10px;"
                )
                container.setPalette(self._pal)
                container.show()

    def addItemWithDesc(self, name: str, description: str = "", data: Any = None) -> None:
        """Add an item with a name and optional description line."""
        self.addItem(name, data)
        idx = self.count() - 1
        self.setItemData(idx, description, Qt.ItemDataRole.UserRole + 1)

    def addHeader(self, text: str) -> None:
        """Add a non-selectable section header."""
        self.addItem(text)
        idx = self.count() - 1
        self.setItemData(idx, True, Qt.ItemDataRole.UserRole + 2)
        # Make it non-selectable
        model = self.model()
        if model:
            item = model.item(idx)
            if item:
                item.setEnabled(False)

    @staticmethod
    def _make_arrow() -> str:
        cache_dir = tempfile.mkdtemp(prefix="codex_combo_")
        path = f"{cache_dir}/arrow.png"
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#aaaaaa"))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(2, 4, 6, 8)
        painter.drawLine(6, 8, 10, 4)
        painter.end()
        pixmap.save(path, "PNG")
        return path
