"""Tiny painted icons shared across panel header toolbars.

Originally extracted from ``git_panel.py`` (hence ``draw_refresh_icon``
and ``draw_branch_icon``); several other panels had each independently
reimplemented the same refresh/plus/pop-out glyphs, so those live here
too now — one definition per icon instead of one per panel.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from polyglot_ai.ui import theme_colors as tc


def draw_refresh_icon() -> QIcon:
    """Circular arrow refresh glyph, 16×16."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
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
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.6)
    p.setPen(pen)
    # Trunk
    p.drawLine(5, 3, 5, 13)
    # Branch
    p.drawLine(5, 7, 11, 10)
    p.drawLine(11, 10, 11, 13)
    # Node dots
    p.setBrush(QColor(tc.get("text_primary")))
    p.drawEllipse(3, 2, 4, 4)
    p.drawEllipse(3, 12, 4, 4)
    p.drawEllipse(9, 9, 4, 4)
    p.end()
    return QIcon(pm)


def draw_plus_icon() -> QIcon:
    """Plus glyph — 'new'/'add' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(2.0)
    p.setPen(pen)
    p.drawLine(8, 3, 8, 13)
    p.drawLine(3, 8, 13, 8)
    p.end()
    return QIcon(pm)


def draw_popout_icon() -> QIcon:
    """Box-with-arrow ↗ glyph — 'open in a separate window' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.drawRect(2, 5, 9, 9)
    p.drawLine(7, 9, 14, 2)
    p.drawLine(9, 2, 14, 2)
    p.drawLine(14, 2, 14, 7)
    p.end()
    return QIcon(pm)


def draw_trash_icon() -> QIcon:
    """Trash/bin glyph — 'erase'/'clear' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.5)
    p.setPen(pen)
    # Lid
    p.drawLine(3, 5, 13, 5)
    p.drawLine(6, 5, 6, 3)
    p.drawLine(6, 3, 10, 3)
    p.drawLine(10, 3, 10, 5)
    # Bin body
    p.drawLine(4, 5, 5, 14)
    p.drawLine(12, 5, 11, 14)
    p.drawLine(5, 14, 11, 14)
    # Vertical strokes
    p.drawLine(7, 7, 7, 12)
    p.drawLine(9, 7, 9, 12)
    p.end()
    return QIcon(pm)


def draw_package_icon() -> QIcon:
    """Open shipping-box glyph — 'browse starter templates' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.drawLine(2, 5, 8, 2)
    p.drawLine(8, 2, 14, 5)
    p.drawLine(14, 5, 8, 8)
    p.drawLine(8, 8, 2, 5)
    p.drawLine(2, 5, 2, 11)
    p.drawLine(2, 11, 8, 14)
    p.drawLine(8, 14, 14, 11)
    p.drawLine(14, 11, 14, 5)
    p.drawLine(8, 8, 8, 14)
    p.end()
    return QIcon(pm)


def draw_blank_page_icon() -> QIcon:
    """Empty page with a folded corner — 'start blank' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.4)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(4, 2)
    path.lineTo(10, 2)
    path.lineTo(13, 5)
    path.lineTo(13, 14)
    path.lineTo(4, 14)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(10, 2, 10, 5)
    p.drawLine(10, 5, 13, 5)
    p.end()
    return QIcon(pm)


def draw_folder_icon() -> QIcon:
    """Folder glyph — 'open existing project' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(2, 5, 12, 8), 1, 1)
    p.drawLine(2, 5, 2, 4)
    p.drawLine(2, 4, 6, 4)
    p.drawLine(6, 4, 7, 5)
    p.end()
    return QIcon(pm)


def draw_copy_icon() -> QIcon:
    """Two overlapping pages — 'copy to clipboard' affordance."""
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.3)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(5, 2, 9, 9), 1, 1)
    p.drawRoundedRect(QRectF(2, 5, 9, 9), 1, 1)
    p.end()
    return QIcon(pm)
