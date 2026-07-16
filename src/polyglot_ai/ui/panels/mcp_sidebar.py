"""MCP servers sidebar panel — shows connected MCP servers and their tools."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
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
from polyglot_ai.ui import theme
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels import shared_icons
from polyglot_ai.ui.widgets.icon_button import make_icon_button


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
        self._header = QWidget()
        self._header.setFixedHeight(34)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 6, 0)
        header_layout.setSpacing(2)

        self._title = QLabel("MCP SERVERS")
        header_layout.addWidget(self._title)

        self._summary_label = QLabel("")
        header_layout.addWidget(self._summary_label)
        header_layout.addStretch()

        refresh_btn = make_icon_button(
            shared_icons.draw_refresh_icon(), "Refresh connection status"
        )
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        header_layout.addWidget(refresh_btn)

        reconnect_all_btn = make_icon_button(self._draw_bolt_icon(), "Connect all servers")
        reconnect_all_btn.clicked.connect(self._on_connect_all)
        header_layout.addWidget(reconnect_all_btn)

        manage_btn = make_icon_button(shared_icons.draw_plus_icon(), "Manage MCP Servers")
        manage_btn.clicked.connect(self._open_mcp_settings)
        header_layout.addWidget(manage_btn)
        layout.addWidget(self._header)

        # Search box
        self._search_wrap = QWidget()
        sl = QHBoxLayout(self._search_wrap)
        sl.setContentsMargins(8, 6, 8, 6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter servers or tools…")
        self._search.textChanged.connect(self._on_filter_changed)
        sl.addWidget(self._search)
        layout.addWidget(self._search_wrap)

        # Scroll area for server list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 8, 0, 8)
        self._content_layout.setSpacing(0)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Empty state
        self._empty_label = QLabel(
            "No MCP servers configured.\n\nClick + to add servers from\nthe MCP marketplace."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._content_layout.addWidget(self._empty_label)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)

        self._apply_theme_styles()
        theme.connect_theme_changed(self._apply_theme_styles)

    def _apply_theme_styles(self) -> None:
        self._header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        self._title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        self._summary_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_success_muted')}; "
            "background: transparent; margin-left: 6px;"
        )
        self._search_wrap.setStyleSheet(
            f"background: {tc.get('bg_base')}; border-bottom: 1px solid {tc.get('border_subtle')};"
        )
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {tc.get('bg_card')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 3px; padding: 4px 6px; font-size: {tc.FONT_SM}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}"
            f"QScrollBar:vertical {{ width: 6px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ background: {tc.get('scrollbar_thumb')}; "
            f"border-radius: 3px; }}"
        )
        self._empty_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_MD}px; "
            "padding: 20px; background: transparent;"
        )
        self.refresh()

    def _draw_bolt_icon(self) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint

        p.setBrush(QColor(tc.get("text_primary")))
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
            f"QWidget {{ background: transparent; }}"
            f"QWidget:hover {{ background: {tc.get('bg_hover_subtle')}; }}"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)

        # Expand caret
        caret = QLabel("▾" if expanded else "▸")
        caret.setFixedWidth(12)
        caret.setStyleSheet(
            f"color: {tc.get('text_secondary') if connected else tc.get('text_disabled')}; "
            "font-size: 9px; background: transparent;"
        )
        caret.setCursor(Qt.CursorShape.PointingHandCursor)
        caret.mousePressEvent = lambda e, n=name: self._toggle_expand(n)  # type: ignore
        layout.addWidget(caret)

        # Status dot
        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setToolTip("Connected" if connected else "Not connected")
        dot.setStyleSheet(
            f"color: {tc.get('accent_success_muted') if connected else tc.get('text_muted')}; "
            f"font-size: {tc.FONT_XS}px; background: transparent;"
        )
        layout.addWidget(dot)

        # Server name (clickable to expand)
        name_label = QLabel(name)
        name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        name_label.setToolTip(f"{name}\nClick to {'collapse' if expanded else 'expand'} tool list")
        name_label.setStyleSheet(
            f"font-size: {tc.FONT_MD}px; "
            f"color: {tc.get('text_primary') if connected else tc.get('text_tertiary')}; "
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
                f"background: {tc.get('bg_active')}; color: {tc.get('syn_identifier')}; "
                f"font-size: {tc.FONT_XS}px; border-radius: 3px; font-weight: 600;"
            )
            layout.addWidget(badge)

        # Connect/disconnect button
        action_btn = QPushButton("⏻" if connected else "▶")
        action_btn.setFixedSize(22, 20)
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_btn.setToolTip("Disconnect" if connected else "Connect")
        color = tc.get("accent_error") if connected else tc.get("accent_success_muted")
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {color}; border: none;
                font-size: {tc.FONT_MD}px; font-weight: bold; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {tc.get("bg_hover")}; }}
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
            f"QWidget {{ background: transparent; }}"
            f"QWidget:hover {{ background: {tc.get('bg_hover_subtle')}; }}"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(38, 0, 8, 0)
        layout.setSpacing(4)

        icon = QLabel("⚡")
        icon.setFixedWidth(14)
        icon.setStyleSheet(
            f"font-size: 9px; color: {tc.get('accent_warning')}; background: transparent;"
        )
        layout.addWidget(icon)

        label = QLabel(name)
        label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_secondary')}; background: transparent;"
        )
        layout.addWidget(label, stretch=1)

        return item

    def _open_mcp_settings(self) -> None:
        """Open settings dialog on MCP tab."""
        window = self.window()
        if hasattr(window, "_action_settings"):
            window._action_settings.trigger()
