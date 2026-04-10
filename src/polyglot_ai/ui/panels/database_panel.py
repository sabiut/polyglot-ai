"""Database explorer panel — browse schemas, run queries, view results."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import safe_task
from polyglot_ai.core.db_explorer import QueryResult, get_global_db_manager
from polyglot_ai.core.db_notebook import get_notebook_store
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.widgets.result_chart import ResultChartWidget
from polyglot_ai.ui.widgets.result_profile import ResultProfileWidget

logger = logging.getLogger(__name__)


# ── Sidebar Panel ───────────────────────────────────────────────────


class DatabasePanel(QWidget):
    """Database explorer sidebar — connection management + expand to full window."""

    _CONFIG_PATH = Path.home() / ".config" / "polyglot-ai" / "db_connections.json"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db_manager = get_global_db_manager()
        self._mcp_client = None
        self._active_connection: str | None = None
        self._full_window: _DatabaseWindow | None = None

        self._setup_ui()
        self._load_saved_connections()

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("dbHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#dbHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("DATABASES")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_tertiary')}; letter-spacing: 0.5px; "
            "background: transparent;"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()

        # Add connection button (painted + icon)
        add_btn = QPushButton()
        add_btn.setObjectName("dbAddBtn")
        add_btn.setFixedSize(22, 22)
        add_btn.setToolTip("Add database connection")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plus_pixmap = QPixmap(16, 16)
        plus_pixmap.fill(QColor(0, 0, 0, 0))
        pp = QPainter(plus_pixmap)
        plus_pen = QPen(QColor("#aaaaaa"))
        plus_pen.setWidthF(2.0)
        pp.setPen(plus_pen)
        pp.drawLine(8, 3, 8, 13)
        pp.drawLine(3, 8, 13, 8)
        pp.end()
        add_btn.setIcon(QIcon(plus_pixmap))
        add_btn.setStyleSheet(
            "#dbAddBtn { background: transparent; border: none; }"
            "#dbAddBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        add_btn.clicked.connect(self._show_add_dialog)
        h_layout.addWidget(add_btn)

        layout.addWidget(header)

        # Connection selector
        conn_bar = QWidget()
        conn_bar.setObjectName("dbConnBar")
        conn_bar.setStyleSheet(f"#dbConnBar {{ background: {tc.get('bg_base')}; }}")
        conn_layout = QHBoxLayout(conn_bar)
        conn_layout.setContentsMargins(8, 4, 8, 4)
        conn_layout.setSpacing(4)

        self._conn_combo = QComboBox()
        self._conn_combo.setStyleSheet(
            f"QComboBox {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; "
            f"padding: 3px 8px; font-size: {tc.FONT_SM}px; }}"
        )
        self._conn_combo.setPlaceholderText("No connections")
        self._conn_combo.currentTextChanged.connect(self._on_connection_changed)
        conn_layout.addWidget(self._conn_combo, stretch=1)

        # Connect icon button
        connect_btn = QPushButton()
        connect_btn.setObjectName("dbConnectBtn")
        connect_btn.setFixedSize(26, 26)
        connect_btn.setToolTip("Connect to database")
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        conn_pixmap = QPixmap(16, 16)
        conn_pixmap.fill(QColor(0, 0, 0, 0))
        cp = QPainter(conn_pixmap)
        cp_pen = QPen(QColor("#4ec9b0"))
        cp_pen.setWidthF(2.0)
        cp.setPen(cp_pen)
        cp.drawLine(4, 8, 8, 4)
        cp.drawLine(8, 4, 12, 8)
        cp.drawLine(12, 8, 8, 12)
        cp.drawLine(8, 12, 4, 8)
        cp.drawLine(8, 1, 8, 4)
        cp.drawLine(8, 12, 8, 15)
        cp.end()
        connect_btn.setIcon(QIcon(conn_pixmap))
        connect_btn.setStyleSheet(
            "#dbConnectBtn { background: transparent; border: none; }"
            "#dbConnectBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        connect_btn.clicked.connect(self._connect_selected)
        conn_layout.addWidget(connect_btn)

        # Disconnect icon button
        disconnect_btn = QPushButton()
        disconnect_btn.setObjectName("dbDisconnectBtn")
        disconnect_btn.setFixedSize(26, 26)
        disconnect_btn.setToolTip("Disconnect from database")
        disconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dc_pixmap = QPixmap(16, 16)
        dc_pixmap.fill(QColor(0, 0, 0, 0))
        dp = QPainter(dc_pixmap)
        dp_pen = QPen(QColor("#f44747"))
        dp_pen.setWidthF(2.0)
        dp.setPen(dp_pen)
        # Draw an X
        dp.drawLine(4, 4, 12, 12)
        dp.drawLine(12, 4, 4, 12)
        dp.end()
        disconnect_btn.setIcon(QIcon(dc_pixmap))
        disconnect_btn.setStyleSheet(
            "#dbDisconnectBtn { background: transparent; border: none; }"
            "#dbDisconnectBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        disconnect_btn.clicked.connect(self._disconnect_selected)
        conn_layout.addWidget(disconnect_btn)

        layout.addWidget(conn_bar)

        # Schema tree (compact, in sidebar)
        self._schema_tree = QTreeWidget()
        self._schema_tree.setHeaderLabels(["Name", "Type"])
        self._schema_tree.setColumnWidth(0, 180)
        self._schema_tree.setStyleSheet(
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 2px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 3px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )
        self._schema_tree.itemDoubleClicked.connect(self._on_schema_double_click)
        layout.addWidget(self._schema_tree)

        layout.addStretch()

        # Open Explorer button at bottom
        open_bar = QWidget()
        open_bar.setObjectName("dbOpenBar")
        open_bar.setFixedHeight(40)
        open_bar.setStyleSheet(
            f"#dbOpenBar {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        ob_layout = QHBoxLayout(open_bar)
        ob_layout.setContentsMargins(8, 0, 8, 0)

        self._open_btn = QPushButton("Open SQL Editor")
        self._open_btn.setObjectName("dbOpenExplorer")
        self._open_btn.setFixedHeight(28)
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.setStyleSheet(
            f"#dbOpenExplorer {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 16px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#dbOpenExplorer:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        self._open_btn.clicked.connect(self._open_full_window)
        ob_layout.addWidget(self._open_btn)

        layout.addWidget(open_bar)

        # Status bar
        self._status_label = QLabel("")
        self._status_label.setObjectName("dbStatusBar")
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet(
            f"#dbStatusBar {{ color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px; }}"
        )
        layout.addWidget(self._status_label)

        # Keyboard shortcut: Ctrl+Enter opens full window
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self._open_full_window)

    # ── Connection Management ───────────────────────────────────────

    def _show_add_dialog(self) -> None:
        dialog = _AddConnectionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, db_type, conn_str, read_only = dialog.get_values()
            if name and conn_str:
                self._db_manager.add_connection_sync(
                    name, db_type, conn_str, mcp_client=self._mcp_client,
                    read_only=read_only,
                )
                self._conn_combo.addItem(f"{name} ({db_type})")
                self._conn_combo.setCurrentIndex(self._conn_combo.count() - 1)
                self._save_connections()

    def _save_connections(self) -> None:
        """Persist connections to disk. Connection strings stored in keyring."""
        import json
        import keyring

        self._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for name, conn in self._db_manager.connections.items():
            # Store connection string in keyring (may contain passwords)
            keyring.set_password("polyglot-ai-db", name, conn._connection_string)
            data.append({
                "name": name,
                "db_type": conn.db_type,
                "read_only": conn.read_only,
            })

        from polyglot_ai.core.security import secure_write

        secure_write(self._CONFIG_PATH, json.dumps(data, indent=2))
        logger.info("Saved %d database connections", len(data))

    def _load_saved_connections(self) -> None:
        """Load previously saved connections from disk."""
        import json
        import keyring

        if not self._CONFIG_PATH.exists():
            return
        try:
            data = json.loads(self._CONFIG_PATH.read_text(encoding="utf-8"))
            for entry in data:
                name = entry.get("name", "")
                db_type = entry.get("db_type", "sqlite")
                read_only = entry.get("read_only", True)
                conn_str = keyring.get_password("polyglot-ai-db", name) or ""
                if name and conn_str:
                    self._db_manager.add_connection_sync(
                        name, db_type, conn_str, mcp_client=self._mcp_client,
                        read_only=read_only,
                    )
                    self._conn_combo.addItem(f"{name} ({db_type})")
            if self._conn_combo.count() > 0:
                logger.info("Loaded %d saved database connections", self._conn_combo.count())
        except Exception:
            logger.exception("Failed to load saved database connections")

    def _on_connection_changed(self, text: str) -> None:
        if not text:
            self._active_connection = None
            return
        name = text.split(" (")[0] if " (" in text else text
        self._active_connection = name

    def _connect_selected(self) -> None:
        if not self._active_connection:
            return
        conn = self._db_manager.get_connection(self._active_connection)
        if not conn:
            return

        async def do_connect():
            ok, msg = await conn.connect()
            if ok:
                self._status_label.setText(f"Connected: {conn.name}")
                tables = await conn.get_schema()
                QTimer.singleShot(0, lambda: self._populate_schema(tables))
            else:
                self._status_label.setText(f"Error: {msg[:60]}")

        safe_task(do_connect(), name="db_connect")

    def _disconnect_selected(self) -> None:
        if not self._active_connection:
            self._status_label.setText("No active connection")
            return
        conn = self._db_manager.get_connection(self._active_connection)
        if not conn:
            return

        async def do_disconnect():
            try:
                await conn.disconnect()
                self._status_label.setText(f"Disconnected: {conn.name}")
                QTimer.singleShot(0, self._schema_tree.clear)
            except Exception as e:
                self._status_label.setText(f"Error: {str(e)[:60]}")

        safe_task(do_disconnect(), name="db_disconnect")

    def _populate_schema(self, tables) -> None:
        self._schema_tree.clear()
        for table in tables:
            table_item = QTreeWidgetItem(self._schema_tree)
            table_item.setText(0, table.name)
            table_item.setText(1, "table")
            for col in table.columns:
                col_item = QTreeWidgetItem(table_item)
                col_item.setText(0, col.name)
                type_str = col.data_type
                if col.primary_key:
                    type_str += " PK"
                col_item.setText(1, type_str)
        self._schema_tree.expandAll()

    def _on_schema_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        if item.parent() is None:
            table_name = item.text(0)
            self._open_full_window(initial_query=f'SELECT * FROM "{table_name}" LIMIT 100')

    # ── Full Window ─────────────────────────────────────────────────

    def _open_full_window(self, initial_query: str = "") -> None:
        if not self._active_connection:
            self._status_label.setText("Connect to a database first")
            return
        conn = self._db_manager.get_connection(self._active_connection)
        if not conn:
            return

        # Reuse existing window or create new
        if self._full_window and self._full_window.isVisible():
            self._full_window.raise_()
            self._full_window.activateWindow()
            if initial_query:
                self._full_window.set_query(initial_query)
            return

        self._full_window = _DatabaseWindow(conn, self)
        if initial_query:
            self._full_window.set_query(initial_query)
        self._full_window.show()


# ── Full Database Explorer Window ───────────────────────────────────


class _DatabaseWindow(QWidget):
    """Standalone database explorer window with schema tree, SQL editor, results."""

    def __init__(self, connection, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._conn = connection
        self.resize(1000, 700)
        self.setMinimumSize(600, 400)
        self.setStyleSheet(f"background: {tc.get('bg_base')};")

        self._setup_ui()
        self.setWindowTitle(f"Database — {self._extract_db_name()}")
        self._load_schema()
        # Populate history + snippets sidebars from the persistent store.
        self._refresh_history()
        self._refresh_snippets()

    def _extract_db_name(self) -> str:
        """Extract the database name from the connection string."""
        conn_str = self._conn._connection_string
        if self._conn.db_type == "sqlite":
            return Path(conn_str).stem
        # PostgreSQL/MySQL: postgresql://user:pass@host:port/dbname
        try:
            from urllib.parse import urlparse

            parsed = urlparse(conn_str)
            db = parsed.path.lstrip("/") if parsed.path else ""
            return db or self._conn.name
        except Exception:
            return self._conn.name

    def set_query(self, sql: str) -> None:
        self._sql_editor.setPlainText(sql)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setObjectName("dbWindowHeader")
        header.setFixedHeight(40)
        header.setStyleSheet(
            f"#dbWindowHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 12, 0)

        # Connection badge — extract database name from connection string
        db_icons = {"sqlite": "📁", "postgresql": "🐘", "mysql": "🐬"}
        icon = db_icons.get(self._conn.db_type, "🗄")
        db_name = self._extract_db_name()
        conn_label = QLabel(f"{icon}  {db_name}")
        conn_label.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(conn_label)

        type_badge = QLabel(self._conn.db_type.upper())
        type_badge.setStyleSheet(
            f"background: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; "
            f"border-radius: 3px; padding: 2px 8px; font-size: {tc.FONT_XS}px; font-weight: 600;"
        )
        h_layout.addWidget(type_badge)
        h_layout.addStretch()

        layout.addWidget(header)

        # Main content: horizontal splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; width: 2px; }}"
        )

        # Left: Schema tree
        schema_widget = QWidget()
        s_layout = QVBoxLayout(schema_widget)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(0)

        schema_header = QWidget()
        schema_header.setObjectName("dbSchemaHeader")
        schema_header.setFixedHeight(28)
        schema_header.setStyleSheet(
            f"#dbSchemaHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        sh_layout = QHBoxLayout(schema_header)
        sh_layout.setContentsMargins(8, 0, 8, 0)
        sh_label = QLabel("SCHEMA")
        sh_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        sh_layout.addWidget(sh_label)
        s_layout.addWidget(schema_header)

        self._schema_tree = QTreeWidget()
        self._schema_tree.setHeaderLabels(["Name", "Type"])
        self._schema_tree.setColumnWidth(0, 200)
        self._schema_tree.setStyleSheet(
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 3px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 4px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )
        self._schema_tree.itemClicked.connect(self._on_table_clicked)
        self._schema_tree.itemDoubleClicked.connect(self._on_table_double_click)
        s_layout.addWidget(self._schema_tree)

        main_splitter.addWidget(schema_widget)

        # History + Snippets sidebar — tabbed widget so the user can
        # browse recent queries and reuse saved snippets without leaving
        # the SQL editor. Click any entry → loads it into the editor.
        side_widget = QWidget()
        side_layout = QVBoxLayout(side_widget)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)

        side_tabs = QTabWidget()
        side_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #1e1e1e; }"
            "QTabBar::tab { background: #252526; color: #888; padding: 5px 12px; "
            "font-size: 11px; border-top: 2px solid transparent; }"
            "QTabBar::tab:selected { background: #1e1e1e; color: #ddd; "
            "border-top-color: #0e639c; }"
        )

        # History tab
        self._history_list = QListWidget()
        self._history_list.setStyleSheet(
            "QListWidget { background: #1e1e1e; color: #ddd; border: none; "
            "font-size: 11px; }"
            "QListWidget::item { padding: 4px 8px; border-bottom: 1px solid #2a2a2a; }"
            "QListWidget::item:hover { background: #2a2d2e; }"
            "QListWidget::item:selected { background: #094771; color: #fff; }"
        )
        self._history_list.itemDoubleClicked.connect(self._on_history_picked)
        side_tabs.addTab(self._history_list, "History")

        # Snippets tab
        self._snippets_list = QListWidget()
        self._snippets_list.setStyleSheet(self._history_list.styleSheet())
        self._snippets_list.itemDoubleClicked.connect(self._on_snippet_picked)
        self._snippets_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._snippets_list.customContextMenuRequested.connect(self._show_snippets_menu)
        side_tabs.addTab(self._snippets_list, "Snippets")

        side_layout.addWidget(side_tabs)
        main_splitter.addWidget(side_widget)

        # Right: SQL editor + results (vertical splitter)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )

        # SQL editor area
        sql_widget = QWidget()
        sql_layout = QVBoxLayout(sql_widget)
        sql_layout.setContentsMargins(0, 0, 0, 0)
        sql_layout.setSpacing(0)

        # SQL header with run button
        sql_header = QWidget()
        sql_header.setObjectName("dbSqlHeader")
        sql_header.setFixedHeight(28)
        sql_header.setStyleSheet(
            f"#dbSqlHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        sqlh_layout = QHBoxLayout(sql_header)
        sqlh_layout.setContentsMargins(8, 0, 8, 0)

        sql_label = QLabel("SQL QUERY")
        sql_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        sqlh_layout.addWidget(sql_label)
        sqlh_layout.addStretch()

        hint_label = QLabel("Ctrl+Enter to run")
        hint_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        sqlh_layout.addWidget(hint_label)

        # Run button (play icon)
        self._run_btn = QPushButton()
        self._run_btn.setObjectName("dbWinRunBtn")
        self._run_btn.setFixedSize(22, 22)
        self._run_btn.setToolTip("Run query (Ctrl+Enter)")
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_pixmap = QPixmap(16, 16)
        play_pixmap.fill(QColor(0, 0, 0, 0))
        play_p = QPainter(play_pixmap)
        play_p.setRenderHint(QPainter.RenderHint.Antialiasing)
        play_p.setBrush(QColor("#10a37f"))
        play_p.setPen(Qt.PenStyle.NoPen)
        play_p.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(14, 8), QPointF(4, 14)]))
        play_p.end()
        self._run_btn.setIcon(QIcon(play_pixmap))
        self._run_btn.setStyleSheet(
            "#dbWinRunBtn { background: transparent; border: none; }"
            "#dbWinRunBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        self._run_btn.clicked.connect(self._execute_query)
        sqlh_layout.addWidget(self._run_btn)

        sql_layout.addWidget(sql_header)

        # SQL text editor
        self._sql_editor = QPlainTextEdit()
        self._sql_editor.setPlaceholderText(
            "-- Write your SQL query here\nSELECT * FROM table_name LIMIT 100"
        )
        mono = QFont("Monospace", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._sql_editor.setFont(mono)
        self._sql_editor.setStyleSheet(
            f"QPlainTextEdit {{ background: #1a1a2e; color: {tc.get('text_primary')}; "
            f"border: none; padding: 10px 12px; font-size: 13px; "
            f"selection-background-color: {tc.get('accent_primary')}; }}"
        )
        sql_layout.addWidget(self._sql_editor)

        right_splitter.addWidget(sql_widget)

        # Results area
        results_widget = QWidget()
        r_layout = QVBoxLayout(results_widget)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_layout.setSpacing(0)

        # Results header
        results_header = QWidget()
        results_header.setObjectName("dbResultsHeader")
        results_header.setFixedHeight(28)
        results_header.setStyleSheet(
            f"#dbResultsHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        rh_layout = QHBoxLayout(results_header)
        rh_layout.setContentsMargins(8, 0, 6, 0)
        rh_layout.setSpacing(6)

        self._results_label = QLabel("RESULTS")
        self._results_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        rh_layout.addWidget(self._results_label)

        # View toggle: Table / Chart / Profile
        self._view_toggle = QComboBox()
        self._view_toggle.addItems(["📊 Table", "📈 Chart", "🔍 Profile"])
        self._view_toggle.setStyleSheet(_combo_dropdown_style())
        self._view_toggle.currentIndexChanged.connect(self._on_view_changed)
        rh_layout.addWidget(self._view_toggle)

        rh_layout.addStretch()

        self._results_status = QLabel("")
        self._results_status.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        rh_layout.addWidget(self._results_status)

        # Header action buttons: save snippet, export CSV
        self._snippet_btn = self._mk_header_btn("💾", "Save current SQL as a snippet")
        self._snippet_btn.clicked.connect(self._on_save_snippet)
        rh_layout.addWidget(self._snippet_btn)

        self._export_btn = self._mk_header_btn("⬇", "Export results to CSV")
        self._export_btn.clicked.connect(self._on_export_csv)
        rh_layout.addWidget(self._export_btn)

        r_layout.addWidget(results_header)

        # Results: Table / Chart / Profile views in a stacked widget so the
        # header dropdown can switch between them without re-running the query.
        self._current_table_name: str | None = None  # Track which table is shown
        self._results_stack = QStackedWidget()

        self._results_table = QTableWidget()
        self._results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._results_table.customContextMenuRequested.connect(self._show_results_menu)
        self._results_table.setStyleSheet(
            f"QTableWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; "
            f"gridline-color: {tc.get('border_secondary')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 4px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
            f"QTableWidget::item {{ padding: 4px; }}"
            f"QTableWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
        )
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._results_stack.addWidget(self._results_table)  # 0: table

        self._chart_widget = ResultChartWidget()
        self._results_stack.addWidget(self._chart_widget)  # 1: chart

        self._profile_widget = ResultProfileWidget()
        self._results_stack.addWidget(self._profile_widget)  # 2: profile

        r_layout.addWidget(self._results_stack)

        # Cached last result for view switching + export.
        self._last_result: QueryResult | None = None

        right_splitter.addWidget(results_widget)
        right_splitter.setSizes([200, 400])

        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([220, 200, 700])

        layout.addWidget(main_splitter)

        # Keyboard shortcut
        run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._sql_editor)
        run_shortcut.activated.connect(self._execute_query)

    def _load_schema(self) -> None:
        async def do_load():
            tables = await self._conn.get_schema()
            QTimer.singleShot(0, lambda: self._populate_schema(tables))

        safe_task(do_load(), name="db_window_schema")

    def _populate_schema(self, tables) -> None:
        self._schema_tree.clear()
        for table in tables:
            table_item = QTreeWidgetItem(self._schema_tree)
            table_item.setText(0, f"📋 {table.name}")
            table_item.setText(1, "table")
            for col in table.columns:
                col_item = QTreeWidgetItem(table_item)
                pk_marker = " 🔑" if col.primary_key else ""
                col_item.setText(0, f"  {col.name}{pk_marker}")
                col_item.setText(1, col.data_type)
        self._schema_tree.expandAll()

    def _on_table_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Double-click a table to query its contents."""
        if item.parent() is None:
            table_name = item.text(0).replace("📋 ", "")
            self._query_table(table_name)

    def _on_table_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Single-click a table to preview its contents."""
        if item.parent() is None:
            table_name = item.text(0).replace("📋 ", "")
            self._query_table(table_name)

    def _query_table(self, table_name: str) -> None:
        self._current_table_name = table_name
        sql = f'SELECT * FROM "{table_name}" LIMIT 100'
        self._sql_editor.setPlainText(sql)
        self._execute_query()

    def _execute_query(self) -> None:
        sql = self._sql_editor.toPlainText().strip()
        if not sql:
            return

        self._run_btn.setEnabled(False)
        self._results_status.setText("Executing...")

        # Always run via safe_task — qasync is the active event loop and
        # run_until_complete on a running loop raises a RuntimeError that
        # leaves a dangling never-awaited coroutine (the
        # `coroutine 'execute_query' was never awaited` warning).
        async def do_query():
            try:
                result = await self._conn.execute_query(sql)
                self._show_results(result)
            except Exception as e:
                # Without this catch the panel would be stuck on
                # "Executing..." with the Run button disabled forever.
                logger.exception("db_window: query execution crashed")
                self._results_status.setText(f"Error: {str(e)[:200]}")
                self._run_btn.setEnabled(True)

        safe_task(do_query(), name="db_window_query")

    def _show_results(self, result: QueryResult) -> None:
        self._run_btn.setEnabled(True)
        self._last_result = result

        # Record in history (best-effort, never blocks results display).
        try:
            store = get_notebook_store()
            store.add_history(
                connection=self._conn.name,
                sql=self._sql_editor.toPlainText().strip(),
                duration_ms=int((result.execution_time or 0) * 1000),
                row_count=result.row_count if not result.error else -1,
                error=result.error,
            )
            self._refresh_history()
        except Exception:
            logger.exception("db_window: could not record history")

        if result.error:
            self._results_status.setText(f"Error: {result.error[:80]}")
            self._results_table.clear()
            self._results_table.setRowCount(0)
            self._results_table.setColumnCount(0)
            self._chart_widget.set_results([], [])
            self._profile_widget.set_results([], [])
            return

        self._results_table.setColumnCount(len(result.columns))
        self._results_table.setHorizontalHeaderLabels(result.columns)
        self._results_table.setRowCount(result.row_count)
        self._results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        for r, row in enumerate(result.rows):
            for c, val in enumerate(row):
                display = str(val) if val is not None else "NULL"
                item = QTableWidgetItem(display[:500])
                if val is None:
                    item.setForeground(QColor(tc.get("text_muted")))
                self._results_table.setItem(r, c, item)

        # Populate the chart and profile views with the same data so the
        # user can switch instantly without re-running.
        self._chart_widget.set_results(result.columns, result.rows)
        self._profile_widget.set_results(result.columns, result.rows)

        elapsed = f"{result.execution_time:.3f}s" if result.execution_time else ""
        self._results_label.setText(
            f"RESULTS — {result.row_count:,} rows × {len(result.columns)} cols"
        )
        self._results_status.setText(elapsed)

    # ── Header actions: view toggle, snippet, export ────────────────

    def _mk_header_btn(self, glyph: str, tooltip: str) -> QPushButton:
        """Small flat header icon button (text glyph variant)."""
        btn = QPushButton(glyph)
        btn.setObjectName("dbWinHdrBtn")
        btn.setFixedSize(24, 24)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#dbWinHdrBtn { background: transparent; color: #cccccc; "
            "border: none; font-size: 14px; }"
            "#dbWinHdrBtn:hover { background: rgba(255,255,255,0.1); "
            "color: #ffffff; border-radius: 3px; }"
        )
        return btn

    def _on_view_changed(self, index: int) -> None:
        """Switch the results stack to Table (0) / Chart (1) / Profile (2)."""
        self._results_stack.setCurrentIndex(index)

    def _on_export_csv(self) -> None:
        """Export the current result set to a CSV file picked by the user."""
        if self._last_result is None or self._last_result.error or not self._last_result.rows:
            self._results_status.setText("Nothing to export — run a query first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export results to CSV",
            "results.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            import csv

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._last_result.columns)
                for row in self._last_result.rows:
                    writer.writerow(["" if v is None else v for v in row])
            self._results_status.setText(f"Exported {len(self._last_result.rows):,} rows → {path}")
        except OSError as e:
            logger.exception("db_window: CSV export failed")
            self._results_status.setText(f"Export failed: {e}")

    def _on_save_snippet(self) -> None:
        """Save the current SQL editor content as a named snippet."""
        sql = self._sql_editor.toPlainText().strip()
        if not sql:
            self._results_status.setText("Nothing to save — write a query first.")
            return
        name = _prompt_text(
            self,
            title="Save snippet",
            label="Snippet name:",
            placeholder="my-favourite-query",
        )
        if not name:
            return
        store = get_notebook_store()
        ok, msg = store.save_snippet(self._conn.name, name, sql)
        self._results_status.setText(msg)
        if ok:
            self._refresh_snippets()

    # ── Sidebar refresh helpers (defined in __init__'s setup_sidebar) ──

    def _refresh_history(self) -> None:
        if not hasattr(self, "_history_list"):
            return
        self._history_list.clear()
        store = get_notebook_store()
        for entry in store.list_history(self._conn.name, limit=50):
            preview = entry.sql.replace("\n", " ").strip()
            if len(preview) > 64:
                preview = preview[:61] + "…"
            label = preview
            if entry.error:
                label = f"⚠ {label}"
            elif entry.row_count >= 0:
                label = f"{label}  · {entry.row_count} rows"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.sql)
            item.setToolTip(entry.sql)
            self._history_list.addItem(item)

    def _refresh_snippets(self) -> None:
        if not hasattr(self, "_snippets_list"):
            return
        self._snippets_list.clear()
        store = get_notebook_store()
        for snip in store.list_snippets(self._conn.name):
            item = QListWidgetItem(f"⭐ {snip.name}")
            item.setData(Qt.ItemDataRole.UserRole, snip.sql)
            item.setToolTip(snip.sql)
            item.setData(Qt.ItemDataRole.UserRole + 1, snip.id)
            self._snippets_list.addItem(item)

    def _on_history_picked(self, item: QListWidgetItem) -> None:
        sql = item.data(Qt.ItemDataRole.UserRole)
        if sql:
            self._sql_editor.setPlainText(sql)
            self._sql_editor.setFocus()

    def _on_snippet_picked(self, item: QListWidgetItem) -> None:
        sql = item.data(Qt.ItemDataRole.UserRole)
        if sql:
            self._sql_editor.setPlainText(sql)
            self._sql_editor.setFocus()

    def _show_snippets_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        item = self._snippets_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #252526; color: #ddd; border: 1px solid #444; }"
            "QMenu::item { padding: 4px 20px; }"
            "QMenu::item:selected { background: #094771; }"
        )
        load_action = menu.addAction("Load into editor")
        delete_action = menu.addAction("Delete snippet")
        chosen = menu.exec(self._snippets_list.viewport().mapToGlobal(pos))
        if chosen == load_action:
            self._on_snippet_picked(item)
        elif chosen == delete_action:
            snip_id = item.data(Qt.ItemDataRole.UserRole + 1)
            if isinstance(snip_id, int):
                get_notebook_store().delete_snippet(snip_id)
                self._refresh_snippets()

    # ── Results context menu (CRUD) ─────────────────────────────────

    def _show_results_menu(self, pos) -> None:
        if not self._current_table_name:
            return

        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; font-size: {tc.FONT_SM}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        # Edit cell
        item = self._results_table.itemAt(pos)
        if item:
            row = item.row()
            col = item.column()
            col_name = self._results_table.horizontalHeaderItem(col).text() if col >= 0 else ""

            edit_action = menu.addAction(f"✏️  Edit cell ({col_name})")
            edit_action.triggered.connect(lambda: self._edit_cell(row, col))

            menu.addSeparator()

            delete_action = menu.addAction("🗑  Delete this row")
            delete_action.triggered.connect(lambda: self._delete_row(row))

        menu.addSeparator()

        add_action = menu.addAction("➕  Insert new row")
        add_action.triggered.connect(self._insert_row)

        menu.exec(self._results_table.viewport().mapToGlobal(pos))

    def _edit_cell(self, row: int, col: int) -> None:
        col_name = self._results_table.horizontalHeaderItem(col).text()
        current_val = (
            self._results_table.item(row, col).text() if self._results_table.item(row, col) else ""
        )

        dialog = _EditCellDialog(col_name, current_val, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_val = dialog.get_value()

        # Build WHERE clause from all columns in this row
        where = self._build_where_clause(row)
        if not where:
            self._results_status.setText("Error: Cannot identify row for update")
            return

        # Handle NULL
        if new_val.upper() == "NULL" or new_val == "":
            set_clause = f'"{col_name}" = NULL'
        else:
            escaped = new_val.replace("'", "''")
            set_clause = f"\"{col_name}\" = '{escaped}'"

        sql = f'UPDATE "{self._current_table_name}" SET {set_clause} WHERE {where}'
        self._execute_write(sql, f"Updated {col_name}")

    def _delete_row(self, row: int) -> None:
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Delete Row",
            "Are you sure you want to delete this row?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        where = self._build_where_clause(row)
        if not where:
            self._results_status.setText("Error: Cannot identify row for delete")
            return

        sql = f'DELETE FROM "{self._current_table_name}" WHERE {where}'
        self._execute_write(sql, "Row deleted")

    def _insert_row(self) -> None:
        if not self._current_table_name or self._results_table.columnCount() == 0:
            return

        columns = []
        for c in range(self._results_table.columnCount()):
            header = self._results_table.horizontalHeaderItem(c)
            if header:
                columns.append(header.text())

        from PyQt6.QtWidgets import QDialog as _QDialog

        dialog = _InsertRowDialog(self._current_table_name, columns, self)
        if dialog.exec() == _QDialog.DialogCode.Accepted:
            values = dialog.get_values()
            cols_str = ", ".join(f'"{c}"' for c in values.keys())
            vals_str = ", ".join(
                "NULL"
                if v.upper() == "NULL" or v == ""
                else f"'{v.replace(chr(39), chr(39) + chr(39))}'"
                for v in values.values()
            )
            sql = f'INSERT INTO "{self._current_table_name}" ({cols_str}) VALUES ({vals_str})'
            self._execute_write(sql, "Row inserted")

    def _build_where_clause(self, row: int) -> str:
        """Build a WHERE clause to identify a specific row using all column values."""
        conditions = []
        for c in range(self._results_table.columnCount()):
            header = self._results_table.horizontalHeaderItem(c)
            item = self._results_table.item(row, c)
            if not header or not item:
                continue
            col_name = header.text()
            val = item.text()
            if val == "NULL":
                conditions.append(f'"{col_name}" IS NULL')
            else:
                escaped = val.replace("'", "''")
                conditions.append(f"\"{col_name}\" = '{escaped}'")
        return " AND ".join(conditions) if conditions else ""

    def _execute_write(self, sql: str, success_msg: str) -> None:
        """Execute a write query and refresh the table.

        qasync owns the running event loop, so ``run_until_complete``
        would always raise ``RuntimeError`` from a Qt slot — the old
        try/except was effectively using the ``RuntimeError`` branch
        every time and the sync path was dead code. Stick to one model:
        schedule the work as an async task and update the UI when it
        finishes. The callbacks run on the qasync loop which in this
        app is the GUI thread, so ``setText`` is safe without an extra
        marshal.
        """
        self._results_status.setText("Executing...")

        table_name = self._current_table_name

        async def do_write() -> None:
            # Catch everything: a driver crash, a lost connection, or
            # asyncio cancellation would otherwise get swallowed by
            # ``safe_task`` and leave the status bar stuck on
            # "Executing...". Surface the error to the user directly.
            try:
                result = await self._conn.execute_query(sql)
            except Exception as exc:
                logger.exception("db_write failed")
                self._results_status.setText(f"Error: {exc}")
                return
            if result.error:
                self._results_status.setText(f"Error: {result.error[:80]}")
                return
            self._results_status.setText(success_msg)
            if table_name:
                self._query_table(table_name)

        safe_task(do_write(), name="db_write")


class _InsertRowDialog(QDialog):
    """Dialog for inserting a new row into a table."""

    def __init__(self, table_name: str, columns: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Insert Row — {table_name}")
        self.setMinimumWidth(450)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("insertHeader")
        header.setFixedHeight(40)
        header.setStyleSheet(
            f"#insertHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        h_label = QLabel(f"Insert into {table_name}")
        h_label.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(h_label)
        layout.addWidget(header)

        # Form
        from PyQt6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}")

        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(16, 12, 16, 12)
        form_layout.setSpacing(8)

        input_style = (
            f"QLineEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 6px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )

        self._inputs: dict[str, QLineEdit] = {}
        for col in columns:
            label = QLabel(col)
            label.setStyleSheet(
                f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_secondary')};"
            )
            form_layout.addWidget(label)

            inp = QLineEdit()
            inp.setPlaceholderText("NULL")
            inp.setStyleSheet(input_style)
            form_layout.addWidget(inp)
            self._inputs[col] = inp

        scroll.setWidget(form_widget)
        layout.addWidget(scroll)

        # Footer
        footer = QWidget()
        footer.setObjectName("insertFooter")
        footer.setFixedHeight(48)
        footer.setStyleSheet(
            f"#insertFooter {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(16, 0, 16, 0)
        f_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("insertCancel")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setStyleSheet(
            f"#insertCancel {{ background: transparent; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 0 16px; font-size: {tc.FONT_SM}px; }}"
            f"#insertCancel:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        f_layout.addWidget(cancel_btn)

        insert_btn = QPushButton("Insert")
        insert_btn.setObjectName("insertConfirm")
        insert_btn.setFixedHeight(30)
        insert_btn.setStyleSheet(
            f"#insertConfirm {{ background: {tc.get('accent_success')}; color: #fff; "
            f"border: none; border-radius: 4px; padding: 0 20px; "
            f"font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#insertConfirm:hover {{ background: #0eb87a; }}"
        )
        insert_btn.clicked.connect(self.accept)
        f_layout.addWidget(insert_btn)

        layout.addWidget(footer)

    def get_values(self) -> dict[str, str]:
        return {col: inp.text() for col, inp in self._inputs.items()}


class _EditCellDialog(QDialog):
    """Styled dialog for editing a single cell value."""

    def __init__(self, column_name: str, current_value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit — {column_name}")
        self.setFixedWidth(420)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("editCellHeader")
        header.setFixedHeight(40)
        header.setStyleSheet(
            f"#editCellHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        h_label = QLabel(f"Edit {column_name}")
        h_label.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(h_label)
        layout.addWidget(header)

        # Input area
        form = QWidget()
        f_layout = QVBoxLayout(form)
        f_layout.setContentsMargins(16, 16, 16, 12)
        f_layout.setSpacing(8)

        col_label = QLabel(column_name)
        col_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_secondary')};"
        )
        f_layout.addWidget(col_label)

        self._input = QPlainTextEdit()
        self._input.setPlainText(current_value)
        mono = QFont("Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._input.setFont(mono)
        self._input.setMinimumHeight(60)
        self._input.setMaximumHeight(150)
        self._input.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 8px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QPlainTextEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        f_layout.addWidget(self._input)

        hint = QLabel("Leave empty or type NULL for null value")
        hint.setStyleSheet(f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;")
        f_layout.addWidget(hint)

        layout.addWidget(form)

        # Footer
        footer = QWidget()
        footer.setObjectName("editCellFooter")
        footer.setFixedHeight(48)
        footer.setStyleSheet(
            f"#editCellFooter {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        ft_layout = QHBoxLayout(footer)
        ft_layout.setContentsMargins(16, 0, 16, 0)
        ft_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("editCellCancel")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"#editCellCancel {{ background: transparent; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 0 16px; font-size: {tc.FONT_SM}px; }}"
            f"#editCellCancel:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        ft_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("editCellSave")
        save_btn.setFixedHeight(30)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            f"#editCellSave {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 20px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#editCellSave:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        save_btn.clicked.connect(self.accept)
        ft_layout.addWidget(save_btn)

        layout.addWidget(footer)

        # Focus the input and select all
        self._input.setFocus()
        self._input.selectAll()

    def get_value(self) -> str:
        return self._input.toPlainText()


# ── Add Connection Dialog ───────────────────────────────────────────


class _AddConnectionDialog(QDialog):
    """Dialog to add a new database connection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Database Connection")
        self.setFixedWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("dbDialogHeader")
        header.setFixedHeight(44)
        header.setStyleSheet(
            f"#dbDialogHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        header_title = QLabel("New Database Connection")
        header_title.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(header_title)
        layout.addWidget(header)

        # Form area
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(20, 16, 20, 12)
        form_layout.setSpacing(14)

        input_style = (
            f"QLineEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 8px 12px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        label_style = (
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_secondary')}; margin-bottom: 2px;"
        )

        # Connection name
        name_label = QLabel("Connection Name")
        name_label.setStyleSheet(label_style)
        form_layout.addWidget(name_label)
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. production-db, local-dev")
        self._name_input.setStyleSheet(input_style)
        form_layout.addWidget(self._name_input)

        # Database type
        type_label = QLabel("Database Type")
        type_label.setStyleSheet(label_style)
        form_layout.addWidget(type_label)

        self._type_combo = QComboBox()
        self._type_combo.addItem("📁  SQLite", "sqlite")
        self._type_combo.addItem("🐘  PostgreSQL", "postgresql")
        self._type_combo.addItem("🐬  MySQL", "mysql")
        self._type_combo.setFixedHeight(36)
        self._type_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._type_combo.setStyleSheet(
            f"QComboBox {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 8px 12px; font-size: {tc.FONT_MD}px; }}"
            f"QComboBox:focus {{ border-color: {tc.get('accent_primary')}; }}"
            f"QComboBox::drop-down {{ border: none; width: 30px; }}"
            f"QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; "
            f"border-right: 5px solid transparent; border-top: 6px solid {tc.get('text_secondary')}; "
            f"margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"selection-background-color: {tc.get('bg_active')}; "
            f"padding: 4px; font-size: {tc.FONT_MD}px; }}"
        )
        self._type_combo.currentIndexChanged.connect(
            lambda idx: self._on_type_changed(self._type_combo.itemData(idx))
        )
        form_layout.addWidget(self._type_combo)

        # ── SQLite fields ──
        self._sqlite_widget = QWidget()
        sqlite_layout = QVBoxLayout(self._sqlite_widget)
        sqlite_layout.setContentsMargins(0, 0, 0, 0)
        sqlite_layout.setSpacing(8)

        sl = QLabel("Database File")
        sl.setStyleSheet(label_style)
        sqlite_layout.addWidget(sl)
        self._conn_input = QLineEdit()
        self._conn_input.setPlaceholderText("/path/to/database.db")
        self._conn_input.setStyleSheet(input_style)
        sqlite_layout.addWidget(self._conn_input)

        self._browse_btn = QPushButton("📂  Browse for file...")
        self._browse_btn.setObjectName("dbBrowseBtn")
        self._browse_btn.setFixedHeight(32)
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.setStyleSheet(
            f"#dbBrowseBtn {{ background: {tc.get('bg_input')}; "
            f"color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 4px; font-size: {tc.FONT_SM}px; padding: 0 12px; }}"
            f"#dbBrowseBtn:hover {{ border-color: {tc.get('accent_primary')}; "
            f"background: {tc.get('bg_hover')}; }}"
        )
        self._browse_btn.clicked.connect(self._browse_file)
        sqlite_layout.addWidget(self._browse_btn)
        form_layout.addWidget(self._sqlite_widget)

        # ── Server fields (PostgreSQL / MySQL) ──
        self._server_widget = QWidget()
        server_layout = QVBoxLayout(self._server_widget)
        server_layout.setContentsMargins(0, 0, 0, 0)
        server_layout.setSpacing(8)

        # Host + Port row
        host_row = QHBoxLayout()
        host_row.setSpacing(8)

        host_col = QVBoxLayout()
        hl = QLabel("Host")
        hl.setStyleSheet(label_style)
        host_col.addWidget(hl)
        self._host_input = QLineEdit()
        self._host_input.setPlaceholderText("localhost")
        self._host_input.setText("localhost")
        self._host_input.setStyleSheet(input_style)
        host_col.addWidget(self._host_input)
        host_row.addLayout(host_col, stretch=3)

        port_col = QVBoxLayout()
        pl = QLabel("Port")
        pl.setStyleSheet(label_style)
        port_col.addWidget(pl)
        self._port_input = QLineEdit()
        self._port_input.setPlaceholderText("5432")
        self._port_input.setText("5432")
        self._port_input.setStyleSheet(input_style)
        self._port_input.setFixedWidth(80)
        port_col.addWidget(self._port_input)
        host_row.addLayout(port_col)

        server_layout.addLayout(host_row)

        # Username
        ul = QLabel("Username")
        ul.setStyleSheet(label_style)
        server_layout.addWidget(ul)
        self._user_input = QLineEdit()
        self._user_input.setPlaceholderText("postgres")
        self._user_input.setStyleSheet(input_style)
        server_layout.addWidget(self._user_input)

        # Password
        pwl = QLabel("Password")
        pwl.setStyleSheet(label_style)
        server_layout.addWidget(pwl)
        self._pass_input = QLineEdit()
        self._pass_input.setPlaceholderText("Enter password")
        self._pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_input.setStyleSheet(input_style)
        server_layout.addWidget(self._pass_input)

        # Database name
        dbl = QLabel("Database")
        dbl.setStyleSheet(label_style)
        server_layout.addWidget(dbl)
        self._db_input = QLineEdit()
        self._db_input.setPlaceholderText("mydb")
        self._db_input.setStyleSheet(input_style)
        server_layout.addWidget(self._db_input)

        self._server_widget.setVisible(False)
        form_layout.addWidget(self._server_widget)

        # Help text
        self._help_label = QLabel("Select a SQLite database file (.db, .sqlite)")
        self._help_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; padding-top: 2px;"
        )
        self._help_label.setWordWrap(True)
        form_layout.addWidget(self._help_label)

        # Read-only checkbox (defaults to checked for safety)
        from PyQt6.QtWidgets import QCheckBox

        self._read_only_cb = QCheckBox("Read-only (recommended)")
        self._read_only_cb.setChecked(True)
        self._read_only_cb.setStyleSheet(
            f"QCheckBox {{ color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; margin-top: 4px; }}"
        )
        self._read_only_cb.setToolTip(
            "When checked, write statements (INSERT, UPDATE, DELETE, DDL) are blocked. "
            "Uncheck only for connections where you intentionally need write access."
        )
        form_layout.addWidget(self._read_only_cb)

        layout.addWidget(form_widget)
        layout.addStretch()

        # Footer with buttons
        footer = QWidget()
        footer.setObjectName("dbDialogFooter")
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            f"#dbDialogFooter {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(16, 0, 16, 0)
        f_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("dbCancelBtn")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"#dbCancelBtn {{ background: transparent; "
            f"color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 4px; padding: 0 16px; font-size: {tc.FONT_SM}px; }}"
            f"#dbCancelBtn:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        f_layout.addWidget(cancel_btn)

        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("dbDialogConnBtn")
        connect_btn.setFixedHeight(32)
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.setStyleSheet(
            f"#dbDialogConnBtn {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 20px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#dbDialogConnBtn:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        connect_btn.clicked.connect(self.accept)
        f_layout.addWidget(connect_btn)

        layout.addWidget(footer)

    def _on_type_changed(self, db_type: str) -> None:
        if db_type == "sqlite":
            self._sqlite_widget.setVisible(True)
            self._server_widget.setVisible(False)
            self._help_label.setText("Select a SQLite database file (.db, .sqlite)")
        elif db_type == "postgresql":
            self._sqlite_widget.setVisible(False)
            self._server_widget.setVisible(True)
            self._port_input.setText("5432")
            self._user_input.setPlaceholderText("postgres")
            self._help_label.setText(
                "Connects directly via asyncpg. Works with local, Docker, or remote databases."
            )
        elif db_type == "mysql":
            self._sqlite_widget.setVisible(False)
            self._server_widget.setVisible(True)
            self._port_input.setText("3306")
            self._user_input.setPlaceholderText("root")
            self._help_label.setText(
                "Connects directly via aiomysql. Works with local, Docker, or remote databases."
            )

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SQLite Database", "", "SQLite (*.db *.sqlite *.sqlite3);;All (*)"
        )
        if path:
            self._conn_input.setText(path)
            if not self._name_input.text():
                self._name_input.setText(Path(path).stem)

    def get_values(self) -> tuple[str, str, str, bool]:
        db_type = self._type_combo.currentData() or "sqlite"
        name = self._name_input.text().strip()
        read_only = self._read_only_cb.isChecked()

        if db_type == "sqlite":
            conn_str = self._conn_input.text().strip()
        else:
            # Build connection string from fields
            host = self._host_input.text().strip() or "localhost"
            port = self._port_input.text().strip() or (
                "5432" if db_type == "postgresql" else "3306"
            )
            user = self._user_input.text().strip()
            password = self._pass_input.text()
            database = self._db_input.text().strip()
            scheme = "postgresql" if db_type == "postgresql" else "mysql"

            if user and password:
                conn_str = f"{scheme}://{user}:{password}@{host}:{port}/{database}"
            elif user:
                conn_str = f"{scheme}://{user}@{host}:{port}/{database}"
            else:
                conn_str = f"{scheme}://{host}:{port}/{database}"

            # Auto-fill name from database if empty
            if not name and database:
                name = database

        return (name, db_type, conn_str, read_only)


#: Cached path to a painted chevron PNG used for dropdown arrows.
#: Qt's stylesheet engine doesn't reliably render CSS triangles or
#: SVG data URLs, but ``image: url(/tmp/...png)`` always works.
_DROPDOWN_ARROW_PATH: str | None = None


def _get_dropdown_arrow_path() -> str:
    """Lazily paint and cache a chevron PNG for QComboBox dropdown arrows."""
    global _DROPDOWN_ARROW_PATH
    if _DROPDOWN_ARROW_PATH is not None:
        return _DROPDOWN_ARROW_PATH
    import tempfile

    cache_dir = tempfile.mkdtemp(prefix="polyglot_combo_")
    path = f"{cache_dir}/dropdown_arrow.png"
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#cccccc"))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Chevron ▼: two lines forming a v
    painter.drawLine(2, 4, 6, 8)
    painter.drawLine(6, 8, 10, 4)
    painter.end()
    pixmap.save(path, "PNG")
    _DROPDOWN_ARROW_PATH = path
    return path


def _combo_dropdown_style() -> str:
    """Shared QComboBox stylesheet with a visible chevron dropdown arrow.

    Used by view toggles, chart selectors, etc. so every dropdown in
    the database panel has the same compact dark look with a real ▼.
    """
    arrow = _get_dropdown_arrow_path()
    return (
        "QComboBox { background: #1e1e1e; color: #ddd; border: 1px solid #444; "
        "border-radius: 3px; padding: 3px 24px 3px 10px; font-size: 11px; "
        "min-width: 100px; }"
        "QComboBox:hover { border-color: #0e639c; }"
        "QComboBox:focus { border-color: #0e639c; }"
        "QComboBox::drop-down { subcontrol-origin: padding; "
        "subcontrol-position: center right; width: 22px; "
        "border-left: 1px solid #444; background: transparent; }"
        "QComboBox::down-arrow { "
        f"image: url({arrow}); width: 12px; height: 12px; }}"
        "QComboBox QAbstractItemView { background: #252526; color: #ddd; "
        "selection-background-color: #094771; border: 1px solid #444; "
        "outline: none; padding: 2px; }"
    )


def _prompt_text(
    parent: QWidget,
    title: str,
    label: str,
    placeholder: str = "",
) -> str:
    """Show a styled single-line text prompt and return the entered value.

    Used by database_panel for snippet naming etc. Returns "" if the
    user cancelled.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(360)
    dlg.setStyleSheet("QDialog { background: #1e1e1e; }")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(10)

    lbl = QLabel(label)
    lbl.setStyleSheet("color: #ccc; font-size: 12px; font-weight: 600; background: transparent;")
    layout.addWidget(lbl)

    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setStyleSheet(
        "QLineEdit { background: #252526; color: #e0e0e0; border: 1px solid #333; "
        "border-radius: 4px; padding: 7px 10px; font-size: 13px; }"
        "QLineEdit:focus { border-color: #0e639c; }"
    )
    layout.addWidget(field)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch()

    cancel = QPushButton("Cancel")
    cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel.setStyleSheet(
        "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
        "border-radius: 4px; padding: 6px 14px; font-size: 12px; }"
        "QPushButton:hover { background: #4a4a4a; }"
    )
    cancel.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel)

    ok = QPushButton("Save")
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setDefault(True)
    ok.setStyleSheet(
        "QPushButton { background: #0e639c; color: white; border: none; "
        "border-radius: 4px; padding: 6px 16px; font-size: 12px; font-weight: 600; }"
        "QPushButton:hover { background: #1a8ae8; }"
    )
    ok.clicked.connect(dlg.accept)
    btn_row.addWidget(ok)

    layout.addLayout(btn_row)
    field.returnPressed.connect(dlg.accept)
    field.setFocus()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    return field.text().strip()
