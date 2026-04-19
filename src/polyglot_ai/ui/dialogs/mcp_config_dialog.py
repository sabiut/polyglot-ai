"""Dialog for configuring an MCP server connection.

Extracted from ``settings_dialog.py``. The settings dialog opens this
when the user clicks "Connect" on an unconfigured MCP marketplace
entry (e.g. a GitHub token, a Postgres connection string). The
dialog is a plain form: one labeled input per ``config_fields`` entry,
with a dedicated "directory" variant that includes a Browse button.

Returning an empty dict from :meth:`get_values` signals "user left a
required field blank" — the caller treats that as equivalent to
hitting Cancel so the server isn't added with half-filled credentials.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


class MCPConfigDialog(QDialog):
    """Custom styled dialog for configuring an MCP server connection."""

    def __init__(
        self,
        server_name: str,
        icon: str,
        config_fields: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Connect to {server_name}")
        self.setFixedWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; }}"
        )

        self._fields: dict[str, tuple[dict, QLineEdit | str]] = {}
        self._config_fields = config_fields

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("mcpDialogHeader")
        header.setFixedHeight(48)
        header.setStyleSheet(
            f"#mcpDialogHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        title = QLabel(f"{icon}  Connect to {server_name}")
        title.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(title)
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
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_secondary')};"
        )
        hint_style = f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; padding-top: 0px;"

        for cf in config_fields:
            label = QLabel(cf.get("label", cf["key"]))
            label.setStyleSheet(label_style)
            form_layout.addWidget(label)

            if cf["type"] == "directory":
                # Directory picker
                row = QHBoxLayout()
                row.setSpacing(6)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText("/path/to/directory")
                line_edit.setStyleSheet(input_style)
                row.addWidget(line_edit, stretch=1)

                browse_btn = QPushButton("Browse")
                browse_btn.setFixedHeight(32)
                browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                browse_btn.setStyleSheet(
                    f"QPushButton {{ background: {tc.get('bg_input')}; "
                    f"color: {tc.get('text_primary')}; "
                    f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; "
                    f"padding: 0 12px; font-size: {tc.FONT_SM}px; }}"
                    f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }}"
                )

                def _browse(edit=line_edit, field_label=cf.get("label", "directory")):
                    from PyQt6.QtWidgets import QFileDialog

                    path = QFileDialog.getExistingDirectory(self, f"Select {field_label}")
                    if path:
                        edit.setText(path)

                browse_btn.clicked.connect(_browse)
                row.addWidget(browse_btn)
                form_layout.addLayout(row)
                self._fields[cf["key"]] = (cf, line_edit)
            else:
                line_edit = QLineEdit()
                line_edit.setStyleSheet(input_style)
                if cf["type"] == "password":
                    line_edit.setEchoMode(QLineEdit.EchoMode.Password)
                    line_edit.setPlaceholderText("Enter secret value")
                else:
                    line_edit.setPlaceholderText(cf.get("label", cf["key"]))
                form_layout.addWidget(line_edit)
                self._fields[cf["key"]] = (cf, line_edit)

            if cf.get("description"):
                hint = QLabel(cf["description"])
                hint.setStyleSheet(hint_style)
                hint.setWordWrap(True)
                form_layout.addWidget(hint)

        layout.addWidget(form_widget)
        layout.addStretch()

        # Footer
        footer = QWidget()
        footer.setObjectName("mcpDialogFooter")
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            f"#mcpDialogFooter {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(16, 0, 16, 0)
        f_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("mcpCancelBtn")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"#mcpCancelBtn {{ background: transparent; "
            f"color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 4px; padding: 0 16px; font-size: {tc.FONT_SM}px; }}"
            f"#mcpCancelBtn:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        f_layout.addWidget(cancel_btn)

        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("mcpConnectBtn")
        connect_btn.setFixedHeight(32)
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.setStyleSheet(
            f"#mcpConnectBtn {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 20px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#mcpConnectBtn:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        connect_btn.clicked.connect(self.accept)
        f_layout.addWidget(connect_btn)

        layout.addWidget(footer)

    def get_values(self) -> dict[str, str]:
        """Return collected field values. Returns empty dict if any required field is empty."""
        result: dict[str, str] = {}
        for key, (cf, widget) in self._fields.items():
            val = widget.text().strip() if isinstance(widget, QLineEdit) else ""
            if not val:
                return {}  # Any missing field = cancel
            result[key] = val
        return result
