"""Connect-over-SSH dialog: host, user, port, identity file."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.ssh import SshTarget, parse_target, valid_host
from polyglot_ai.ui import theme_colors as tc


class SshConnectDialog(QDialog):
    """Collects an :class:`SshTarget`; ``target()`` is None until accepted."""

    def __init__(
        self,
        recent: list[str] | None = None,
        config_hosts: list[str] | None = None,
        parent: QWidget | None = None,
        prefill: SshTarget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New SSH Session")
        self.setMinimumWidth(420)
        self._target: SshTarget | None = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Opens <code>ssh</code> in the terminal panel. Recent targets and "
            "<code>~/.ssh/config</code> aliases are suggested below."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {tc.get('text_secondary')}; font-size: {tc.FONT_SM}px;")
        layout.addWidget(intro)

        form = QFormLayout()
        self._host = QComboBox()
        self._host.setEditable(True)
        seen: list[str] = []
        for name in [*(recent or []), *(config_hosts or [])]:
            if name and name not in seen:
                seen.append(name)
        self._host.addItems(seen)
        self._host.setCurrentText("")
        self._host.lineEdit().setPlaceholderText("host, IP, or config alias — or user@host:port")
        self._host.currentTextChanged.connect(self._on_host_text)
        form.addRow("Host:", self._host)

        self._user = QLineEdit()
        self._user.setPlaceholderText("optional")
        form.addRow("User:", self._user)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(22)
        form.addRow("Port:", self._port)

        key_row = QHBoxLayout()
        self._identity = QLineEdit()
        self._identity.setPlaceholderText("optional, e.g. ~/.ssh/id_ed25519 or a .pem")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_identity)
        key_row.addWidget(self._identity, stretch=1)
        key_row.addWidget(browse)
        form.addRow("Identity file:", key_row)
        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet(f"color: {tc.get('accent_error')}; font-size: {tc.FONT_SM}px;")
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if prefill is not None:
            self._host.setCurrentText(prefill.host)
            self._user.setText(prefill.user)
            self._port.setValue(prefill.port or 22)
            self._identity.setText(prefill.identity_file)

    def _on_host_text(self, text: str) -> None:
        # Typing/pasting "user@host:2222" splits into the other fields.
        if "@" in text or ":" in text:
            parsed = parse_target(text)
            if parsed is not None:
                if parsed.user:
                    self._user.setText(parsed.user)
                if parsed.port != 22:
                    self._port.setValue(parsed.port)
                self._host.blockSignals(True)
                self._host.setCurrentText(parsed.host)
                self._host.blockSignals(False)

    def _browse_identity(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose identity file")
        if path:
            self._identity.setText(path)

    def build_target(self) -> SshTarget | None:
        host = self._host.currentText().strip()
        if not valid_host(host):
            return None
        return SshTarget(
            host=host,
            user=self._user.text().strip(),
            port=self._port.value(),
            identity_file=self._identity.text().strip(),
        )

    def _on_accept(self) -> None:
        target = self.build_target()
        if target is None:
            self._error.setText("Enter a hostname, IP address or ~/.ssh/config alias.")
            self._error.show()
            return
        self._target = target
        self.accept()

    def target(self) -> SshTarget | None:
        return self._target
