"""GitHub connection consent dialog.

Lives in ``ui/dialogs/`` rather than inside ``chat_panel`` so the chat
panel is focused on chat, and so the dialog is easy to surface from
other panels (e.g. a future Settings → Integrations page).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class GitHubConnectDialog(QDialog):
    """Styled dialog that collects a GitHub token for the MCP GitHub server.

    Returns the token via :meth:`get_token` after ``accept()``. The
    caller is responsible for persisting it to the keyring.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._token = ""
        self.setWindowTitle("Connect GitHub")
        self.setFixedSize(440, 560)
        self.setStyleSheet("QDialog { background-color: #202020; }")

        # Request dark title bar on GNOME/KDE via Qt palette hints.
        try:
            dark_palette = self.palette()
            dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
            dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
            dark_palette.setColor(QPalette.ColorRole.Button, QColor("#333333"))
            dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#cccccc"))
            self.setPalette(dark_palette)
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(0)

        # Close button row
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #666; font-size: 16px; "
            "border: none; border-radius: 14px; }"
            "QPushButton:hover { background: #333; color: #ccc; }"
        )
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        # Icons — drawn as colored circles with letters. Import here to
        # avoid a top-level dependency on chat_message (keeps the dialog
        # independent of the chat panel).
        icons_row = QHBoxLayout()
        icons_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icons_row.setSpacing(10)
        from polyglot_ai.ui.panels.chat_message import AvatarWidget

        icons_row.addWidget(AvatarWidget("C", "#10a37f"))  # Codex
        dots = QLabel("···")
        dots.setStyleSheet("color: #555; font-size: 18px; background: transparent;")
        dots.setFixedWidth(30)
        dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icons_row.addWidget(dots)
        icons_row.addWidget(AvatarWidget("G", "#8b5cf6"))  # GitHub
        layout.addLayout(icons_row)

        layout.addSpacing(14)

        title = QLabel("Connect GitHub")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #e8e8e8; background: transparent;"
        )
        layout.addWidget(title)

        subtitle = QLabel("via MCP Server")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 11px; color: #666; background: transparent;")
        layout.addWidget(subtitle)

        layout.addSpacing(18)

        info_items = [
            (
                "Permissions always respected",
                "Access is limited to permissions you explicitly grant.\n"
                "Disable access anytime to revoke.",
            ),
            (
                "You're in control",
                "Your token is stored locally in your system keyring.\n"
                "It is never sent to any third party.",
            ),
            (
                "Connectors may introduce risk",
                "Use fine-grained tokens with minimal scopes.\n"
                "Only grant access to repos you need.",
            ),
        ]
        for i, (heading, desc) in enumerate(info_items):
            if i > 0:
                sep = QWidget()
                sep.setFixedHeight(1)
                sep.setStyleSheet("background-color: #333;")
                layout.addWidget(sep)

            section = QWidget()
            section.setStyleSheet("background: transparent;")
            sec_layout = QVBoxLayout(section)
            sec_layout.setContentsMargins(4, 10, 4, 10)
            sec_layout.setSpacing(3)

            h = QLabel(heading)
            h.setStyleSheet(
                "font-size: 13px; font-weight: 600; color: #e0e0e0; background: transparent;"
            )
            sec_layout.addWidget(h)

            d = QLabel(desc)
            d.setWordWrap(True)
            d.setMinimumHeight(36)
            d.setStyleSheet("font-size: 12px; color: #9a9a9a; background: transparent;")
            sec_layout.addWidget(d)

            layout.addWidget(section)

        layout.addSpacing(14)

        self._token_input = QLineEdit()
        self._token_input.setPlaceholderText("Paste your token here...")
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.setStyleSheet(
            "QLineEdit { background: #161616; color: #d4d4d4; border: 1px solid #3a3a3a; "
            "border-radius: 10px; padding: 10px 14px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #555; }"
        )
        layout.addWidget(self._token_input)

        layout.addSpacing(14)

        connect_btn = QPushButton("Continue to GitHub  ↗")
        connect_btn.setFixedHeight(44)
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.setStyleSheet(
            "QPushButton { background-color: #f0f0f0; color: #111; font-size: 14px; "
            "font-weight: 600; border: none; border-radius: 22px; "
            "font-family: -apple-system, 'Segoe UI', sans-serif; }"
            "QPushButton:hover { background-color: #fff; }"
            "QPushButton:pressed { background-color: #ddd; }"
        )
        connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(connect_btn)

    def _on_connect(self) -> None:
        token = self._token_input.text().strip()
        if not token:
            # Flash the input border red to signal the validation failure.
            self._token_input.setStyleSheet(
                "QLineEdit { background: #161616; color: #d4d4d4; "
                "border: 1px solid #d32f2f; border-radius: 10px; "
                "padding: 10px 14px; font-size: 13px; }"
            )
            return
        self._token = token
        self.accept()

    def get_token(self) -> str:
        return self._token
