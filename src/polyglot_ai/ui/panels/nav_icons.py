"""Tiny painted icons for primary navigation — the right-hand tab bar
(main_window.py) and the Settings sidebar (settings_dialog.py).

Replaces emoji glyphs previously used for these labels, matching the
16x16 line-art style already established in git_icons.py.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from polyglot_ai.ui import theme_colors as tc


def _new_painter() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(1.6)
    p.setPen(pen)
    return pm, p


def make_chat_icon() -> QIcon:
    """Speech-bubble glyph."""
    pm, p = _new_painter()
    path = QPainterPath()
    path.moveTo(3, 3)
    path.lineTo(13, 3)
    path.quadTo(14, 3, 14, 4.5)
    path.lineTo(14, 9.5)
    path.quadTo(14, 11, 13, 11)
    path.lineTo(6.5, 11)
    path.lineTo(4, 13.5)
    path.lineTo(4, 11)
    path.lineTo(3, 11)
    path.quadTo(2, 11, 2, 9.5)
    path.lineTo(2, 4.5)
    path.quadTo(2, 3, 3, 3)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def make_plan_icon() -> QIcon:
    """Ordered plan steps — three bullet+line rows."""
    pm, p = _new_painter()
    for y in (4, 8, 12):
        p.drawEllipse(QRectF(2, y - 1, 2, 2))
        p.drawLine(6, y, 14, y)
    p.end()
    return QIcon(pm)


def make_changes_icon() -> QIcon:
    """A page with a small pencil badge in the corner — pending edits."""
    pm, p = _new_painter()
    p.drawRoundedRect(QRectF(2, 2, 8.5, 12), 1, 1)
    p.drawLine(QPointF(4, 5.5), QPointF(8.5, 5.5))
    p.drawLine(QPointF(4, 8.5), QPointF(8.5, 8.5))
    p.drawLine(QPointF(4, 11.5), QPointF(6.5, 11.5))
    # Small pencil badge, contained in the bottom-right corner
    p.drawLine(QPointF(10.5, 14), QPointF(14.5, 10))
    p.drawLine(QPointF(14.5, 10), QPointF(13, 8.5))
    p.drawLine(QPointF(13, 8.5), QPointF(9, 12.5))
    p.drawLine(QPointF(9, 12.5), QPointF(10.5, 14))
    p.end()
    return QIcon(pm)


def make_review_icon() -> QIcon:
    """Eye glyph — inspection / review."""
    pm, p = _new_painter()
    path = QPainterPath()
    path.moveTo(2, 8)
    path.quadTo(8, 2.5, 14, 8)
    path.quadTo(8, 13.5, 2, 8)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QRectF(6, 6, 4, 4))
    p.end()
    return QIcon(pm)


def make_usage_icon() -> QIcon:
    """Bar-chart glyph."""
    pm, p = _new_painter()
    p.drawLine(2, 13, 14, 13)
    p.drawRect(QRectF(3, 9, 2.5, 4))
    p.drawRect(QRectF(7, 6, 2.5, 7))
    p.drawRect(QRectF(11, 3, 2.5, 10))
    p.end()
    return QIcon(pm)


def make_cicd_icon() -> QIcon:
    """Pipeline glyph — three stages on a track."""
    pm, p = _new_painter()
    p.drawLine(2, 8, 14, 8)
    p.setBrush(QColor(tc.get("text_primary")))
    p.drawEllipse(QPointF(3, 8), 1.6, 1.6)
    p.drawEllipse(QPointF(8, 8), 1.6, 1.6)
    p.drawEllipse(QPointF(13, 8), 1.6, 1.6)
    p.end()
    return QIcon(pm)


def make_accounts_icon() -> QIcon:
    """Person silhouette — accounts / sign-in."""
    pm, p = _new_painter()
    p.drawEllipse(QRectF(5.5, 2.5, 5, 5))
    path = QPainterPath()
    path.moveTo(2.5, 13.5)
    path.quadTo(3, 9.5, 8, 9.5)
    path.quadTo(13, 9.5, 13.5, 13.5)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def make_editor_icon() -> QIcon:
    """Pencil glyph — editor settings."""
    pm, p = _new_painter()
    p.drawLine(QPointF(3, 13), QPointF(3.8, 10.2))
    p.drawLine(QPointF(3.8, 10.2), QPointF(11, 3))
    p.drawLine(QPointF(11, 3), QPointF(13, 5))
    p.drawLine(QPointF(13, 5), QPointF(5.8, 12.2))
    p.drawLine(QPointF(5.8, 12.2), QPointF(3, 13))
    p.drawLine(QPointF(3.8, 10.2), QPointF(5.8, 12.2))
    p.end()
    return QIcon(pm)


def make_ai_icon() -> QIcon:
    """Four-point sparkle — AI settings."""
    pm, p = _new_painter()
    path = QPainterPath()
    cx, cy = 8.0, 8.0
    path.moveTo(cx, cy - 6)
    path.quadTo(cx, cy, cx + 6, cy)
    path.quadTo(cx, cy, cx, cy + 6)
    path.quadTo(cx, cy, cx - 6, cy)
    path.quadTo(cx, cy, cx, cy - 6)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def make_terminal_icon() -> QIcon:
    """Terminal prompt glyph — matches the '>' chevron used in chat's menu icon."""
    pm, p = _new_painter()
    p.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
    p.drawLine(QPointF(4.5, 6.5), QPointF(7, 8.5))
    p.drawLine(QPointF(7, 8.5), QPointF(4.5, 10.5))
    p.drawLine(QPointF(8.5, 10.5), QPointF(11.5, 10.5))
    p.end()
    return QIcon(pm)


def make_mcp_icon() -> QIcon:
    """Plug glyph — same concept as the activity bar's MCP icon."""
    pm, p = _new_painter()
    p.drawLine(QPointF(5.5, 1.5), QPointF(5.5, 5))
    p.drawLine(QPointF(10.5, 1.5), QPointF(10.5, 5))
    p.drawRoundedRect(QRectF(3, 5, 10, 5.5), 1.5, 1.5)
    p.drawLine(QPointF(8, 10.5), QPointF(8, 14.5))
    p.end()
    return QIcon(pm)
