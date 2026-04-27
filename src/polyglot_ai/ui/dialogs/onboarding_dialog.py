"""First-run onboarding wizard — guides new users through setup."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class OnboardingDialog(QDialog):
    """Welcome wizard shown on first launch."""

    # Signals to safely update UI from background thread
    _login_result = pyqtSignal(bool, str)
    _claude_login_result = pyqtSignal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Polyglot AI")
        self.setFixedSize(520, 480)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        self._api_key = ""
        self._current_page = 0

        # Connect login result signals once (thread-safe UI updates)
        self._login_result.connect(self._on_login_result)
        self._claude_login_result.connect(self._on_claude_login_result)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_welcome_page())
        self._stack.addWidget(self._create_auth_page())
        self._stack.addWidget(self._create_features_page())
        self._stack.addWidget(self._create_ready_page())
        layout.addWidget(self._stack)

        # Bottom nav bar
        nav = QWidget()
        nav.setStyleSheet("background-color: #252526; border-top: 1px solid #333;")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(24, 12, 24, 12)

        # Page dots
        self._dots: list[QLabel] = []
        dots_row = QHBoxLayout()
        dots_row.setSpacing(6)
        for i in range(4):
            dot = QLabel("●" if i == 0 else "○")
            dot.setStyleSheet(
                f"color: {'#0078d4' if i == 0 else '#555'}; font-size: 10px; background: transparent;"
            )
            dots_row.addWidget(dot)
            self._dots.append(dot)
        nav_layout.addLayout(dots_row)
        nav_layout.addStretch()

        self._back_btn = QPushButton("Back")
        self._back_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; font-size: 13px; "
            "border: 1px solid #555; border-radius: 8px; padding: 6px 20px; }"
            "QPushButton:hover { background: #333; color: #ddd; }"
        )
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.hide()
        nav_layout.addWidget(self._back_btn)

        self._next_btn = QPushButton("Get Started →")
        self._next_btn.setStyleSheet(
            "QPushButton { background: #0078d4; color: white; font-size: 13px; "
            "font-weight: 600; border: none; border-radius: 8px; padding: 6px 24px; }"
            "QPushButton:hover { background: #1a8ae8; }"
        )
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._go_next)
        nav_layout.addWidget(self._next_btn)

        layout.addWidget(nav)

    def _create_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # App icon
        from pathlib import Path
        from PyQt6.QtGui import QPixmap

        icon_row = QHBoxLayout()
        icon_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = Path(__file__).parent.parent / "resources" / "icons" / "polyglot-ai.png"
        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_lbl.setPixmap(pixmap)
        else:
            icon_lbl.setText("🤖")
            icon_lbl.setStyleSheet("font-size: 48px;")
        icon_lbl.setFixedSize(72, 72)
        icon_row.addWidget(icon_lbl)
        layout.addLayout(icon_row)
        layout.addSpacing(20)

        title = QLabel("Welcome to Polyglot AI")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #e8e8e8;")
        layout.addWidget(title)
        layout.addSpacing(8)

        subtitle = QLabel(
            "Your AI-powered coding assistant for Linux.\n"
            "Chat, edit, review, and build — all in one place."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 14px; color: #999; line-height: 160%;")
        layout.addWidget(subtitle)
        layout.addSpacing(30)

        # Feature highlights
        features = [
            ("💬", "Chat with AI", "Multi-provider support — OpenAI, Claude, Gemini, DeepSeek"),
            ("✏️", "Edit code safely", "AI proposes changes, you approve before they're applied"),
            ("🔍", "Review & plan", "Structured code review and step-by-step planning"),
            ("🔌", "Extensible", "Connect MCP servers for GitHub, databases, and more"),
        ]
        for emoji, heading, desc in features:
            row = QHBoxLayout()
            row.setSpacing(12)
            icon_lbl = QLabel(emoji)
            icon_lbl.setFixedSize(32, 32)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("font-size: 18px; background: transparent;")
            row.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignTop)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            h = QLabel(heading)
            h.setStyleSheet(
                "font-size: 13px; font-weight: 600; color: #e0e0e0; background: transparent;"
            )
            text_col.addWidget(h)
            d = QLabel(desc)
            d.setStyleSheet("font-size: 12px; color: #888; background: transparent;")
            text_col.addWidget(d)
            row.addLayout(text_col, stretch=1)
            layout.addLayout(row)
            layout.addSpacing(4)

        layout.addStretch()
        return page

    def _create_auth_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 20)

        title = QLabel("Connect your AI")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e8e8e8;")
        layout.addWidget(title)
        layout.addSpacing(6)

        desc = QLabel(
            "Sign in with your ChatGPT or Claude subscription, or add an API key.\n"
            "You can always change this later in Settings."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px; color: #999;")
        layout.addWidget(desc)
        layout.addSpacing(20)

        # ChatGPT subscription button
        chatgpt_btn = QPushButton("Sign in with ChatGPT")
        chatgpt_btn.setFixedHeight(44)
        chatgpt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        chatgpt_btn.setStyleSheet(
            "QPushButton { background: #10a37f; color: white; font-size: 14px; "
            "font-weight: 600; border: none; border-radius: 10px; }"
            "QPushButton:hover { background: #1bbd96; }"
        )
        chatgpt_btn.clicked.connect(self._login_chatgpt)
        layout.addWidget(chatgpt_btn)

        self._chatgpt_status = QLabel("")
        self._chatgpt_status.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._chatgpt_status)
        layout.addSpacing(8)

        # Claude subscription button
        claude_btn = QPushButton("Sign in with Claude")
        claude_btn.setFixedHeight(44)
        claude_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        claude_btn.setStyleSheet(
            "QPushButton { background: #d97706; color: white; font-size: 14px; "
            "font-weight: 600; border: none; border-radius: 10px; }"
            "QPushButton:hover { background: #e69500; }"
        )
        claude_btn.clicked.connect(self._login_claude)
        layout.addWidget(claude_btn)

        self._claude_status = QLabel("")
        self._claude_status.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._claude_status)
        layout.addSpacing(16)

        # Divider
        or_row = QHBoxLayout()
        line1 = QWidget()
        line1.setFixedHeight(1)
        line1.setStyleSheet("background: #333;")
        or_row.addWidget(line1, stretch=1)
        or_label = QLabel("  or use an API key  ")
        or_label.setStyleSheet("color: #666; font-size: 12px;")
        or_row.addWidget(or_label)
        line2 = QWidget()
        line2.setFixedHeight(1)
        line2.setStyleSheet("background: #333;")
        or_row.addWidget(line2, stretch=1)
        layout.addLayout(or_row)
        layout.addSpacing(12)

        # API key input
        key_label = QLabel("OpenAI API Key:")
        key_label.setStyleSheet("font-size: 12px; color: #bbb;")
        layout.addWidget(key_label)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("sk-...")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setStyleSheet(
            "QLineEdit { background: #161616; color: #d4d4d4; border: 1px solid #3a3a3a; "
            "border-radius: 8px; padding: 10px 14px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0078d4; }"
        )
        layout.addWidget(self._key_input)

        skip_label = QLabel("You can skip this and configure later in Settings.")
        skip_label.setStyleSheet("font-size: 11px; color: #666; margin-top: 8px;")
        layout.addWidget(skip_label)

        layout.addStretch()
        return page

    def _create_features_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 20)

        title = QLabel("How it works")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e8e8e8;")
        layout.addWidget(title)
        layout.addSpacing(16)

        steps = [
            (
                "1",
                "Open a project",
                "Use File → Open Project to load your codebase. The AI will understand your project structure.",
            ),
            (
                "2",
                "Chat with AI",
                "Ask questions, request changes, or use /review to analyze your code.",
            ),
            (
                "3",
                "Review & approve",
                "The AI proposes changes — you review diffs and approve before anything is written.",
            ),
            (
                "4",
                "Plan before coding",
                "Toggle Plan mode to get a structured step-by-step plan before implementation.",
            ),
        ]
        for num, heading, desc in steps:
            card = QWidget()
            card.setStyleSheet(
                "QWidget { background: #252526; border: 1px solid #333; border-radius: 8px; }"
            )
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(12)

            num_label = QLabel(num)
            num_label.setFixedSize(32, 32)
            num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_label.setStyleSheet(
                "background: #0078d4; color: white; font-size: 14px; font-weight: bold; "
                "border-radius: 16px; border: none;"
            )
            card_layout.addWidget(num_label)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            h = QLabel(heading)
            h.setStyleSheet(
                "font-size: 13px; font-weight: 600; color: #e0e0e0; background: transparent; border: none;"
            )
            text_col.addWidget(h)
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet("font-size: 12px; color: #999; background: transparent; border: none;")
            text_col.addWidget(d)
            card_layout.addLayout(text_col, stretch=1)

            layout.addWidget(card)
            layout.addSpacing(4)

        layout.addStretch()
        return page

    def _create_ready_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 60, 40, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Big checkmark
        check = QLabel("✓")
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        check.setStyleSheet("font-size: 48px; color: #4ec9b0; background: transparent;")
        layout.addWidget(check)
        layout.addSpacing(16)

        title = QLabel("You're all set!")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #e8e8e8;")
        layout.addWidget(title)
        layout.addSpacing(8)

        desc = QLabel(
            "Open a project and start chatting with your AI assistant.\n\n"
            "Tip: Use the + button for quick actions,\n"
            "or try /review to analyze your code."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 14px; color: #999; line-height: 160%;")
        layout.addWidget(desc)

        layout.addStretch()
        return page

    def _go_next(self) -> None:
        if self._current_page < 3:
            self._current_page += 1
            self._stack.setCurrentIndex(self._current_page)
            self._update_nav()
        else:
            # Save API key if entered
            self._api_key = self._key_input.text().strip()
            self.accept()

    def _go_back(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._stack.setCurrentIndex(self._current_page)
            self._update_nav()

    def _update_nav(self) -> None:
        for i, dot in enumerate(self._dots):
            dot.setText("●" if i == self._current_page else "○")
            dot.setStyleSheet(
                f"color: {'#0078d4' if i == self._current_page else '#555'}; "
                f"font-size: 10px; background: transparent;"
            )
        self._back_btn.setVisible(self._current_page > 0)

        if self._current_page == 3:
            self._next_btn.setText("Start Coding →")
        elif self._current_page == 1:
            self._next_btn.setText("Next →")
        else:
            self._next_btn.setText("Next →")

    def _login_chatgpt(self) -> None:
        from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient

        if not OpenAIOAuthClient.is_codex_available():
            self._chatgpt_status.setText("Node.js required. Install: sudo apt install nodejs npm")
            self._chatgpt_status.setStyleSheet("font-size: 11px; color: #f44747;")
            return

        self._chatgpt_status.setText("Opening login in terminal...")
        self._chatgpt_status.setStyleSheet("font-size: 11px; color: #969696;")

        import threading

        def run():
            success = OpenAIOAuthClient.run_codex_login()
            if success:
                from polyglot_ai.core.bridge import EventBus

                client = OpenAIOAuthClient(EventBus())
                if client.is_authenticated:
                    self._login_result.emit(True, "✓ Signed in successfully!")
                    return
            self._login_result.emit(False, "Login failed or cancelled")

        threading.Thread(target=run, daemon=True).start()

    def _on_login_result(self, success: bool, message: str) -> None:
        """Handle ChatGPT login result on the main thread (via signal)."""
        self._chatgpt_status.setText(message)
        color = "#4ec9b0" if success else "#f44747"
        self._chatgpt_status.setStyleSheet(f"font-size: 11px; color: {color};")

    def _login_claude(self) -> None:
        from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient

        if not ClaudeOAuthClient.is_claude_available():
            self._claude_status.setText(
                "Claude Code CLI not found. Install from claude.ai/download"
            )
            self._claude_status.setStyleSheet("font-size: 11px; color: #f44747;")
            return

        self._claude_status.setText("Opening login in terminal...")
        self._claude_status.setStyleSheet("font-size: 11px; color: #969696;")

        import threading

        def run():
            success = ClaudeOAuthClient.run_claude_login()
            if success:
                from polyglot_ai.core.bridge import EventBus

                client = ClaudeOAuthClient(EventBus())
                if client.is_authenticated:
                    self._claude_login_result.emit(True, "✓ Signed in to Claude!")
                    return
            self._claude_login_result.emit(False, "Login failed or cancelled")

        threading.Thread(target=run, daemon=True).start()

    def _on_claude_login_result(self, success: bool, message: str) -> None:
        """Handle Claude login result on the main thread (via signal)."""
        self._claude_status.setText(message)
        color = "#4ec9b0" if success else "#f44747"
        self._claude_status.setStyleSheet(f"font-size: 11px; color: {color};")

    @property
    def api_key(self) -> str:
        return self._api_key
