"""Dialogs and style helpers used by the database panel.

Extracted from ``database_panel.py`` so the panel file can focus on
the connection / query / schema controller logic. Public surface:

* :class:`InsertRowDialog` — modal form for inserting a row into the
  current table.
* :class:`EditCellDialog` — modal editor for a single cell value.
* :class:`AddConnectionDialog` — the new-connection form (SQLite /
  PostgreSQL / MySQL) with read-only-by-default checkbox.
* :func:`prompt_text` — simple dark-themed text-input dialog (used
  for snippet naming).
* :func:`combo_dropdown_style` — shared QComboBox stylesheet with a
  painted chevron. Backed by :func:`get_dropdown_arrow_path` which
  lazily produces a tmp-file PNG (Qt stylesheets don't reliably
  render CSS triangles or SVG data URLs).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


class InsertRowDialog(QDialog):
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


class EditCellDialog(QDialog):
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


class AddConnectionDialog(QDialog):
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


def get_dropdown_arrow_path() -> str:
    """Lazily paint and cache a chevron PNG for QComboBox dropdown arrows."""
    global _DROPDOWN_ARROW_PATH
    if _DROPDOWN_ARROW_PATH is not None:
        return _DROPDOWN_ARROW_PATH

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


def combo_dropdown_style() -> str:
    """Shared QComboBox stylesheet with a visible chevron dropdown arrow.

    Used by view toggles, chart selectors, etc. so every dropdown in
    the database panel has the same compact dark look with a real ▼.
    """
    arrow = get_dropdown_arrow_path()
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


def prompt_text(
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
