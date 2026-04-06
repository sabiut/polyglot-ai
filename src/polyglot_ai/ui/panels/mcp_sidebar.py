"""MCP servers sidebar panel — shows connected MCP servers and their tools."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import safe_task


class MCPSidebar(QWidget):
    """Sidebar panel showing MCP server status and tools."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mcp_client = None
        self._filter_text = ""
        self._expanded: set[str] = set()

        # Auto-refresh timer while connections settle
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #333;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 6, 0)
        header_layout.setSpacing(2)

        title = QLabel("MCP SERVERS")
        title.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #888; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        header_layout.addWidget(title)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "font-size: 10px; color: #4ec9b0; background: transparent; margin-left: 6px;"
        )
        header_layout.addWidget(self._summary_label)
        header_layout.addStretch()

        refresh_btn = self._icon_btn(self._draw_refresh_icon(), "Refresh connection status")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        header_layout.addWidget(refresh_btn)

        reconnect_all_btn = self._icon_btn(self._draw_bolt_icon(), "Connect all servers")
        reconnect_all_btn.clicked.connect(self._on_connect_all)
        header_layout.addWidget(reconnect_all_btn)

        manage_btn = self._icon_btn(self._draw_plus_icon(), "Manage MCP Servers")
        manage_btn.clicked.connect(self._open_mcp_settings)
        header_layout.addWidget(manage_btn)
        layout.addWidget(header)

        # Search box
        search_wrap = QWidget()
        search_wrap.setStyleSheet("background: #1e1e1e; border-bottom: 1px solid #2a2a2a;")
        sl = QHBoxLayout(search_wrap)
        sl.setContentsMargins(8, 6, 8, 6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter servers or tools…")
        self._search.setStyleSheet(
            "QLineEdit { background: #2a2a2a; color: #ddd; border: 1px solid #333; "
            "border-radius: 3px; padding: 4px 6px; font-size: 11px; }"
            "QLineEdit:focus { border-color: #0e639c; }"
        )
        self._search.textChanged.connect(self._on_filter_changed)
        sl.addWidget(self._search)
        layout.addWidget(search_wrap)

        # Scroll area for server list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #1e1e1e; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 3px; }"
        )

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 8, 0, 8)
        self._content_layout.setSpacing(0)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Empty state
        self._empty_label = QLabel(
            "No MCP servers configured.\n\nClick + to add servers from\nthe MCP marketplace."
        )
        self._empty_label.setStyleSheet(
            "color: #666; font-size: 12px; padding: 20px; background: transparent;"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._content_layout.addWidget(self._empty_label)

        scroll.setWidget(self._content)
        layout.addWidget(scroll)

    def _icon_btn(self, icon: QIcon, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("mcpIconBtn")
        btn.setIcon(icon)
        btn.setFixedSize(22, 22)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#mcpIconBtn { background: transparent; border: none; }"
            "#mcpIconBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        return btn

    def _draw_plus_icon(self) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.drawLine(8, 3, 8, 13)
        p.drawLine(3, 8, 13, 8)
        p.end()
        return QIcon(pm)

    def _draw_refresh_icon(self) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        # Circular arc (arrow around ~300°)
        from PyQt6.QtCore import QRectF

        p.drawArc(QRectF(3, 3, 10, 10), 60 * 16, 280 * 16)
        # Arrow head at the opening (top-right)
        p.drawLine(12, 2, 12, 6)
        p.drawLine(12, 6, 8, 6)
        p.end()
        return QIcon(pm)

    def _draw_bolt_icon(self) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint

        p.setBrush(QColor("#cccccc"))
        p.setPen(Qt.PenStyle.NoPen)
        bolt = QPolygon(
            [
                QPoint(9, 1),
                QPoint(3, 9),
                QPoint(7, 9),
                QPoint(5, 15),
                QPoint(13, 6),
                QPoint(9, 6),
                QPoint(11, 1),
            ]
        )
        p.drawPolygon(bolt)
        p.end()
        return QIcon(pm)

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client
        self.refresh()
        # Kick a few refreshes to catch async connections settling
        self._refresh_timer.start()
        QTimer.singleShot(8000, self._refresh_timer.stop)

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self.refresh()

    def _on_refresh_clicked(self) -> None:
        self.refresh()
        self._refresh_timer.start()
        QTimer.singleShot(6000, self._refresh_timer.stop)

    def _on_connect_all(self) -> None:
        if self._mcp_client:
            safe_task(self._mcp_client.connect_all(), name="mcp_connect_all")
            self._refresh_timer.start()
            QTimer.singleShot(10000, self._refresh_timer.stop)

    def _toggle_server(self, name: str) -> None:
        if not self._mcp_client:
            return
        connected = set(self._mcp_client.connected_servers)
        if name in connected:
            safe_task(self._mcp_client.disconnect(name), name=f"mcp_disconnect_{name}")
        else:
            safe_task(self._mcp_client.connect(name), name=f"mcp_connect_{name}")
        self._refresh_timer.start()
        QTimer.singleShot(6000, self._refresh_timer.stop)

    def _toggle_expand(self, name: str) -> None:
        if name in self._expanded:
            self._expanded.discard(name)
        else:
            self._expanded.add(name)
        self.refresh()

    def refresh(self) -> None:
        """Refresh the server list."""
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not self._mcp_client:
            self._empty_label.show()
            self._summary_label.setText("")
            return

        configs = self._mcp_client.get_server_configs()
        connected = set(self._mcp_client.connected_servers)

        if not configs:
            self._empty_label.show()
            self._summary_label.setText("")
            return

        self._empty_label.hide()
        total_tools = len(self._mcp_client.available_tools)
        self._summary_label.setText(f"{len(connected)}/{len(configs)} · {total_tools} tools")

        f = self._filter_text
        for cfg in configs:
            is_connected = cfg.name in connected
            tools = (
                [t for t in self._mcp_client.available_tools.values() if t.server_name == cfg.name]
                if is_connected
                else []
            )

            # Filter
            if f:
                name_match = f in cfg.name.lower()
                tool_match = any(f in t.name.lower() for t in tools)
                if not (name_match or tool_match):
                    continue

            expanded = cfg.name in self._expanded
            card = self._create_server_item(cfg.name, is_connected, len(tools), expanded)
            self._content_layout.addWidget(card)

            if is_connected and expanded and tools:
                shown = [t for t in tools if not f or f in t.name.lower()]
                for tool in shown[:30]:
                    self._content_layout.addWidget(
                        self._create_tool_item(tool.name, tool.description)
                    )

    def _create_server_item(
        self, name: str, connected: bool, tool_count: int, expanded: bool
    ) -> QWidget:
        """Create a server row."""
        item = QWidget()
        item.setFixedHeight(32)
        item.setStyleSheet(
            "QWidget { background: transparent; }QWidget:hover { background: #2a2d2e; }"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)

        # Expand caret
        caret = QLabel("▾" if expanded else "▸")
        caret.setFixedWidth(12)
        caret.setStyleSheet(
            f"color: {'#aaa' if connected else '#555'}; font-size: 9px; background: transparent;"
        )
        caret.setCursor(Qt.CursorShape.PointingHandCursor)
        caret.mousePressEvent = lambda e, n=name: self._toggle_expand(n)  # type: ignore
        layout.addWidget(caret)

        # Status dot
        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setToolTip("Connected" if connected else "Not connected")
        dot.setStyleSheet(
            f"color: {'#4ec9b0' if connected else '#666'}; font-size: 10px; background: transparent;"
        )
        layout.addWidget(dot)

        # Server name (clickable to expand)
        name_label = QLabel(name)
        name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        name_label.setToolTip(
            f"{name}\nClick to {'collapse' if expanded else 'expand'} tool list"
        )
        name_label.setStyleSheet(
            f"font-size: 12px; color: {'#ddd' if connected else '#888'}; "
            f"background: transparent; font-weight: {'600' if connected else 'normal'};"
        )
        name_label.mousePressEvent = lambda e, n=name: self._toggle_expand(n)  # type: ignore
        layout.addWidget(name_label, stretch=1)

        # Tool count badge
        if connected and tool_count > 0:
            badge = QLabel(str(tool_count))
            badge.setFixedSize(22, 16)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setToolTip(f"{tool_count} tools available")
            badge.setStyleSheet(
                "background: #094771; color: #9cdcfe; font-size: 10px; "
                "border-radius: 3px; font-weight: 600;"
            )
            layout.addWidget(badge)

        # Connect/disconnect button
        action_btn = QPushButton("⏻" if connected else "▶")
        action_btn.setFixedSize(22, 20)
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_btn.setToolTip("Disconnect" if connected else "Connect")
        color = "#f48771" if connected else "#4ec9b0"
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {color}; border: none;
                font-size: 12px; font-weight: bold; border-radius: 3px;
            }}
            QPushButton:hover {{ background: #333; }}
        """)
        action_btn.clicked.connect(lambda _, n=name: self._toggle_server(n))
        layout.addWidget(action_btn)

        return item

    def _create_tool_item(self, name: str, description: str) -> QWidget:
        """Create an indented tool row."""
        item = QWidget()
        item.setFixedHeight(22)
        item.setToolTip(description or name)
        item.setStyleSheet(
            "QWidget { background: transparent; }QWidget:hover { background: #2a2d2e; }"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(38, 0, 8, 0)
        layout.setSpacing(4)

        icon = QLabel("⚡")
        icon.setFixedWidth(14)
        icon.setStyleSheet("font-size: 9px; color: #e5a00d; background: transparent;")
        layout.addWidget(icon)

        label = QLabel(name)
        label.setStyleSheet("font-size: 11px; color: #999; background: transparent;")
        layout.addWidget(label, stretch=1)

        return item

    def _open_mcp_settings(self) -> None:
        """Open settings dialog on MCP tab."""
        window = self.window()
        if hasattr(window, "_action_settings"):
            window._action_settings.trigger()
