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


def _hidpi_canvas(size: int = 16, scale: int = 2) -> tuple[QPixmap, QPainter]:
    """A transparent ``size``×``size`` logical pixmap rendered at ``scale``×.

    Header glyphs are tiny; painting them at 1× left them soft and
    ragged on HiDPI screens. The painter is pre-scaled so callers keep
    drawing in 16px coordinates.
    """
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _plus_badge(p: QPainter, cx: float, cy: float) -> None:
    """Small accent "+" in a knocked-out disc at (cx, cy).

    The disc is filled with the header background so the plus never
    collides with the outline underneath it — the old icons drew the
    plus straight over the shape's edge, which read as a smudge.
    """
    from PyQt6.QtCore import Qt

    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tc.get("bg_surface")))
    p.drawEllipse(QRectF(cx - 4.5, cy - 4.5, 9, 9))
    pen = QPen(QColor(tc.get("accent_primary")))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QRectF(cx - 2.6, cy, 5.2, 0).topLeft(), QRectF(cx - 2.6, cy, 5.2, 0).topRight())
    p.drawLine(QRectF(cx, cy - 2.6, 0, 5.2).topLeft(), QRectF(cx, cy - 2.6, 0, 5.2).bottomLeft())
    p.restore()


def _outline_pen(width: float = 1.5) -> QPen:
    from PyQt6.QtCore import Qt

    pen = QPen(QColor(tc.get("text_primary")))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_new_file_icon() -> QIcon:
    """Document with folded corner and a "+" badge — explorer 'New File'."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen())
    path = QPainterPath()
    path.moveTo(3.5, 1.5)
    path.lineTo(8.5, 1.5)
    path.lineTo(11.5, 4.5)
    path.lineTo(11.5, 14.5)
    path.lineTo(3.5, 14.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QRectF(8.5, 1.5, 0, 3).topLeft(), QRectF(8.5, 1.5, 0, 3).bottomLeft())
    p.drawLine(QRectF(8.5, 4.5, 3, 0).topLeft(), QRectF(8.5, 4.5, 3, 0).topRight())
    _plus_badge(p, 12.0, 12.0)
    p.end()
    return QIcon(pm)


def draw_new_folder_icon() -> QIcon:
    """Folder with tab and a "+" badge — explorer 'New Folder'."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen())
    path = QPainterPath()
    path.moveTo(1.5, 4.0)
    path.lineTo(5.5, 4.0)
    path.lineTo(7.0, 5.5)
    path.lineTo(14.5, 5.5)
    path.lineTo(14.5, 13.5)
    path.lineTo(1.5, 13.5)
    path.closeSubpath()
    p.drawPath(path)
    _plus_badge(p, 12.0, 12.0)
    p.end()
    return QIcon(pm)


def draw_collapse_all_icon() -> QIcon:
    """Square with a minus — 'collapse all' (VS Code's glyph)."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen())
    p.drawRoundedRect(QRectF(2.5, 2.5, 11, 11), 1.5, 1.5)
    p.drawLine(QRectF(5.5, 8, 5, 0).topLeft(), QRectF(5.5, 8, 5, 0).topRight())
    p.end()
    return QIcon(pm)


def draw_expand_icon() -> QIcon:
    """Four corner arrows pointing outward — 'give this the whole window'."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen(1.6))
    for cx, cy, dx, dy in (
        (2.5, 2.5, 1, 1),
        (13.5, 2.5, -1, 1),
        (2.5, 13.5, 1, -1),
        (13.5, 13.5, -1, -1),
    ):
        # corner bracket + diagonal toward the centre
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx + 4 * dx, cy, 0, 0).topLeft())
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx, cy + 4 * dy, 0, 0).topLeft())
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx + 3 * dx, cy + 3 * dy, 0, 0).topLeft())
    p.end()
    return QIcon(pm)


def draw_collapse_icon() -> QIcon:
    """Four corner arrows pointing inward — 'restore the side panels'."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen(1.6))
    for cx, cy, dx, dy in (
        (6.5, 6.5, -1, -1),
        (9.5, 6.5, 1, -1),
        (6.5, 9.5, -1, 1),
        (9.5, 9.5, 1, 1),
    ):
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx + 3.5 * dx, cy, 0, 0).topLeft())
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx, cy + 3.5 * dy, 0, 0).topLeft())
        p.drawLine(QRectF(cx, cy, 0, 0).topLeft(), QRectF(cx + 4 * dx, cy + 4 * dy, 0, 0).topLeft())
    p.end()
    return QIcon(pm)


def draw_close_icon() -> QIcon:
    """A small × — 'hide this panel'."""
    pm, p = _hidpi_canvas()
    p.setPen(_outline_pen(1.6))
    p.drawLine(QRectF(4.5, 4.5, 0, 0).topLeft(), QRectF(11.5, 11.5, 0, 0).topLeft())
    p.drawLine(QRectF(11.5, 4.5, 0, 0).topLeft(), QRectF(4.5, 11.5, 0, 0).topLeft())
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
