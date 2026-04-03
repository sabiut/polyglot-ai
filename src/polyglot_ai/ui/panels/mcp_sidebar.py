"""MCP servers sidebar panel — shows connected MCP servers and their tools."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class MCPSidebar(QWidget):
    """Sidebar panel showing MCP server status and tools."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mcp_client = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #333;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("MCP SERVERS")
        title.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #888; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Manage button
        manage_btn = QPushButton("+")
        manage_btn.setFixedSize(22, 22)
        manage_btn.setToolTip("Manage MCP Servers")
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888; border: none;
                font-size: 16px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background: #333; color: #ddd; }
        """)
        manage_btn.clicked.connect(self._open_mcp_settings)
        header_layout.addWidget(manage_btn)
        layout.addWidget(header)

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
            "No MCP servers connected.\n\nClick + to add servers from\nthe MCP marketplace."
        )
        self._empty_label.setStyleSheet(
            "color: #666; font-size: 12px; padding: 20px; background: transparent;"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._content_layout.addWidget(self._empty_label)

        scroll.setWidget(self._content)
        layout.addWidget(scroll)

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client
        self.refresh()

    def refresh(self) -> None:
        """Refresh the server list."""
        # Clear existing items (keep empty label)
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not self._mcp_client:
            self._empty_label.show()
            return

        configs = self._mcp_client.get_server_configs()
        connected = set(self._mcp_client.connected_servers)

        if not configs:
            self._empty_label.show()
            return

        self._empty_label.hide()

        for cfg in configs:
            is_connected = cfg.name in connected
            tools = [t for t in self._mcp_client.available_tools.values()
                     if t.server_name == cfg.name] if is_connected else []

            card = self._create_server_item(cfg.name, is_connected, len(tools))
            self._content_layout.addWidget(card)

            # Show tools under connected servers
            if is_connected and tools:
                for tool in tools[:10]:  # Max 10 shown
                    tool_widget = self._create_tool_item(tool.name, tool.description)
                    self._content_layout.addWidget(tool_widget)

    def _create_server_item(self, name: str, connected: bool, tool_count: int) -> QWidget:
        """Create a server row."""
        item = QWidget()
        item.setFixedHeight(32)
        item.setStyleSheet(
            "QWidget { background: transparent; }"
            "QWidget:hover { background: #2a2d2e; }"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(6)

        # Status dot
        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet(
            f"color: {'#4ec9b0' if connected else '#666'}; "
            f"font-size: 8px; background: transparent;"
        )
        layout.addWidget(dot)

        # Server name
        name_label = QLabel(name)
        name_label.setStyleSheet(
            f"font-size: 12px; color: {'#ddd' if connected else '#888'}; "
            f"background: transparent; font-weight: {'600' if connected else 'normal'};"
        )
        layout.addWidget(name_label, stretch=1)

        # Tool count badge
        if connected and tool_count > 0:
            badge = QLabel(str(tool_count))
            badge.setFixedSize(20, 16)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background: #333; color: #aaa; font-size: 10px; "
                "border-radius: 3px; font-weight: 600;"
            )
            layout.addWidget(badge)

        return item

    def _create_tool_item(self, name: str, description: str) -> QWidget:
        """Create an indented tool row."""
        item = QWidget()
        item.setFixedHeight(24)
        item.setToolTip(description)
        item.setStyleSheet(
            "QWidget { background: transparent; }"
            "QWidget:hover { background: #2a2d2e; }"
        )
        layout = QHBoxLayout(item)
        layout.setContentsMargins(30, 0, 8, 0)
        layout.setSpacing(4)

        icon = QLabel("⚡")
        icon.setFixedWidth(14)
        icon.setStyleSheet("font-size: 9px; background: transparent;")
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
