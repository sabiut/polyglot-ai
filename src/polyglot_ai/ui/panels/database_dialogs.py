"""Dialogs and style helpers used by the database panel.

Extracted from ``database_panel.py`` so the panel file can focus on
the connection / query / schema controller logic. Public surface:

* :class:`InsertRowDialog` — modal form for inserting a row into the
  current table.
* :class:`EditCellDialog` — modal editor for a single cell value.
* :class:`AddConnectionDialog` — the new-connection form (every engine
  in ``core.db_dialects``, TLS, SSH / Session Manager tunnels, IAM
  sign-in) with read-only-by-default checkbox.
* :func:`prompt_text` — simple dark-themed text-input dialog (used
  for snippet naming).
* :func:`combo_dropdown_style` — shared QComboBox stylesheet with a
  painted chevron. Backed by :func:`get_dropdown_arrow_path` which
  lazily produces a tmp-file PNG (Qt stylesheets don't reliably
  render CSS triangles or SVG data URLs).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
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

from polyglot_ai.core.db_dialects import (
    ENGINE_CHOICES,
    ENGINES,
    SSL_MODES,
    build_url,
    ssl_modes_for,
)
from polyglot_ai.core.db_tunnel import TunnelSpec
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
            f"#insertConfirm {{ background: {tc.get('accent_success')}; "
            f"color: {tc.get('text_on_accent')}; "
            f"border: none; border-radius: 4px; padding: 0 20px; "
            f"font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#insertConfirm:hover {{ background: {tc.get('accent_success_hover')}; }}"
        )
        insert_btn.setToolTip("Runs the INSERT now — this writes to the database")
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
        save_btn.setToolTip("Runs the UPDATE now — this writes to the database")
        save_btn.clicked.connect(self.accept)
        ft_layout.addWidget(save_btn)

        layout.addWidget(footer)

        # Focus the input and select all
        self._input.setFocus()
        self._input.selectAll()

    def get_value(self) -> str:
        return self._input.toPlainText()


@dataclass
class NewConnection:
    """What the Add Connection dialog produced."""

    name: str
    db_type: str
    connection_string: str  # holds the password: goes to the keyring
    read_only: bool
    options: dict = field(default_factory=dict)  # no secrets: saved with the name


class AddConnectionDialog(QDialog):
    """Dialog to add a database connection, or edit a saved one.

    Covers every engine in :mod:`polyglot_ai.core.db_dialects`, plus the
    three things a database in a private network needs: encryption, a
    tunnel (SSH bastion or AWS Session Manager) and, on RDS, IAM
    authentication. ``prefill`` seeds the fields — the AWS panel uses it
    to open the dialog for an RDS instance. With ``editing`` the fields
    come from a saved connection (``db_dialects.form_values``) and
    ``taken_names`` are the other connections' names, which it may not
    be renamed to.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        prefill: dict | None = None,
        *,
        editing: bool = False,
        taken_names: Iterable[str] = (),
    ) -> None:
        super().__init__(parent)
        self._editing = editing
        self._taken_names = set(taken_names)
        title = "Edit Database Connection" if editing else "New Database Connection"
        self.setWindowTitle(title)
        self.setFixedWidth(500)
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
        header_title = QLabel(title)
        header_title.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(header_title)
        layout.addWidget(header)

        self._input_style = (
            f"QLineEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 7px 10px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._label_style = (
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_secondary')}; margin-bottom: 2px;"
        )
        self._combo_style = (
            f"QComboBox {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
            f"padding: 7px 10px; font-size: {tc.FONT_MD}px; }}"
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

        # The form scrolls: with a tunnel and IAM both showing it is
        # taller than a small laptop screen.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}")
        form_widget = QWidget()
        self._form_widget = form_widget
        form_widget.setStyleSheet(f"background: {tc.get('bg_base')};")
        form = QVBoxLayout(form_widget)
        form.setContentsMargins(20, 14, 20, 10)
        form.setSpacing(8)

        self._name_input = self._line("e.g. production-db, local-dev")
        self._add(form, "Connection Name", self._name_input)

        self._type_combo = self._combo(ENGINE_CHOICES)
        self._type_combo.currentIndexChanged.connect(lambda _i: self._sync_visibility())
        self._add(form, "Database Type", self._type_combo)

        # ── SQLite fields ──
        self._sqlite_widget = QWidget()
        sqlite_layout = QVBoxLayout(self._sqlite_widget)
        sqlite_layout.setContentsMargins(0, 0, 0, 0)
        sqlite_layout.setSpacing(8)
        self._conn_input = self._line("/path/to/database.db")
        self._add(sqlite_layout, "Database File", self._conn_input)
        self._browse_btn = QPushButton("Browse for file...")
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
        form.addWidget(self._sqlite_widget)

        # ── Server fields (every other engine) ──
        self._server_widget = QWidget()
        server = QVBoxLayout(self._server_widget)
        server.setContentsMargins(0, 0, 0, 0)
        server.setSpacing(8)

        self._host_input = self._line("localhost", "localhost")
        self._port_input = self._line("5432", "5432", width=80)
        self._add_row(server, ("Host", self._host_input, 3), ("Port", self._port_input, 0))

        self._user_input = self._line("postgres")
        self._add(server, "Username", self._user_input)

        self._auth_combo = self._combo([("Password", "password"), ("AWS IAM token (RDS)", "iam")])
        self._auth_combo.currentIndexChanged.connect(lambda _i: self._sync_visibility())
        self._auth_block = self._add(server, "Sign in with", self._auth_combo)

        self._pass_input = self._line("Enter password")
        self._pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_block = self._add(server, "Password", self._pass_input)

        self._db_input = self._line("mydb")
        self._db_label = QLabel("Database")
        self._add(server, self._db_label, self._db_input)

        self._ssl_combo = self._combo(SSL_MODES)
        self._ssl_combo.currentIndexChanged.connect(lambda _i: self._sync_visibility())
        self._add(server, "Encryption (TLS)", self._ssl_combo)
        self._ca_input = self._line("CA bundle, e.g. ~/global-bundle.pem (blank: system CAs)")
        self._ca_block = self._add(server, "Certificate authority file", self._ca_input)

        self._route_combo = self._combo(
            [
                ("Directly", "direct"),
                ("SSH tunnel through a bastion host", "ssh"),
                ("AWS Session Manager through an EC2 instance", "ssm"),
            ]
        )
        self._route_combo.currentIndexChanged.connect(lambda _i: self._sync_visibility())
        self._add(server, "Connect", self._route_combo)

        self._ssh_widget = QWidget()
        ssh = QVBoxLayout(self._ssh_widget)
        ssh.setContentsMargins(0, 0, 0, 0)
        ssh.setSpacing(8)
        self._ssh_host = self._line("bastion.example.com")
        self._ssh_port = self._line("22", "22", width=80)
        self._add_row(ssh, ("Bastion host", self._ssh_host, 3), ("Port", self._ssh_port, 0))
        self._ssh_user = self._line("ec2-user")
        self._ssh_key = self._line("Key file (blank: your SSH agent / ~/.ssh/config)")
        self._add_row(ssh, ("SSH user", self._ssh_user, 1), ("Key file", self._ssh_key, 2))
        server.addWidget(self._ssh_widget)

        self._ssm_widget = QWidget()
        ssm = QVBoxLayout(self._ssm_widget)
        ssm.setContentsMargins(0, 0, 0, 0)
        ssm.setSpacing(8)
        self._ssm_target = QComboBox()
        self._ssm_target.setEditable(True)
        self._ssm_target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._ssm_target.setFixedHeight(34)
        self._ssm_target.setStyleSheet(self._combo_style)
        self._ssm_target.lineEdit().setPlaceholderText("i-0123456789abcdef0")
        self._add(ssm, "EC2 instance in the database's network", self._ssm_target)
        server.addWidget(self._ssm_widget)

        # Shared by Session Manager and IAM sign-in.
        self._aws_widget = QWidget()
        aws = QVBoxLayout(self._aws_widget)
        aws.setContentsMargins(0, 0, 0, 0)
        aws.setSpacing(8)
        self._aws_profile = self._line("default")
        self._aws_region = self._line("us-east-1")
        self._add_row(aws, ("AWS profile", self._aws_profile, 1), ("Region", self._aws_region, 1))
        server.addWidget(self._aws_widget)

        form.addWidget(self._server_widget)

        self._help_label = QLabel("")
        self._help_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; padding-top: 2px;"
        )
        self._help_label.setWordWrap(True)
        form.addWidget(self._help_label)

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
        form.addWidget(self._read_only_cb)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color: {tc.get('accent_error')}; font-size: {tc.FONT_SM}px;"
        )
        self._error_label.setVisible(False)
        form.addWidget(self._error_label)
        form.addStretch()

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, stretch=1)

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

        connect_btn = QPushButton("Save" if editing else "Connect")
        connect_btn.setObjectName("dbDialogConnBtn")
        connect_btn.setFixedHeight(32)
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.setStyleSheet(
            f"#dbDialogConnBtn {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 20px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#dbDialogConnBtn:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        connect_btn.setToolTip("Saves the connection — passwords are stored in your system keyring")
        connect_btn.clicked.connect(self.accept)
        f_layout.addWidget(connect_btn)

        layout.addWidget(footer)

        self._last_type = ""
        self._sync_visibility()
        if prefill:
            self.prefill(prefill)
        # Tall enough to show the whole form when it fits on screen (the
        # scroll area alone would ask for very little); it scrolls past that.
        chrome = header.height() + footer.height() + 8
        self.resize(500, min(760, self._form_widget.sizeHint().height() + chrome))

    # ── small builders ──

    def _line(self, placeholder: str, text: str = "", width: int = 0) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setText(text)
        edit.setStyleSheet(self._input_style)
        if width:
            edit.setFixedWidth(width)
        return edit

    def _combo(self, choices: list[tuple[str, str]]) -> QComboBox:
        combo = QComboBox()
        for label, data in choices:
            combo.addItem(label, data)
        combo.setFixedHeight(34)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setStyleSheet(self._combo_style)
        return combo

    def _add(self, layout, label, widget: QWidget) -> QWidget:
        """Label above widget, in a block that can be hidden as one."""
        block = QWidget()
        column = QVBoxLayout(block)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        if isinstance(label, str):
            label = QLabel(label)
        label.setStyleSheet(self._label_style)
        column.addWidget(label)
        column.addWidget(widget)
        layout.addWidget(block)
        return block

    def _add_row(self, layout, *fields: tuple[str, QWidget, int]) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, widget, stretch in fields:
            holder = QVBoxLayout()
            holder.setSpacing(3)
            caption = QLabel(label)
            caption.setStyleSheet(self._label_style)
            holder.addWidget(caption)
            holder.addWidget(widget)
            row.addLayout(holder, stretch)
        layout.addLayout(row)

    # ── behaviour ──

    def _db_type(self) -> str:
        return self._type_combo.currentData() or "sqlite"

    def _sync_visibility(self) -> None:
        db_type = self._db_type()
        is_sqlite = db_type == "sqlite"
        self._sqlite_widget.setVisible(is_sqlite)
        self._server_widget.setVisible(not is_sqlite)
        if is_sqlite:
            self._help_label.setText("Select a SQLite database file (.db, .sqlite)")
            self._last_type = db_type
            return

        engine = ENGINES[db_type]
        if db_type != self._last_type:
            # Engine changed: move the defaults along, but only where the
            # field still holds the previous engine's default.
            previous = ENGINES.get(self._last_type)
            if not self._port_input.text() or (
                previous and self._port_input.text() == str(previous.default_port)
            ):
                self._port_input.setText(str(engine.default_port))
            self._port_input.setPlaceholderText(str(engine.default_port))
            self._user_input.setPlaceholderText(engine.default_user)
            self._db_label.setText(engine.database_label)
            self._db_input.setPlaceholderText(engine.default_database or "mydb")
            self._offer_ssl_modes(db_type)
            self._last_type = db_type

        # IAM tokens exist for RDS PostgreSQL and MySQL/MariaDB/Aurora only.
        iam_possible = db_type in ("postgresql", "mysql")
        if not iam_possible:
            self._auth_combo.setCurrentIndex(0)
        self._auth_block.setVisible(iam_possible)
        iam = iam_possible and self._auth_combo.currentData() == "iam"
        self._pass_block.setVisible(not iam)

        self._ca_block.setVisible(self._ssl_combo.currentData() == "verify")
        route = self._route_combo.currentData()
        self._ssh_widget.setVisible(route == "ssh")
        self._ssm_widget.setVisible(route == "ssm")
        self._aws_widget.setVisible(iam or route == "ssm")

        notes = {
            "postgresql": "Connects with asyncpg.",
            "mysql": "Connects with aiomysql. Also used for MariaDB and Aurora MySQL.",
            "mssql": "Connects with pymssql. The certificate can't be verified for "
            "SQL Server; “Required” still encrypts the connection.",
            "oracle": "Connects with python-oracledb (no Oracle client needed). "
            "With encryption on, use the TLS listener's port (2484 on RDS).",
        }
        text = notes[db_type]
        if route == "ssh":
            text += " The tunnel uses your SSH agent or the key file; it can't ask for a password."
        elif route == "ssm":
            text += " Session Manager needs the AWS CLI and its session-manager-plugin."
        if iam:
            text += " IAM sign-in needs the rds-db:connect permission and a database user set up for it."
        self._help_label.setText(text)

    def _offer_ssl_modes(self, db_type: str) -> None:
        """List only the encryption modes this engine's driver can honour."""
        self._select_ssl_mode(self._ssl_combo.currentData(), db_type)

    def _select_ssl_mode(self, mode: str | None, db_type: str) -> None:
        choices = ssl_modes_for(db_type)
        offered = [data for _label, data in choices]
        if mode not in offered:
            # "Verify" on an engine that can't: the nearest honest choice
            # is encryption without the check, never no encryption.
            mode = "require" if mode == "verify" else offered[0]
        self._ssl_combo.blockSignals(True)
        self._ssl_combo.clear()
        for label, data in choices:
            self._ssl_combo.addItem(label, data)
        self._ssl_combo.setCurrentIndex(offered.index(mode))
        self._ssl_combo.blockSignals(False)

    def prefill(self, values: dict) -> None:
        """Seed the form. Keys: name, db_type, engine_label, host, port, user,
        database, ssl, route ("ssh"/"ssm"), profile, region, read_only,
        bastions ([(label, instance_id)] offered for Session Manager) — and,
        when a saved connection is being edited, the rest of what it holds:
        path (SQLite), password, auth ("iam"), ca_file, ssh_host, ssh_port,
        ssh_user, ssh_key, ssm_target."""
        label = values.get("engine_label")
        index = self._type_combo.findText(label) if label else -1
        if index < 0 and values.get("db_type"):
            index = self._type_combo.findData(values["db_type"])
        if index >= 0:
            self._type_combo.setCurrentIndex(index)
        for key, edit in (
            ("name", self._name_input),
            ("host", self._host_input),
            ("port", self._port_input),
            ("user", self._user_input),
            ("database", self._db_input),
            ("profile", self._aws_profile),
            ("region", self._aws_region),
            ("path", self._conn_input),
            ("password", self._pass_input),
            ("ca_file", self._ca_input),
            ("ssh_host", self._ssh_host),
            ("ssh_port", self._ssh_port),
            ("ssh_user", self._ssh_user),
            ("ssh_key", self._ssh_key),
        ):
            if values.get(key) not in (None, ""):
                edit.setText(str(values[key]))
        index = self._auth_combo.findData(values.get("auth"))
        if index >= 0:
            self._auth_combo.setCurrentIndex(index)
        if values.get("ssl"):
            self._select_ssl_mode(values["ssl"], self._db_type())
        index = self._route_combo.findData(values.get("route"))
        if index >= 0:
            self._route_combo.setCurrentIndex(index)
        for text, instance_id in values.get("bastions") or []:
            self._ssm_target.addItem(text, instance_id)
        self._ssm_target.setCurrentIndex(-1)
        self._ssm_target.setEditText(str(values.get("ssm_target") or ""))
        if "read_only" in values:
            self._read_only_cb.setChecked(bool(values["read_only"]))
        self._sync_visibility()
        if values.get("note"):
            self._help_label.setText(f"{values['note']} {self._help_label.text()}")

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SQLite Database", "", "SQLite (*.db *.sqlite *.sqlite3);;All (*)"
        )
        if path:
            self._conn_input.setText(path)
            if not self._name_input.text():
                self._name_input.setText(Path(path).stem)

    def _ssm_instance(self) -> str:
        """The instance ID: a picked suggestion's ID, or whatever was typed."""
        text = self._ssm_target.currentText().strip()
        index = self._ssm_target.findText(text)
        if index >= 0 and self._ssm_target.itemData(index):
            return str(self._ssm_target.itemData(index))
        return text.split()[0] if text else ""

    def _problem(self) -> str:
        """Why the form can't be saved yet, or ''."""
        value = self.get_values()
        if not value.name:
            return "Give the connection a name."
        if value.name in self._taken_names:
            return f"Another connection is already called “{value.name}”. Choose a different name."
        if value.db_type == "sqlite":
            return "" if value.connection_string else "Choose a database file."
        if not self._host_input.text().strip():
            return "Enter the database host."
        port = self._port_input.text().strip()
        if port and not (port.isdigit() and 0 < int(port) < 65536):
            return "The port must be a number between 1 and 65535."
        tunnel = value.options.get("tunnel") or {}
        if tunnel.get("kind") == "ssh" and not tunnel.get("host"):
            return "Enter the bastion host for the SSH tunnel."
        if tunnel.get("kind") == "ssm" and not tunnel.get("target"):
            return "Choose or enter the EC2 instance for Session Manager."
        if value.options.get("iam") is not None and not self._user_input.text().strip():
            return "IAM sign-in needs the database user name."
        return ""

    def accept(self) -> None:
        problem = self._problem()
        if problem:
            self._error_label.setText(problem)
            self._error_label.setVisible(True)
            return
        super().accept()

    def get_values(self) -> NewConnection:
        db_type = self._db_type()
        name = self._name_input.text().strip()
        read_only = self._read_only_cb.isChecked()

        if db_type == "sqlite":
            return NewConnection(name, db_type, self._conn_input.text().strip(), read_only)

        engine = ENGINES[db_type]
        host = self._host_input.text().strip() or "localhost"
        port = self._port_input.text().strip() or str(engine.default_port)
        user = self._user_input.text().strip()
        database = self._db_input.text().strip()
        iam = db_type in ("postgresql", "mysql") and self._auth_combo.currentData() == "iam"
        profile = self._aws_profile.text().strip()
        region = self._aws_region.text().strip()

        # ``url_encoded`` marks the URL as built by build_url(), so its
        # parts are decoded when read back.
        options: dict = {"url_encoded": True, "ssl": self._ssl_combo.currentData()}
        label = self._type_combo.currentText()
        if self._type_combo.findData(db_type) != self._type_combo.currentIndex():
            # MariaDB and Aurora share a type with MySQL / PostgreSQL; kept
            # so the connection reopens under the name it was created with.
            options["engine_label"] = label
        ca_file = self._ca_input.text().strip()
        if options["ssl"] == "verify" and ca_file:
            options["ca_file"] = ca_file
        if iam:
            options["iam"] = {"profile": profile, "region": region}
        route = self._route_combo.currentData()
        if route == "ssh":
            ssh_port = self._ssh_port.text().strip()
            options["tunnel"] = TunnelSpec(
                kind="ssh",
                host=self._ssh_host.text().strip(),
                user=self._ssh_user.text().strip(),
                port=int(ssh_port) if ssh_port.isdigit() else 22,
                identity_file=self._ssh_key.text().strip(),
            ).to_dict()
        elif route == "ssm":
            options["tunnel"] = TunnelSpec(
                kind="ssm", target=self._ssm_instance(), profile=profile, region=region
            ).to_dict()

        password = "" if iam else self._pass_input.text()
        conn_str = build_url(db_type, host, port, user, password, database)
        # Auto-fill name from database if empty
        if not name and database:
            name = database
        return NewConnection(name, db_type, conn_str, read_only, options)


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
    pen = QPen(QColor(tc.get("text_primary")))
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
        f"QComboBox {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
        f"border: 1px solid {tc.get('border_menu')}; "
        f"border-radius: 3px; padding: 3px 24px 3px 10px; font-size: {tc.FONT_SM}px; "
        "min-width: 100px; }"
        f"QComboBox:hover {{ border-color: {tc.get('accent_primary')}; }}"
        f"QComboBox:focus {{ border-color: {tc.get('accent_primary')}; }}"
        "QComboBox::drop-down { subcontrol-origin: padding; "
        "subcontrol-position: center right; width: 22px; "
        f"border-left: 1px solid {tc.get('border_menu')}; background: transparent; }}"
        "QComboBox::down-arrow { "
        f"image: url({arrow}); width: 12px; height: 12px; }}"
        f"QComboBox QAbstractItemView {{ background: {tc.get('bg_surface')}; "
        f"color: {tc.get('text_primary')}; "
        f"selection-background-color: {tc.get('bg_active')}; "
        f"border: 1px solid {tc.get('border_menu')}; "
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
    dlg.setStyleSheet(f"QDialog {{ background: {tc.get('bg_base')}; }}")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(10)

    lbl = QLabel(label)
    lbl.setStyleSheet(
        f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; "
        "font-weight: 600; background: transparent;"
    )
    layout.addWidget(lbl)

    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setStyleSheet(
        f"QLineEdit {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
        f"border: 1px solid {tc.get('border_secondary')}; "
        f"border-radius: 4px; padding: 7px 10px; font-size: {tc.FONT_BASE}px; }}"
        f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
    )
    layout.addWidget(field)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch()

    cancel = QPushButton("Cancel")
    cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel.setStyleSheet(
        f"QPushButton {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
        f"border: 1px solid {tc.get('border_input')}; "
        f"border-radius: 4px; padding: 6px 14px; font-size: {tc.FONT_MD}px; }}"
        f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
    )
    cancel.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel)

    ok = QPushButton("Save")
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setDefault(True)
    ok.setStyleSheet(
        f"QPushButton {{ background: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; "
        f"border: none; border-radius: 4px; padding: 6px 16px; "
        f"font-size: {tc.FONT_MD}px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
    )
    ok.clicked.connect(dlg.accept)
    btn_row.addWidget(ok)

    layout.addLayout(btn_row)
    field.returnPressed.connect(dlg.accept)
    field.setFocus()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    return field.text().strip()
