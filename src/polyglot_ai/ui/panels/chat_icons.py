"""Icon factories for the chat panel.

Pure functions that paint small QPixmaps programmatically. Extracted
from ``chat_panel.py`` so the panel file can focus on chat logic.

Two families live here:

* ``make_*`` — return ``QIcon`` objects for use with ``setIcon``.
* ``create_*_png`` — return a filesystem path to a cached PNG, for
  places that need a URL (e.g. CSS ``background-image``).

No dependency on ``ChatPanel``; safe to call before any panel exists.
"""

from __future__ import annotations

import math
import tempfile

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def make_unlock_icon() -> QIcon:
    """White open-padlock icon for the inactive bootstrap button."""
    pm = QPixmap(14, 14)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.setBrush(QColor(0, 0, 0, 0))
    # Lock body
    p.drawRoundedRect(2, 7, 10, 6, 1.5, 1.5)
    # Open shackle
    p.drawArc(4, 1, 6, 8, 0, 180 * 16)
    p.end()
    return QIcon(pm)


def make_lock_icon() -> QIcon:
    """White closed-padlock icon for the active bootstrap button."""
    pm = QPixmap(14, 14)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.setBrush(QColor(0, 0, 0, 0))
    # Lock body
    p.drawRoundedRect(2, 7, 10, 6, 1.5, 1.5)
    # Closed shackle
    p.drawArc(4, 2, 6, 8, 0, 180 * 16)
    p.end()
    return QIcon(pm)


def make_plus_icon() -> QIcon:
    """White plus icon for the new-conversation button."""
    pm = QPixmap(14, 14)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(1.8)
    p.setPen(pen)
    # Horizontal line
    p.drawLine(3, 7, 11, 7)
    # Vertical line
    p.drawLine(7, 3, 7, 11)
    p.end()
    return QIcon(pm)


def make_toolbar_icon(icon_type: str) -> QIcon:
    """Create white toolbar icons (plus, search, template)."""
    size = 20
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    if icon_type == "plus":
        # + icon
        painter.drawLine(10, 4, 10, 16)
        painter.drawLine(4, 10, 16, 10)
    elif icon_type == "search":
        # Magnifying glass icon
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawLine(12, 12, 17, 17)
    elif icon_type == "template":
        # Document/list icon (three horizontal lines with a corner fold)
        painter.drawLine(5, 5, 15, 5)
        painter.drawLine(5, 10, 15, 10)
        painter.drawLine(5, 15, 12, 15)

    painter.end()
    return QIcon(pixmap)


def make_send_icon() -> QIcon:
    """Up-arrow send icon (dark on light circle, like ChatGPT)."""
    size = 18
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1a1a1a"))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Up arrow: vertical line + chevron
    p.drawLine(9, 14, 9, 5)
    p.drawLine(9, 5, 5, 9)
    p.drawLine(9, 5, 13, 9)
    p.end()
    return QIcon(pixmap)


def make_menu_icon(icon_type: str) -> QIcon:
    """Small grey icon for the + menu (paperclip / folder / terminal / plug / gear)."""
    size = 18
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#b0b0b0"))
    pen.setWidthF(1.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if icon_type == "paperclip":
        path = QPainterPath()
        path.moveTo(QPointF(12, 4))
        path.cubicTo(QPointF(15, 1), QPointF(17, 4), QPointF(14, 7))
        path.lineTo(QPointF(7, 14))
        path.cubicTo(QPointF(3, 17), QPointF(1, 14), QPointF(4, 11))
        path.lineTo(QPointF(10, 5))
        painter.drawPath(path)
    elif icon_type == "folder":
        painter.drawRoundedRect(2, 5, 14, 10, 2, 2)
        painter.drawRoundedRect(2, 4, 6, 3, 1, 1)
    elif icon_type == "terminal":
        painter.drawRoundedRect(2, 3, 14, 12, 2, 2)
        painter.drawLine(5, 7, 8, 9)
        painter.drawLine(8, 9, 5, 11)
        painter.drawLine(10, 12, 14, 12)
    elif icon_type == "plug":
        # Plug/connector icon for MCP
        painter.drawLine(9, 2, 9, 6)
        painter.drawLine(6, 2, 6, 6)
        painter.drawRoundedRect(4, 6, 10, 5, 2, 2)
        painter.drawLine(7, 11, 7, 14)
        painter.drawLine(10, 11, 10, 14)
        painter.drawLine(5, 14, 12, 14)
    elif icon_type == "gear":
        painter.drawEllipse(QRectF(5, 5, 8, 8))
        for angle in range(0, 360, 45):
            r = 8.5
            x = 9 + r * math.cos(math.radians(angle))
            y = 9 + r * math.sin(math.radians(angle))
            painter.drawLine(9, 9, int(x), int(y))

    painter.end()
    return QIcon(pixmap)


def create_plus_png() -> str:
    """Write a white plus PNG to a temp dir and return its path.

    Used by stylesheets that need a file URL (not a QIcon).
    """
    cache_dir = tempfile.mkdtemp(prefix="codex_icons_")
    path = f"{cache_dir}/plus.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(8, 3, 8, 13)
    painter.drawLine(3, 8, 13, 8)
    painter.end()
    pixmap.save(path, "PNG")
    return path


def create_arrow_png() -> str:
    """Write a white down-chevron PNG to a temp dir and return its path."""
    cache_dir = tempfile.mkdtemp(prefix="codex_icons_")
    path = f"{cache_dir}/arrow.png"
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(2, 4, 6, 8)
    painter.drawLine(6, 8, 10, 4)
    painter.end()
    pixmap.save(path, "PNG")
    return path
