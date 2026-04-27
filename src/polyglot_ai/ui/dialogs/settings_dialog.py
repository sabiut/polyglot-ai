"""Settings dialog — modern sidebar navigation with MCP marketplace."""

from __future__ import annotations

import asyncio
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.keyring_store import KeyringStore
from polyglot_ai.core.settings import SettingsManager
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.dialogs.mcp_config_dialog import MCPConfigDialog

logger = logging.getLogger(__name__)

PROVIDERS = [
    {
        "name": "openai",
        "display": "OpenAI",
        "placeholder": "sk-...",
        "url": "platform.openai.com/api-keys",
    },
    {
        "name": "anthropic",
        "display": "Anthropic",
        "placeholder": "sk-ant-...",
        "url": "console.anthropic.com/settings/keys",
    },
    {
        "name": "google",
        "display": "Google (Gemini)",
        "placeholder": "AIza...",
        "url": "aistudio.google.com/apikey",
    },
    {
        "name": "deepseek",
        "display": "DeepSeek",
        "placeholder": "sk-...",
        "url": "platform.deepseek.com/api_keys",
    },
]

# ── Shared Styles ────────────────────────────────────────────────
_CARD_STYLE = (
    f"QGroupBox {{"
    f"  background-color: {tc.get('bg_card')};"
    f"  border: 1px solid {tc.get('border_card')};"
    f"  border-radius: {tc.RADIUS_MD}px;"
    f"  margin-top: 6px;"
    f"  padding: 16px 14px 12px 14px;"
    f"  font-weight: bold;"
    f"  color: {tc.get('text_heading')};"
    f"}}"
    f"QGroupBox::title {{"
    f"  subcontrol-origin: margin;"
    f"  left: 14px;"
    f"  padding: 0 6px;"
    f"}}"
)
_BTN_PRIMARY = (
    f"QPushButton {{"
    f"  background-color: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; font-weight: 600;"
    f"  padding: 7px 18px; border: none; border-radius: {tc.RADIUS_MD}px; font-size: {tc.FONT_MD}px;"
    f"}}"
    f"QPushButton:hover {{ background-color: {tc.get('accent_primary_hover')}; }}"
    f"QPushButton:pressed {{ background-color: {tc.get('accent_primary_pressed')}; }}"
    f"QPushButton:disabled {{ background-color: {tc.get('bg_hover')}; color: {tc.get('text_muted')}; }}"
)
_BTN_SUCCESS = (
    f"QPushButton {{"
    f"  background-color: {tc.get('accent_success')}; color: {tc.get('text_on_accent')}; font-weight: 600;"
    f"  padding: 7px 18px; border: none; border-radius: {tc.RADIUS_MD}px; font-size: {tc.FONT_MD}px;"
    f"}}"
    f"QPushButton:hover {{ background-color: {tc.get('accent_success_hover')}; }}"
)
_BTN_DANGER = (
    f"QPushButton {{"
    f"  background-color: {tc.get('accent_danger')}; color: {tc.get('text_on_accent')}; font-weight: 600;"
    f"  padding: 7px 18px; border: none; border-radius: {tc.RADIUS_MD}px; font-size: {tc.FONT_MD}px;"
    f"}}"
    f"QPushButton:hover {{ background-color: {tc.get('accent_danger_hover')}; }}"
)
_BTN_OUTLINE = (
    f"QPushButton {{"
    f"  background-color: transparent; color: #aaa; font-size: {tc.FONT_SM}px;"
    f"  padding: 5px 12px; border: 1px solid {tc.get('border_input')}; border-radius: 5px;"
    f"}}"
    f"QPushButton:hover {{ background-color: {tc.get('border_secondary')}; color: {tc.get('text_heading')}; }}"
)
_INPUT_STYLE = (
    f"QLineEdit {{"
    f"  background-color: {tc.get('bg_base')}; color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')};"
    f"  border-radius: 5px; padding: 6px 10px; font-size: {tc.FONT_BASE}px;"
    f"}}"
    f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
)
_SECTION_TITLE = f"font-size: {tc.FONT_XL}px; font-weight: bold; color: {tc.get('text_heading')}; margin-bottom: 2px;"
_SECTION_DESC = f"font-size: {tc.FONT_MD}px; color: {tc.get('text_tertiary')}; margin-bottom: 12px;"


class SettingsDialog(QDialog):
    """Application settings dialog with sidebar navigation."""

    oauth_status_changed = pyqtSignal(str, str)
    claude_oauth_status_changed = pyqtSignal(str, str)

    def __init__(
        self,
        settings: SettingsManager,
        keyring_store: KeyringStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._keyring = keyring_store
        self._save_task: asyncio.Task | None = None
        self._mcp_client = None  # Set externally if available

        self.setWindowTitle("Settings")
        self.setMinimumSize(700, 540)
        self.resize(780, 600)
        self.setStyleSheet(f"QDialog {{ background-color: {tc.get('bg_base')}; }}")

        self.oauth_status_changed.connect(self._set_openai_oauth_status)
        self.claude_oauth_status_changed.connect(self._set_claude_oauth_status)

        # Main layout: sidebar | content
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Sidebar ──
        sidebar = QWidget()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; border-right: 1px solid {tc.get('border_secondary')};"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 12, 0, 12)
        sidebar_layout.setSpacing(0)

        # App title in sidebar
        title = QLabel("Settings")
        title.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: bold; color: {tc.get('text_tertiary')}; "
            f"padding: 8px 16px 12px 16px;"
        )
        sidebar_layout.addWidget(title)

        # Nav items
        self._nav_list = QListWidget()
        self._nav_list.setStyleSheet(
            f"QListWidget {{"
            f"  background: transparent; border: none; outline: none;"
            f"  font-size: {tc.FONT_BASE}px;"
            f"}}"
            f"QListWidget::item {{"
            f"  color: {tc.get('text_primary')}; padding: 10px 16px; border: none;"
            f"  border-left: 3px solid transparent;"
            f"}}"
            f"QListWidget::item:selected {{"
            f"  color: {tc.get('text_on_accent')}; background-color: {tc.get('bg_surface_overlay')};"
            f"  border-left: 3px solid {tc.get('accent_primary')};"
            f"}}"
            f"QListWidget::item:hover:!selected {{"
            f"  background-color: {tc.get('bg_hover_subtle')}; color: {tc.get('text_heading')};"
            f"}}"
        )
        self._nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        sections = [
            ("🔑  Accounts",),
            ("✏️  Editor",),
            ("🤖  AI",),
            ("💻  Terminal",),
            ("🔌  MCP Servers",),
        ]
        for (label,) in sections:
            item = QListWidgetItem(label)
            item.setSizeHint(item.sizeHint().__class__(190, 40))
            self._nav_list.addItem(item)

        self._nav_list.setCurrentRow(0)
        self._nav_list.currentRowChanged.connect(self._switch_section)
        sidebar_layout.addWidget(self._nav_list)
        sidebar_layout.addStretch()

        # Version label
        ver = QLabel("v0.1.0")
        ver.setStyleSheet(
            f"color: {tc.get('border_input')}; font-size: {tc.FONT_XS}px; padding: 8px 16px;"
        )
        sidebar_layout.addWidget(ver)

        main_layout.addWidget(sidebar)

        # ── Content area ──
        content_area = QWidget()
        content_area.setStyleSheet(f"background-color: {tc.get('bg_base')};")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Stacked widget for sections
        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_accounts_section())
        self._stack.addWidget(self._create_editor_section())
        self._stack.addWidget(self._create_ai_section())
        self._stack.addWidget(self._create_terminal_section())
        self._stack.addWidget(self._create_mcp_section())
        content_layout.addWidget(self._stack)

        # Bottom buttons
        btn_bar = QWidget()
        btn_bar.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; border-top: 1px solid {tc.get('border_secondary')};"
        )
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(16, 10, 16, 10)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN_OUTLINE)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save Changes")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.clicked.connect(self._save_and_close)
        self._save_btn = save_btn
        btn_layout.addWidget(save_btn)

        content_layout.addWidget(btn_bar)
        main_layout.addWidget(content_area)

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client
        self._refresh_mcp_cards()

    def _switch_section(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    # ── Section: Accounts ────────────────────────────────────────

    def _create_accounts_section(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)

        # Header
        h = QLabel("Accounts & API Keys")
        h.setStyleSheet(_SECTION_TITLE)
        layout.addWidget(h)
        d = QLabel("Sign in with your subscription or add API keys for pay-as-you-go access.")
        d.setStyleSheet(_SECTION_DESC)
        d.setWordWrap(True)
        layout.addWidget(d)

        # ── OAuth card ──
        oauth_card = QGroupBox("Subscription Sign-In")
        oauth_card.setStyleSheet(_CARD_STYLE)
        oauth_layout = QVBoxLayout(oauth_card)
        oauth_layout.setSpacing(8)

        row = QHBoxLayout()
        self._openai_oauth_btn = QPushButton("Sign in with ChatGPT")
        self._openai_oauth_btn.setStyleSheet(_BTN_SUCCESS)
        self._openai_oauth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._openai_oauth_btn.clicked.connect(self._login_openai_oauth)
        row.addWidget(self._openai_oauth_btn)

        self._openai_oauth_status = QLabel("")
        self._openai_oauth_status.setStyleSheet(
            f"font-size: {tc.FONT_MD}px; color: {tc.get('text_tertiary')};"
        )
        row.addWidget(self._openai_oauth_status)
        row.addStretch()

        logout_btn = QPushButton("Sign Out")
        logout_btn.setStyleSheet(_BTN_OUTLINE)
        logout_btn.clicked.connect(self._logout_openai_oauth)
        row.addWidget(logout_btn)
        oauth_layout.addLayout(row)

        # Check current status
        from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient
        from polyglot_ai.core.bridge import EventBus

        _check = OpenAIOAuthClient(EventBus())
        if _check.is_authenticated:
            self._set_openai_oauth_status("Signed in ✓", tc.get("accent_success_muted"))
        del _check

        note = QLabel("Works with ChatGPT Plus, Pro, Business, and Enterprise plans.")
        note.setStyleSheet(f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')};")
        note.setWordWrap(True)
        oauth_layout.addWidget(note)
        layout.addWidget(oauth_card)

        # ── Claude OAuth card ──
        claude_card = QGroupBox("Claude Subscription")
        claude_card.setStyleSheet(_CARD_STYLE)
        claude_layout_inner = QVBoxLayout(claude_card)
        claude_layout_inner.setSpacing(8)

        claude_row = QHBoxLayout()
        self._claude_oauth_btn = QPushButton("Sign in with Claude")
        self._claude_oauth_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_claude')}; color: {tc.get('text_on_accent')}; font-size: {tc.FONT_BASE}px; "
            f"font-weight: 600; border: none; border-radius: {tc.RADIUS_MD}px; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_claude_hover')}; }}"
        )
        self._claude_oauth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._claude_oauth_btn.clicked.connect(self._login_claude_oauth)
        claude_row.addWidget(self._claude_oauth_btn)

        self._claude_oauth_status = QLabel("")
        self._claude_oauth_status.setStyleSheet(
            f"font-size: {tc.FONT_MD}px; color: {tc.get('text_tertiary')};"
        )
        claude_row.addWidget(self._claude_oauth_status)
        claude_row.addStretch()

        claude_logout_btn = QPushButton("Sign Out")
        claude_logout_btn.setStyleSheet(_BTN_OUTLINE)
        claude_logout_btn.clicked.connect(self._logout_claude_oauth)
        claude_row.addWidget(claude_logout_btn)
        claude_layout_inner.addLayout(claude_row)

        # Check Claude auth status
        from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient

        _check_claude = ClaudeOAuthClient(EventBus())
        if _check_claude.is_authenticated:
            sub = _check_claude._subscription_type or ""
            self._set_claude_oauth_status(
                f"Signed in ✓{' (' + sub + ')' if sub else ''}", tc.get("accent_success_muted")
            )
        del _check_claude

        claude_note = QLabel("Works with Claude Pro, Max, and Team plans.")
        claude_note.setStyleSheet(f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')};")
        claude_note.setWordWrap(True)
        claude_layout_inner.addWidget(claude_note)
        layout.addWidget(claude_card)

        # ── API Keys card ──
        api_card = QGroupBox("API Keys (Pay-as-you-go)")
        api_card.setStyleSheet(_CARD_STYLE)
        api_layout = QVBoxLayout(api_card)
        api_layout.setSpacing(10)

        self._api_inputs: dict[str, QLineEdit] = {}
        self._test_labels: dict[str, QLabel] = {}

        for provider in PROVIDERS:
            row = QHBoxLayout()
            row.setSpacing(8)

            # Status dot
            has_key = bool(self._keyring.get_key(provider["name"]))
            dot = QLabel("●")
            dot.setFixedWidth(14)
            dot.setStyleSheet(
                f"color: {tc.get('accent_success_muted') if has_key else tc.get('border_input')}; font-size: {tc.FONT_LG}px;"
            )
            row.addWidget(dot)

            # Provider name
            name_lbl = QLabel(provider["display"])
            name_lbl.setFixedWidth(100)
            name_lbl.setStyleSheet(
                f"font-weight: bold; font-size: {tc.FONT_BASE}px; color: {tc.get('text_heading')};"
            )
            row.addWidget(name_lbl)

            # Key input
            key_input = QLineEdit()
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            key_input.setPlaceholderText(provider["placeholder"])
            key_input.setStyleSheet(_INPUT_STYLE)
            existing = self._keyring.get_key(provider["name"])
            if existing:
                key_input.setText(existing)
            self._api_inputs[provider["name"]] = key_input
            row.addWidget(key_input, stretch=1)

            # "Get key" button — opens the provider's dashboard URL in
            # the user's default browser. The actual API key still has
            # to be pasted into the field on the left; this is just a
            # discovery/convenience shortcut. We expose it for every
            # provider that has a ``url`` entry in ``PROVIDERS`` so the
            # dashboard for OpenAI / Anthropic / Google / DeepSeek is
            # one click away regardless of which one the user picks.
            url_path = provider.get("url", "")
            if url_path:
                full_url = url_path if url_path.startswith("http") else f"https://{url_path}"
                get_key_btn = QPushButton("Get key")
                get_key_btn.setFixedWidth(70)
                get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                get_key_btn.setToolTip(f"Open {url_path} in your browser")
                get_key_btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {tc.get('accent_primary')}; "
                    f"  border: 1px solid {tc.get('border_input')}; "
                    f"  border-radius: {tc.RADIUS_SM}px; padding: 5px; "
                    f"  font-size: {tc.FONT_SM}px; }}"
                    f"QPushButton:hover {{ background: {tc.get('bg_hover')}; "
                    f"  border-color: {tc.get('accent_primary')}; }}"
                )

                def _open_url(_=False, u=full_url):
                    from PyQt6.QtCore import QUrl
                    from PyQt6.QtGui import QDesktopServices

                    QDesktopServices.openUrl(QUrl(u))

                get_key_btn.clicked.connect(_open_url)
                row.addWidget(get_key_btn)

            # Test button
            test_btn = QPushButton("Test")
            test_btn.setFixedWidth(55)
            test_btn.setStyleSheet(
                f"QPushButton {{ background: {tc.get('border_secondary')}; color: #ccc; border: 1px solid {tc.get('border_input')};"
                f"  border-radius: {tc.RADIUS_SM}px; padding: 5px; font-size: {tc.FONT_SM}px; }}"
                f"QPushButton:hover {{ background: {tc.get('border_card')}; }}"
            )
            pname = provider["name"]
            test_btn.clicked.connect(lambda checked, p=pname: self._test_provider(p))
            row.addWidget(test_btn)

            # Test result
            test_label = QLabel("")
            test_label.setFixedWidth(80)
            test_label.setStyleSheet(
                f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')};"
            )
            self._test_labels[provider["name"]] = test_label
            row.addWidget(test_label)

            api_layout.addLayout(row)

        key_note = QLabel("🔒 Keys are stored securely in your system keyring.")
        key_note.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_muted')}; margin-top: 4px;"
        )
        api_layout.addWidget(key_note)
        layout.addWidget(api_card)

        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    # ── Section: Editor ──────────────────────────────────────────

    def _create_editor_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)

        h = QLabel("Editor")
        h.setStyleSheet(_SECTION_TITLE)
        layout.addWidget(h)
        d = QLabel("Configure the built-in code editor.")
        d.setStyleSheet(_SECTION_DESC)
        layout.addWidget(d)

        card = QGroupBox()
        card.setStyleSheet(_CARD_STYLE)
        form = QFormLayout(card)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._font_family = QComboBox()
        self._font_family.addItems(["Monospace", "Courier New", "DejaVu Sans Mono", "Fira Code"])
        self._font_family.setCurrentText(self._settings.get("editor.font_family"))
        form.addRow("Font Family:", self._font_family)

        self._font_size = QSpinBox()
        self._font_size.setRange(8, 24)
        self._font_size.setValue(self._settings.get("editor.font_size"))
        form.addRow("Font Size:", self._font_size)

        self._tab_size = QSpinBox()
        self._tab_size.setRange(2, 8)
        self._tab_size.setValue(self._settings.get("editor.tab_size"))
        form.addRow("Tab Size:", self._tab_size)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["dark", "light"])
        self._theme_combo.setCurrentText(self._settings.get("theme"))
        form.addRow("Theme:", self._theme_combo)

        layout.addWidget(card)
        layout.addStretch()
        return page

    # ── Section: AI ──────────────────────────────────────────────

    def _create_ai_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)

        h = QLabel("AI Assistant")
        h.setStyleSheet(_SECTION_TITLE)
        layout.addWidget(h)
        d = QLabel("Configure default model, temperature, and system prompt.")
        d.setStyleSheet(_SECTION_DESC)
        layout.addWidget(d)

        card = QGroupBox()
        card.setStyleSheet(_CARD_STYLE)
        form = QFormLayout(card)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._default_model = QComboBox()
        self._default_model.setEditable(True)
        self._default_model.addItems(
            [
                "gpt-5.5",
                "gpt-5.4",
                "o4-mini",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-sonnet-4-6",
                "gemini-3.1-pro-preview",
                "gemini-3-flash-preview",
                "deepseek-v4-pro",
                "deepseek-v4-flash",
            ]
        )
        self._default_model.setCurrentText(self._settings.get("ai.default_model"))
        form.addRow("Default Model:", self._default_model)

        self._temperature = QSpinBox()
        self._temperature.setRange(0, 20)
        self._temperature.setValue(int(self._settings.get("ai.temperature") * 10))
        self._temperature.setSuffix(" / 10")
        form.addRow("Temperature:", self._temperature)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(256, 128000)
        self._max_tokens.setSingleStep(256)
        self._max_tokens.setValue(self._settings.get("ai.max_tokens"))
        form.addRow("Max Tokens:", self._max_tokens)

        self._system_prompt = QTextEdit()
        self._system_prompt.setPlaceholderText("Additional system prompt instructions...")
        self._system_prompt.setMaximumHeight(100)
        self._system_prompt.setStyleSheet(
            f"background: {tc.get('bg_input_deep')}; color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 5px; padding: 6px; font-size: {tc.FONT_MD}px;"
        )
        self._system_prompt.setText(self._settings.get("ai.system_prompt") or "")
        form.addRow("System Prompt:", self._system_prompt)

        layout.addWidget(card)
        layout.addStretch()
        return page

    # ── Section: Terminal ────────────────────────────────────────

    def _create_terminal_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)

        h = QLabel("Terminal")
        h.setStyleSheet(_SECTION_TITLE)
        layout.addWidget(h)
        d = QLabel("Configure the built-in terminal emulator.")
        d.setStyleSheet(_SECTION_DESC)
        layout.addWidget(d)

        card = QGroupBox()
        card.setStyleSheet(_CARD_STYLE)
        form = QFormLayout(card)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._shell_path = QLineEdit()
        self._shell_path.setText(self._settings.get("terminal.shell"))
        self._shell_path.setStyleSheet(_INPUT_STYLE)
        form.addRow("Shell:", self._shell_path)

        self._term_font_size = QSpinBox()
        self._term_font_size.setRange(8, 24)
        self._term_font_size.setValue(self._settings.get("terminal.font_size"))
        form.addRow("Font Size:", self._term_font_size)

        layout.addWidget(card)
        layout.addStretch()
        return page

    # ── Section: MCP Marketplace ─────────────────────────────────

    def _create_mcp_section(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)

        h = QLabel("MCP Servers")
        h.setStyleSheet(_SECTION_TITLE)
        layout.addWidget(h)
        d = QLabel("Extend the AI assistant with third-party tools via Model Context Protocol.")
        d.setStyleSheet(_SECTION_DESC)
        d.setWordWrap(True)
        layout.addWidget(d)

        # Server cards grid
        self._mcp_grid = QGridLayout()
        self._mcp_grid.setSpacing(10)
        self._mcp_cards: dict[str, dict] = {}  # id -> {card, btn, status}

        from polyglot_ai.core.mcp_client import MCP_CATALOG

        for i, entry in enumerate(MCP_CATALOG):
            card = self._create_server_card(entry)
            row, col = divmod(i, 2)
            self._mcp_grid.addWidget(card, row, col)

        layout.addLayout(self._mcp_grid)

        # Custom server section
        layout.addSpacing(16)
        custom_header = QLabel("Custom Server")
        custom_header.setStyleSheet(
            f"font-size: {tc.FONT_LG}px; font-weight: bold; color: #ccc; margin-top: 8px;"
        )
        layout.addWidget(custom_header)

        custom_card = QGroupBox()
        custom_card.setStyleSheet(_CARD_STYLE)
        custom_layout = QFormLayout(custom_card)
        custom_layout.setSpacing(8)

        self._custom_name = QLineEdit()
        self._custom_name.setPlaceholderText("my-server")
        self._custom_name.setStyleSheet(_INPUT_STYLE)
        custom_layout.addRow("Name:", self._custom_name)

        self._custom_command = QLineEdit()
        self._custom_command.setPlaceholderText("npx -y @org/server-name")
        self._custom_command.setStyleSheet(_INPUT_STYLE)
        custom_layout.addRow("Command:", self._custom_command)

        self._custom_env = QLineEdit()
        self._custom_env.setPlaceholderText("API_KEY=xxx, OTHER=yyy")
        self._custom_env.setStyleSheet(_INPUT_STYLE)
        custom_layout.addRow("Env vars:", self._custom_env)

        add_btn = QPushButton("Add Server")
        add_btn.setStyleSheet(_BTN_PRIMARY)
        add_btn.clicked.connect(self._add_custom_server)
        custom_layout.addRow("", add_btn)

        layout.addWidget(custom_card)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    def _create_server_card(self, entry: dict) -> QWidget:
        """Create a marketplace card for one MCP server."""
        card = QWidget()
        card.setFixedHeight(100)
        card.setStyleSheet(
            f"QWidget {{"
            f"  background-color: {tc.get('bg_card')}; border: 1px solid {tc.get('border_card')};"
            f"  border-radius: {tc.RADIUS_MD}px;"
            f"}}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Top row: icon + name + connect button
        top = QHBoxLayout()
        icon_label = QLabel(entry["icon"])
        icon_label.setStyleSheet(
            f"font-size: {tc.FONT_2XL}px; background: transparent; border: none;"
        )
        icon_label.setFixedWidth(28)
        top.addWidget(icon_label)

        name_label = QLabel(entry["name"])
        name_label.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: bold; color: {tc.get('text_heading')}; "
            f"background: transparent; border: none;"
        )
        top.addWidget(name_label)
        top.addStretch()

        btn = QPushButton("Connect")
        btn.setFixedSize(100, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; font-size: {tc.FONT_SM}px;"
            f"  font-weight: 600; border: none; border-radius: 5px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {tc.get('accent_primary_hover')}; }}"
        )
        entry_id = entry["id"]
        btn.clicked.connect(lambda checked, eid=entry_id: self._toggle_mcp_server(eid))
        top.addWidget(btn)
        layout.addLayout(top)

        # Description
        desc = QLabel(entry["description"])
        desc.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')}; background: transparent; border: none;"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Status
        status = QLabel("")
        status.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_success_muted')}; background: transparent; border: none;"
        )
        layout.addWidget(status)

        self._mcp_cards[entry["id"]] = {"card": card, "btn": btn, "status": status}
        return card

    def _refresh_mcp_cards(self) -> None:
        """Update card states based on connected servers."""
        if not self._mcp_client:
            return
        connected = set(self._mcp_client.connected_servers)
        registered = {c.name for c in self._mcp_client.get_server_configs()}

        for sid, widgets in self._mcp_cards.items():
            if sid in connected:
                widgets["btn"].setText("Disconnect")
                widgets["btn"].setStyleSheet(
                    f"QPushButton {{"
                    f"  background-color: {tc.get('accent_danger')}; color: {tc.get('text_on_accent')}; font-size: {tc.FONT_SM}px;"
                    f"  font-weight: 600; border: none; border-radius: 5px;"
                    f"}}"
                    f"QPushButton:hover {{ background-color: {tc.get('accent_danger_hover')}; }}"
                )
                tools = [
                    t for t in self._mcp_client.available_tools.values() if t.server_name == sid
                ]
                widgets["status"].setText(f"✓ Connected · {len(tools)} tools")
            elif sid in registered:
                widgets["btn"].setText("Connect")
                widgets["btn"].setStyleSheet(
                    f"QPushButton {{"
                    f"  background-color: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')}; font-size: {tc.FONT_SM}px;"
                    f"  font-weight: 600; border: none; border-radius: 5px;"
                    f"}}"
                    f"QPushButton:hover {{ background-color: {tc.get('accent_primary_hover')}; }}"
                )
                widgets["status"].setText("Registered · not connected")
                widgets["status"].setStyleSheet(
                    f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_warning')}; background: transparent; border: none;"
                )
            else:
                widgets["btn"].setText("Connect")
                widgets["status"].setText("")

    def _toggle_mcp_server(self, server_id: str) -> None:
        """Connect or disconnect an MCP server from the catalog."""
        if not self._mcp_client:
            QMessageBox.warning(self, "MCP", "MCP client not available.")
            return

        connected = set(self._mcp_client.connected_servers)

        if server_id in connected:
            # Disconnect
            from polyglot_ai.core.async_utils import safe_task

            safe_task(self._disconnect_mcp(server_id), name="mcp_disconnect")
        else:
            # Need config?
            from polyglot_ai.core.mcp_client import MCP_CATALOG

            entry = next((e for e in MCP_CATALOG if e["id"] == server_id), None)
            if not entry:
                return

            config_fields = entry.get("config_fields", [])
            if config_fields:
                dialog = MCPConfigDialog(entry["name"], entry.get("icon", ""), config_fields, self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                config_values = dialog.get_values()
                if not config_values:
                    return
            else:
                config_values = {}

            try:
                self._mcp_client.install_from_catalog(server_id, config_values)
                from polyglot_ai.core.async_utils import safe_task

                safe_task(self._connect_mcp(server_id), name="mcp_connect")
            except Exception as e:
                QMessageBox.warning(self, "MCP Error", str(e))

    async def _connect_mcp(self, server_id: str) -> None:
        widgets = self._mcp_cards.get(server_id, {})
        if widgets.get("status"):
            widgets["status"].setText("Connecting...")
            widgets["status"].setStyleSheet(
                f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_warning')}; background: transparent; border: none;"
            )

        success = await self._mcp_client.connect(server_id)
        if success:
            self._refresh_mcp_cards()
        else:
            if widgets.get("status"):
                widgets["status"].setText("Failed to connect")
                widgets["status"].setStyleSheet(
                    f"font-size: {tc.FONT_XS}px; color: {tc.get('accent_error')}; background: transparent; border: none;"
                )

    async def _disconnect_mcp(self, server_id: str) -> None:
        await self._mcp_client.disconnect(server_id)
        self._mcp_client.uninstall_server(server_id)
        self._refresh_mcp_cards()

    def _add_custom_server(self) -> None:
        """Add a custom MCP server from the form."""
        if not self._mcp_client:
            QMessageBox.warning(self, "MCP", "MCP client not available.")
            return

        name = self._custom_name.text().strip()
        cmd_full = self._custom_command.text().strip()
        env_text = self._custom_env.text().strip()

        if not name or not cmd_full:
            QMessageBox.warning(self, "MCP", "Name and command are required.")
            return

        parts = cmd_full.split()
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        env: dict[str, str] = {}
        if env_text:
            for pair in env_text.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    env[k.strip()] = v.strip()

        from polyglot_ai.core.mcp_client import MCPServerConfig

        config = MCPServerConfig(
            name=name, command=command, args=args, env=env if env else None, enabled=True
        )
        self._mcp_client.add_server(config)
        self._mcp_client._save_config()

        self._custom_name.clear()
        self._custom_command.clear()
        self._custom_env.clear()

        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._mcp_client.connect(name), name="mcp_connect_custom")
        QMessageBox.information(self, "MCP", f"Server '{name}' added and connecting.")

    # ── OAuth ────────────────────────────────────────────────────

    def _set_openai_oauth_status(self, text: str, color: str) -> None:
        self._openai_oauth_status.setText(text)
        self._openai_oauth_status.setStyleSheet(f"font-size: {tc.FONT_MD}px; color: {color};")
        self._openai_oauth_btn.setEnabled(True)

    def _login_openai_oauth(self) -> None:
        from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient

        if not OpenAIOAuthClient.is_codex_available():
            QMessageBox.warning(
                self,
                "Node.js Required",
                "ChatGPT sign-in requires Node.js (for npx).\n\n"
                "Install it with:\n  sudo apt install nodejs npm\n\nThen try again.",
            )
            return

        self._openai_oauth_btn.setEnabled(False)
        self.oauth_status_changed.emit("Logging in via terminal...", tc.get("text_secondary"))

        import threading

        def run() -> None:
            success = OpenAIOAuthClient.run_codex_login()
            if success:
                from polyglot_ai.core.bridge import EventBus

                client = OpenAIOAuthClient(EventBus())
                if client.is_authenticated:
                    self.oauth_status_changed.emit("Signed in ✓", tc.get("accent_success_muted"))
                else:
                    self.oauth_status_changed.emit(
                        "Login completed but no token found", tc.get("accent_warning")
                    )
            else:
                self.oauth_status_changed.emit("Login failed or cancelled", tc.get("accent_error"))

        threading.Thread(target=run, daemon=True).start()

    def _logout_openai_oauth(self) -> None:
        from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient
        from polyglot_ai.core.bridge import EventBus

        client = OpenAIOAuthClient(EventBus())
        client.logout()
        self._set_openai_oauth_status("Signed out", tc.get("text_secondary"))

    # ── Claude OAuth ─────────────────────────────────────────────

    def _set_claude_oauth_status(self, text: str, color: str) -> None:
        self._claude_oauth_status.setText(text)
        self._claude_oauth_status.setStyleSheet(f"font-size: {tc.FONT_MD}px; color: {color};")
        self._claude_oauth_btn.setEnabled(True)

    def _login_claude_oauth(self) -> None:
        from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient
        from polyglot_ai.core.bridge import EventBus

        if not ClaudeOAuthClient.is_claude_available():
            self._set_claude_oauth_status(
                "Claude Code CLI not found. Install from claude.ai/download", "#f44747"
            )
            return

        self._claude_oauth_btn.setEnabled(False)
        self.claude_oauth_status_changed.emit("Logging in via terminal...", "#969696")

        import threading

        def run():
            success = ClaudeOAuthClient.run_claude_login()
            if success:
                client = ClaudeOAuthClient(EventBus())
                if client.is_authenticated:
                    sub = client._subscription_type or ""
                    label = f"Signed in ✓{' (' + sub + ')' if sub else ''}"
                    self.claude_oauth_status_changed.emit(label, "#4ec9b0")
                else:
                    self.claude_oauth_status_changed.emit(
                        "Login completed but no token found", "#e5a00d"
                    )
            else:
                self.claude_oauth_status_changed.emit("Login failed or cancelled", "#f44747")

        threading.Thread(target=run, daemon=True).start()

    def _logout_claude_oauth(self) -> None:
        from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient
        from polyglot_ai.core.bridge import EventBus

        client = ClaudeOAuthClient(EventBus())
        client.logout()
        self._set_claude_oauth_status("Signed out", "#969696")

    # ── Provider testing ─────────────────────────────────────────

    def _test_provider(self, provider_name: str) -> None:
        api_key = self._api_inputs[provider_name].text().strip()
        label = self._test_labels[provider_name]

        if not api_key:
            label.setText("No key")
            label.setStyleSheet("font-size: 11px; color: #f44747;")
            return

        label.setText("Testing...")
        label.setStyleSheet("font-size: 11px; color: #969696;")

        from polyglot_ai.core.bridge import EventBus

        bus = EventBus()

        async def test():
            try:
                if provider_name == "openai":
                    from polyglot_ai.core.ai.client import OpenAIClient

                    client = OpenAIClient(api_key, bus)
                elif provider_name == "anthropic":
                    from polyglot_ai.core.ai.anthropic_client import AnthropicClient

                    client = AnthropicClient(api_key, bus)
                elif provider_name == "google":
                    from polyglot_ai.core.ai.google_client import GoogleClient

                    client = GoogleClient(api_key, bus)
                elif provider_name == "deepseek":
                    from polyglot_ai.core.ai.client import OpenAIClient as _OAI

                    client = _OAI(
                        api_key,
                        bus,
                        base_url="https://api.deepseek.com/v1",
                        provider_name="deepseek",
                        provider_display_name="DeepSeek",
                    )
                else:
                    label.setText("Unknown")
                    return

                ok, msg = await client.test_connection()
                if ok:
                    label.setText("✓ OK")
                    label.setStyleSheet("font-size: 11px; color: #4ec9b0;")
                else:
                    label.setText(f"✗ {msg[:40]}")
                    label.setStyleSheet("font-size: 11px; color: #f44747;")
            except Exception as e:
                label.setText(f"✗ {str(e)[:40]}")
                label.setStyleSheet("font-size: 11px; color: #f44747;")

        from polyglot_ai.core.async_utils import safe_task

        safe_task(test(), name="provider_test")

    # ── Save ─────────────────────────────────────────────────────

    def _save_and_close(self) -> None:
        self._save_btn.setEnabled(False)
        self._save_btn.setText("Saving...")
        self._save_task = asyncio.ensure_future(self._save())
        self._save_task.add_done_callback(self._on_save_complete)

    def _on_save_complete(self, task: asyncio.Task) -> None:
        self._save_btn.setEnabled(True)
        self._save_btn.setText("Save Changes")
        try:
            task.result()
        except Exception as exc:
            logger.exception("Failed to save settings")
            QMessageBox.critical(self, "Save Failed", str(exc))
            return
        self.accept()

    async def _save(self) -> None:
        for provider in PROVIDERS:
            key = self._api_inputs[provider["name"]].text().strip()
            if key:
                self._keyring.store_key(provider["name"], key)
            else:
                self._keyring.delete_key(provider["name"])

        await self._settings.set("editor.font_family", self._font_family.currentText())
        await self._settings.set("editor.font_size", self._font_size.value())
        await self._settings.set("editor.tab_size", self._tab_size.value())
        await self._settings.set("theme", self._theme_combo.currentText())
        await self._settings.set("ai.default_model", self._default_model.currentText())
        await self._settings.set("ai.temperature", self._temperature.value() / 10.0)
        await self._settings.set("ai.max_tokens", self._max_tokens.value())
        await self._settings.set("ai.system_prompt", self._system_prompt.toPlainText())
        await self._settings.set("terminal.shell", self._shell_path.text())
        await self._settings.set("terminal.font_size", self._term_font_size.value())

        logger.info("Settings saved")
