"""VS Code-style activity bar — thin icon strip on the far left."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


class ActivityBarButton(QWidget):
    """Single icon button in the activity bar."""

    clicked = pyqtSignal()

    def __init__(
        self,
        icon_type: str,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_type = icon_type
        self._active = False
        self._hovered = False
        self.setFixedSize(48, 48)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, val: bool) -> None:
        self._active = val
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # Active indicator — left accent bar
        if self._active:
            painter.fillRect(0, 8, 2, h - 16, QColor(tc.get("activity_indicator")))

        # Hover background
        if self._hovered and not self._active:
            painter.fillRect(0, 0, w, h, QColor(255, 255, 255, 15))

        # Icon color
        if self._active:
            color = QColor(tc.get("activity_icon_active"))
        elif self._hovered:
            color = QColor(tc.get("activity_icon_hover"))
        else:
            color = QColor(tc.get("activity_icon"))

        pen = QPen(color)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # All icons drawn in a 24x24 area centered in the 48x48 button
        ox = (w - 24) / 2
        oy = (h - 24) / 2

        if self._icon_type == "files":
            self._draw_files_icon(painter, ox, oy)
        elif self._icon_type == "search":
            self._draw_search_icon(painter, ox, oy)
        elif self._icon_type == "git":
            self._draw_git_icon(painter, ox, oy)
        elif self._icon_type == "mcp":
            self._draw_mcp_icon(painter, ox, oy)
        elif self._icon_type == "database":
            self._draw_database_icon(painter, ox, oy)
        elif self._icon_type == "docker":
            self._draw_docker_icon(painter, ox, oy)
        elif self._icon_type == "kubernetes":
            self._draw_kubernetes_icon(painter, ox, oy)
        elif self._icon_type == "tests":
            self._draw_tests_icon(painter, ox, oy)
        elif self._icon_type == "tasks":
            self._draw_tasks_icon(painter, ox, oy)
        elif self._icon_type == "today":
            self._draw_today_icon(painter, ox, oy)
        elif self._icon_type == "settings":
            self._draw_settings_icon(painter, ox, oy)

        painter.end()

    def _draw_files_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """File explorer — stacked file pages."""
        # Back page
        path1 = QPainterPath()
        path1.moveTo(ox + 6, oy + 2)
        path1.lineTo(ox + 18, oy + 2)
        path1.lineTo(ox + 18, oy + 18)
        path1.lineTo(ox + 6, oy + 18)
        path1.closeSubpath()
        p.drawPath(path1)

        # Front page (offset)
        path2 = QPainterPath()
        path2.moveTo(ox + 3, oy + 6)
        path2.lineTo(ox + 10, oy + 6)
        path2.lineTo(ox + 14, oy + 10)
        path2.lineTo(ox + 14, oy + 22)
        path2.lineTo(ox + 3, oy + 22)
        path2.closeSubpath()
        p.drawPath(path2)

        # Fold corner on front page
        p.drawLine(QPointF(ox + 10, oy + 6), QPointF(ox + 10, oy + 10))
        p.drawLine(QPointF(ox + 10, oy + 10), QPointF(ox + 14, oy + 10))

    def _draw_search_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Magnifying glass."""
        # Circle
        p.drawEllipse(QRectF(ox + 2, oy + 2, 14, 14))
        # Handle — thicker
        pen = p.pen()
        pen.setWidthF(2.5)
        p.setPen(pen)
        p.drawLine(QPointF(ox + 14, oy + 14), QPointF(ox + 21, oy + 21))
        pen.setWidthF(1.5)
        p.setPen(pen)

    def _draw_git_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Source control — git branch fork."""
        r = 3.0  # node radius

        # Top node
        top = QPointF(ox + 12, oy + 4)
        # Bottom-left node
        bl = QPointF(ox + 7, oy + 20)
        # Bottom-right node
        br = QPointF(ox + 17, oy + 20)

        # Vertical line from top to fork point
        fork_y = oy + 14
        p.drawLine(top, QPointF(ox + 12, fork_y))

        # Fork lines
        p.drawLine(QPointF(ox + 12, fork_y), QPointF(bl.x(), bl.y() - r))
        p.drawLine(QPointF(ox + 12, fork_y), QPointF(br.x(), br.y() - r))

        # Nodes (filled circles)
        p.setBrush(p.pen().color())
        p.drawEllipse(top, r, r)
        p.drawEllipse(bl, r, r)
        p.drawEllipse(br, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_mcp_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """MCP servers — plug/connector icon."""
        # Top prongs
        p.drawLine(QPointF(ox + 8, oy + 1), QPointF(ox + 8, oy + 7))
        p.drawLine(QPointF(ox + 16, oy + 1), QPointF(ox + 16, oy + 7))

        # Plug body
        p.drawRoundedRect(QRectF(ox + 4, oy + 7, 16, 8), 2, 2)

        # Cable
        cable = QPainterPath()
        cable.moveTo(ox + 12, oy + 15)
        cable.lineTo(ox + 12, oy + 19)
        cable.cubicTo(
            ox + 12,
            oy + 23,
            ox + 12,
            oy + 23,
            ox + 12,
            oy + 23,
        )
        p.drawPath(cable)
        p.drawLine(QPointF(ox + 12, oy + 15), QPointF(ox + 12, oy + 23))

    def _draw_docker_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Docker — stacked container boxes (simplified whale)."""
        # Three stacked boxes (container imagery)
        p.drawRect(QRectF(ox + 3, oy + 14, 6, 4))
        p.drawRect(QRectF(ox + 9, oy + 14, 6, 4))
        p.drawRect(QRectF(ox + 15, oy + 14, 6, 4))
        p.drawRect(QRectF(ox + 3, oy + 10, 6, 4))
        p.drawRect(QRectF(ox + 9, oy + 10, 6, 4))
        p.drawRect(QRectF(ox + 9, oy + 6, 6, 4))
        # Base wave line
        cable = QPainterPath()
        cable.moveTo(ox + 0, oy + 20)
        cable.cubicTo(ox + 4, oy + 18, ox + 8, oy + 22, ox + 12, oy + 20)
        cable.cubicTo(ox + 16, oy + 18, ox + 20, oy + 22, ox + 24, oy + 20)
        p.drawPath(cable)

    def _draw_kubernetes_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Kubernetes — helm wheel shape."""
        cx, cy = ox + 12, oy + 12
        # Hexagon outline
        import math

        points = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            px = cx + 9 * math.cos(angle)
            py = cy + 9 * math.sin(angle)
            points.append(QPointF(px, py))
        for i in range(6):
            p.drawLine(points[i], points[(i + 1) % 6])
        # Center dot
        p.drawEllipse(QRectF(cx - 2, cy - 2, 4, 4))
        # Spokes from center
        for i in range(6):
            angle = math.radians(60 * i - 30)
            sx = cx + 5 * math.cos(angle)
            sy = cy + 5 * math.sin(angle)
            p.drawLine(QPointF(cx, cy), QPointF(sx, sy))

    def _draw_database_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Database — cylinder/drum shape."""
        # Top ellipse
        p.drawEllipse(QRectF(ox + 4, oy + 2, 16, 6))
        # Sides
        p.drawLine(QPointF(ox + 4, oy + 5), QPointF(ox + 4, oy + 19))
        p.drawLine(QPointF(ox + 20, oy + 5), QPointF(ox + 20, oy + 19))
        # Bottom ellipse
        p.drawEllipse(QRectF(ox + 4, oy + 16, 16, 6))
        # Middle stripe
        p.drawArc(QRectF(ox + 4, oy + 9, 16, 6), 180 * 16, 180 * 16)

    def _draw_today_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Today — sun-with-rays glyph."""
        cx, cy = ox + 12, oy + 12
        # Central disc
        p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
        # 8 rays
        for i in range(8):
            angle = (math.pi / 4) * i
            x1 = cx + 7 * math.cos(angle)
            y1 = cy + 7 * math.sin(angle)
            x2 = cx + 11 * math.cos(angle)
            y2 = cy + 11 * math.sin(angle)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _draw_tasks_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Tasks — checklist clipboard glyph."""
        # Clipboard outline
        p.drawRect(QRectF(ox + 4, oy + 4, 16, 18))
        # Clip at the top
        p.drawRect(QRectF(ox + 9, oy + 1, 6, 4))
        # Three checklist rows: a checkbox + a line
        for i, y in enumerate((9, 13, 17)):
            p.drawRect(QRectF(ox + 7, oy + y, 3, 3))
            p.drawLine(QPointF(ox + 12, oy + y + 1.5), QPointF(ox + 18, oy + y + 1.5))
            _ = i

    def _draw_tests_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Tests — beaker/flask glyph."""
        # Flask outline: narrow neck on top widening to a rounded base.
        path = QPainterPath()
        path.moveTo(ox + 9, oy + 3)
        path.lineTo(ox + 15, oy + 3)
        path.lineTo(ox + 15, oy + 9)
        path.lineTo(ox + 19, oy + 19)
        path.lineTo(ox + 5, oy + 19)
        path.lineTo(ox + 9, oy + 9)
        path.closeSubpath()
        p.drawPath(path)
        # Liquid line inside the flask
        pen = p.pen()
        pen.setWidthF(1.4)
        p.setPen(pen)
        p.drawLine(QPointF(ox + 7, oy + 14), QPointF(ox + 17, oy + 14))

    def _draw_settings_icon(self, p: QPainter, ox: float, oy: float) -> None:
        """Settings — gear/cog icon."""
        cx, cy = ox + 12, oy + 12

        # Inner circle
        p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))

        # Gear teeth
        teeth = 8
        inner_r = 7.0
        outer_r = 10.5
        tooth_half = math.pi / teeth  # half tooth width

        pen = p.pen()
        pen.setWidthF(1.8)
        p.setPen(pen)

        path = QPainterPath()
        for i in range(teeth):
            angle = (2 * math.pi * i) / teeth
            # Outer tooth edge
            a1 = angle - tooth_half * 0.5
            a2 = angle + tooth_half * 0.5
            # Inner valley
            a3 = angle + tooth_half * 0.5
            a4 = angle + tooth_half * 1.5

            x1 = cx + outer_r * math.cos(a1)
            y1 = cy + outer_r * math.sin(a1)
            x2 = cx + outer_r * math.cos(a2)
            y2 = cy + outer_r * math.sin(a2)
            x3 = cx + inner_r * math.cos(a3)
            y3 = cy + inner_r * math.sin(a3)
            x4 = cx + inner_r * math.cos(a4)
            y4 = cy + inner_r * math.sin(a4)

            if i == 0:
                path.moveTo(x1, y1)
            else:
                path.lineTo(x1, y1)
            path.lineTo(x2, y2)
            path.lineTo(x3, y3)
            path.lineTo(x4, y4)

        path.closeSubpath()
        p.drawPath(path)


class ActivityBar(QWidget):
    """Thin vertical icon bar on the far left — VS Code style."""

    view_changed = pyqtSignal(str)  # Emits the view name when clicked

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(48)
        self.setStyleSheet(
            f"background-color: {tc.get('bg_activity_bar')}; "
            f"border-right: 1px solid {tc.get('border_subtle')};"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self._buttons: dict[str, ActivityBarButton] = {}

        # Top icons
        top_items = [
            ("today", "Today (Ctrl+Shift+H)"),
            ("tasks", "Tasks (Ctrl+Shift+J)"),
            ("files", "Explorer (Ctrl+Shift+E)"),
            ("search", "Search (Ctrl+Shift+F)"),
            ("git", "Source Control (Ctrl+Shift+G)"),
            ("mcp", "MCP Servers (Ctrl+Shift+M)"),
            ("database", "Database Explorer (Ctrl+Shift+D)"),
            ("docker", "Docker (Ctrl+Shift+K)"),
            ("kubernetes", "Kubernetes (Ctrl+Shift+8)"),
            ("tests", "Tests (Ctrl+Shift+T)"),
        ]

        for icon_type, tooltip in top_items:
            btn = ActivityBarButton(icon_type, tooltip)
            btn.clicked.connect(lambda it=icon_type: self._on_click(it))
            layout.addWidget(btn)
            self._buttons[icon_type] = btn

        layout.addStretch()

        # Bottom icon: settings
        settings_btn = ActivityBarButton("settings", "Settings (Ctrl+,)")
        settings_btn.clicked.connect(lambda: self._on_click("settings"))
        layout.addWidget(settings_btn)
        self._buttons["settings"] = settings_btn

        # Default active
        self._buttons["files"].active = True

    def _on_click(self, view_name: str) -> None:
        # If clicking the already active view, toggle sidebar visibility
        _ = next((k for k, b in self._buttons.items() if b.active), None)

        if view_name == "settings":
            self.view_changed.emit("settings")
            return

        # Update active state
        for key, btn in self._buttons.items():
            btn.active = key == view_name

        self.view_changed.emit(view_name)

    def set_active(self, view_name: str) -> None:
        """Programmatically set the active view."""
        for key, btn in self._buttons.items():
            btn.active = key == view_name
