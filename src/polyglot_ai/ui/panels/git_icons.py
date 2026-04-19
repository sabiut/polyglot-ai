"""Tiny painted icons used by the git panel.

Extracted from ``git_panel.py``. Kept separate so the panel file
focuses on the controller logic (refresh polling, branch commands,
push flow) rather than pixmap paint code.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def draw_refresh_icon() -> QIcon:
    """Circular arrow refresh glyph, 16×16."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#cccccc"))
    pen.setWidthF(1.6)
    p.setPen(pen)
    p.drawArc(QRectF(3, 3, 10, 10), 60 * 16, 280 * 16)
    p.drawLine(12, 2, 12, 6)
    p.drawLine(12, 6, 8, 6)
    p.end()
    return QIcon(pm)


def draw_branch_icon() -> QIcon:
    """Simple Git-style branch glyph: two parallel dots joined by a fork."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#cccccc"))
    pen.setWidthF(1.6)
    p.setPen(pen)
    # Trunk
    p.drawLine(5, 3, 5, 13)
    # Branch
    p.drawLine(5, 7, 11, 10)
    p.drawLine(11, 10, 11, 13)
    # Node dots
    p.setBrush(QColor("#cccccc"))
    p.drawEllipse(3, 2, 4, 4)
    p.drawEllipse(3, 12, 4, 4)
    p.drawEllipse(9, 9, 4, 4)
    p.end()
    return QIcon(pm)
