"""Database explorer panel — browse schemas, run queries, view results."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import safe_task
from polyglot_ai.core.db_explorer import DatabaseManager, QueryResult
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


class DatabasePanel(QWidget):
    """Database explorer sidebar — schema browser + SQL query executor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db_manager = DatabaseManager()
        self._mcp_client = None
        self._active_connection: str | None = None

        self._setup_ui()

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            f"background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
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

        add_btn = QPushButton("+")
        add_btn.setFixedSize(24, 24)
        add_btn.setToolTip("Add database connection")
        add_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 3px; color: {tc.get('text_primary')}; font-size: 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        add_btn.clicked.connect(self._show_add_dialog)
        h_layout.addWidget(add_btn)

        layout.addWidget(header)

        # Connection selector
        conn_bar = QWidget()
        conn_bar.setStyleSheet(f"background: {tc.get('bg_base')};")
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

        connect_btn = QPushButton("Connect")
        connect_btn.setFixedHeight(26)
        connect_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; "
            f"border: none; border-radius: 3px; padding: 0 10px; font-size: {tc.FONT_SM}px; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        connect_btn.clicked.connect(self._connect_selected)
        conn_layout.addWidget(connect_btn)

        layout.addWidget(conn_bar)

        # Main splitter: schema tree + query area
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )

        # Schema tree
        self._schema_tree = QTreeWidget()
        self._schema_tree.setHeaderLabels(["Name", "Type"])
        self._schema_tree.setColumnWidth(0, 180)
        self._schema_tree.setStyleSheet(
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 2px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_secondary')}; padding: 3px; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )
        self._schema_tree.itemDoubleClicked.connect(self._on_schema_double_click)
        splitter.addWidget(self._schema_tree)

        # Query area
        query_widget = QWidget()
        q_layout = QVBoxLayout(query_widget)
        q_layout.setContentsMargins(0, 0, 0, 0)
        q_layout.setSpacing(0)

        # SQL editor
        self._sql_editor = QPlainTextEdit()
        self._sql_editor.setPlaceholderText("SELECT * FROM table_name LIMIT 100")
        mono = QFont("Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._sql_editor.setFont(mono)
        self._sql_editor.setMaximumHeight(120)
        self._sql_editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: none; border-top: 1px solid {tc.get('border_secondary')}; "
            f"padding: 6px; font-size: {tc.FONT_MD}px; }}"
        )
        q_layout.addWidget(self._sql_editor)

        # Run bar
        run_bar = QWidget()
        run_bar.setFixedHeight(32)
        run_bar.setStyleSheet(f"background: {tc.get('bg_surface')};")
        rb_layout = QHBoxLayout(run_bar)
        rb_layout.setContentsMargins(8, 0, 8, 0)
        rb_layout.setSpacing(4)

        self._run_btn = QPushButton("▶ Run")
        self._run_btn.setFixedHeight(24)
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_success')}; color: #fff; "
            f"border: none; border-radius: 3px; padding: 0 12px; "
            f"font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_success_muted')}; }}"
        )
        self._run_btn.clicked.connect(self._execute_query)
        rb_layout.addWidget(self._run_btn)

        rb_layout.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        rb_layout.addWidget(self._status_label)

        q_layout.addWidget(run_bar)

        # Results table
        self._results_table = QTableWidget()
        self._results_table.setStyleSheet(
            f"QTableWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; gridline-color: {tc.get('border_secondary')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_secondary')}; padding: 3px; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; }}"
            f"QTableWidget::item {{ padding: 3px; }}"
            f"QTableWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
        )
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        q_layout.addWidget(self._results_table)

        splitter.addWidget(query_widget)
        splitter.setSizes([200, 300])

        layout.addWidget(splitter)

        # Keyboard shortcut: Ctrl+Enter to run query
        run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._sql_editor)
        run_shortcut.activated.connect(self._execute_query)

    # ── Connection Management ───────────────────────────────────────

    def _show_add_dialog(self) -> None:
        dialog = _AddConnectionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, db_type, conn_str = dialog.get_values()
            if name and conn_str:
                self._db_manager.add_connection(
                    name, db_type, conn_str, mcp_client=self._mcp_client
                )
                self._conn_combo.addItem(f"{name} ({db_type})")
                self._conn_combo.setCurrentIndex(self._conn_combo.count() - 1)

    def _on_connection_changed(self, text: str) -> None:
        if not text:
            self._active_connection = None
            return
        # Extract connection name (before " (type)")
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
                # Load schema
                tables = await conn.get_schema()
                QTimer.singleShot(0, lambda: self._populate_schema(tables))
            else:
                self._status_label.setText(f"Error: {msg[:60]}")

        safe_task(do_connect(), name="db_connect")

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
        """Double-click a table name to generate SELECT query."""
        if item.parent() is None:
            # It's a table — generate query
            table_name = item.text(0)
            self._sql_editor.setPlainText(f"SELECT * FROM {table_name} LIMIT 100")

    # ── Query Execution ─────────────────────────────────────────────

    def _execute_query(self) -> None:
        sql = self._sql_editor.toPlainText().strip()
        if not sql or not self._active_connection:
            return

        conn = self._db_manager.get_connection(self._active_connection)
        if not conn:
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText("Running...")
        self._status_label.setText("Executing...")

        async def do_query():
            result = await conn.execute_query(sql)
            QTimer.singleShot(0, lambda: self._show_results(result))

        safe_task(do_query(), name="db_query")

    def _show_results(self, result: QueryResult) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶ Run")

        if result.error:
            self._status_label.setText(f"Error: {result.error[:80]}")
            self._results_table.clear()
            self._results_table.setRowCount(0)
            self._results_table.setColumnCount(0)
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
                self._results_table.setItem(r, c, QTableWidgetItem(display[:500]))

        elapsed = f"{result.execution_time:.3f}s" if result.execution_time else ""
        self._status_label.setText(
            f"{result.row_count:,} rows × {len(result.columns)} cols | {elapsed}"
        )


class _AddConnectionDialog(QDialog):
    """Dialog to add a new database connection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Database Connection")
        self.setMinimumWidth(400)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; }}"
        )

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("my-database")
        self._name_input.setStyleSheet(
            f"background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; padding: 4px 8px;"
        )
        form.addRow("Name:", self._name_input)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["sqlite", "postgresql", "mysql"])
        self._type_combo.setStyleSheet(
            f"background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; padding: 4px 8px;"
        )
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Type:", self._type_combo)

        self._conn_input = QLineEdit()
        self._conn_input.setPlaceholderText("/path/to/database.db")
        self._conn_input.setStyleSheet(
            f"background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; padding: 4px 8px;"
        )
        form.addRow("Connection:", self._conn_input)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.clicked.connect(self._browse_file)
        form.addRow("", self._browse_btn)

        layout.addLayout(form)

        # Help text
        self._help_label = QLabel("Path to SQLite database file")
        self._help_label.setStyleSheet(f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;")
        self._help_label.setWordWrap(True)
        layout.addWidget(self._help_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_type_changed(self, db_type: str) -> None:
        if db_type == "sqlite":
            self._conn_input.setPlaceholderText("/path/to/database.db")
            self._help_label.setText("Path to SQLite database file")
            self._browse_btn.setVisible(True)
        elif db_type == "postgresql":
            self._conn_input.setPlaceholderText("postgresql://user:pass@host:5432/dbname")
            self._help_label.setText(
                "PostgreSQL connection string. Requires PostgreSQL MCP server connected in Settings."
            )
            self._browse_btn.setVisible(False)
        elif db_type == "mysql":
            self._conn_input.setPlaceholderText("mysql://user:pass@host:3306/dbname")
            self._help_label.setText(
                "MySQL connection string. Requires MySQL MCP server connected in Settings."
            )
            self._browse_btn.setVisible(False)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SQLite Database", "", "SQLite (*.db *.sqlite *.sqlite3);;All (*)"
        )
        if path:
            self._conn_input.setText(path)
            if not self._name_input.text():
                self._name_input.setText(Path(path).stem)

    def get_values(self) -> tuple[str, str, str]:
        return (
            self._name_input.text().strip(),
            self._type_combo.currentText(),
            self._conn_input.text().strip(),
        )
