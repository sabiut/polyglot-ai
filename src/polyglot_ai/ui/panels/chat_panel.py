"""Chat panel — AI conversation interface with streaming, attachments, and management."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.ai.models import Conversation, Message, ToolCall
from polyglot_ai.ui.panels.chat_message import ChatMessage
from polyglot_ai.ui import theme_colors as tc

if TYPE_CHECKING:
    from polyglot_ai.core.ai.context import ContextBuilder
    from polyglot_ai.core.ai.provider_manager import ProviderManager
    from polyglot_ai.core.database import Database

logger = logging.getLogger(__name__)

# Attachment storage dir
_ATTACH_DIR = Path.home() / ".local" / "share" / "polyglot-ai" / "attachments"

# Model capability info
_MODEL_CAPS = {
    "gpt-5.4": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Most capable for complex tasks",
    },
    "gpt-5.4-mini": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Balanced speed and capability",
    },
    "gpt-5.4-nano": {
        "vision": False,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Fastest for quick answers",
    },
    "o3": {
        "vision": False,
        "tools": True,
        "reasoning": True,
        "fast": False,
        "desc": "Advanced reasoning model",
    },
    "o3-mini": {
        "vision": False,
        "tools": True,
        "reasoning": True,
        "fast": True,
        "desc": "Fast reasoning model",
    },
    "o4-mini": {
        "vision": False,
        "tools": True,
        "reasoning": True,
        "fast": True,
        "desc": "Efficient reasoning model",
    },
    "claude-opus-4-6": {
        "vision": True,
        "tools": True,
        "reasoning": True,
        "fast": False,
        "desc": "Most capable for ambitious work",
    },
    "claude-sonnet-4-6": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Most efficient for everyday tasks",
    },
    "claude-haiku-4-5": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Fastest for quick answers",
    },
    "claude-sonnet-4-5": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Strong balanced model",
    },
    "claude-sonnet-4-0": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Reliable everyday model",
    },
    "gemini-3.1-pro-preview": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Most capable Gemini model",
    },
    "gemini-3-flash-preview": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Fast and efficient",
    },
    "gemini-3.1-flash-lite-preview": {
        "vision": True,
        "tools": False,
        "reasoning": False,
        "fast": True,
        "desc": "Lightweight and fast",
    },
}


class ChatPanel(QWidget):
    """Chat interface with full conversation management, attachments, and streaming."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._provider_manager: ProviderManager | None = None
        self._db: Database | None = None
        self._context_builder: ContextBuilder | None = None
        self._current_conversation: Conversation | None = None
        self._streaming = False
        self._stream_task: asyncio.Task | None = None
        self._workflow_running = False
        self._current_assistant_msg: ChatMessage | None = None
        self._tools: list[dict] | None = None
        self._tool_registry = None
        self._mcp_client = None
        self._persisted_message_count = 0
        self._pending_attachments: list[dict] = []  # {path, filename, mime_type, size}
        self._current_plan = None
        self._onboarding_shown = False
        self._drop_overlay: QLabel | None = None
        self._github_btn: QPushButton | None = None  # Initialized if GitHub connected
        # Task-aware state — populated when set_event_bus() runs after
        # init_task_manager() has bound the global manager.
        self._task_manager = None
        self._active_task = None
        self._event_bus = None

        self._setup_ui()

        # Accept drops on the entire chat panel
        self.setAcceptDrops(True)

    # ─── UI Setup ───────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_label = QLabel("AI ASSISTANT")
        header_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: bold; color: {tc.get('text_secondary')}; letter-spacing: 1px;"
        )
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        # Bootstrap-mode toggle. When enabled, shell_exec is auto-
        # approved for 15 minutes so `npm install` / `pip install` /
        # `go mod tidy` / etc. during project scaffolding don't need a
        # per-command approval dialog. The button label reflects the
        # active state; a QTimer refreshes it once a second so the
        # countdown stays honest and the button auto-reverts when the
        # deadline passes.
        _btn_base = (
            f"QPushButton {{ font-size: {tc.FONT_SM}px; padding: 2px 10px; "
            f"background: {tc.get('bg_input')}; color: #fff; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; "
            f"border-color: {tc.get('accent_primary')}; }}"
        )

        self._bootstrap_btn = QPushButton("  Bootstrap")
        self._bootstrap_btn.setFixedHeight(26)
        self._bootstrap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bootstrap_btn.setToolTip(
            "Bootstrap mode: auto-approve shell_exec for 15 minutes so "
            "scaffolding commands (npm/pip/go/cargo install) don't prompt. "
            "Click again to end early."
        )
        self._bootstrap_btn.setIcon(self._make_unlock_icon())
        self._bootstrap_btn.setStyleSheet(_btn_base)
        self._bootstrap_btn.clicked.connect(self._toggle_bootstrap_mode)
        header_layout.addWidget(self._bootstrap_btn)

        from PyQt6.QtCore import QTimer

        self._bootstrap_timer = QTimer(self)
        self._bootstrap_timer.setInterval(1000)
        self._bootstrap_timer.timeout.connect(self._refresh_bootstrap_label)

        header_layout.addSpacing(6)

        self._new_chat_btn = QPushButton("  New")
        self._new_chat_btn.setFixedHeight(26)
        self._new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_chat_btn.setIcon(self._make_plus_icon())
        self._new_chat_btn.setStyleSheet(_btn_base)
        self._new_chat_btn.clicked.connect(self._new_conversation)
        header_layout.addWidget(self._new_chat_btn)
        layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Conversation sidebar ──
        sidebar = QWidget()
        sidebar.setMaximumWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Search bar
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search conversations...")
        self._search_input.setFixedHeight(28)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                font-size: {tc.FONT_MD}px; padding: 4px 8px;
                background: {tc.get("bg_card")}; border: 1px solid {tc.get("border_primary")};
                border-radius: {tc.RADIUS_MD}px; color: {tc.get("text_primary")}; margin: 4px;
            }}
            QLineEdit:focus {{ border-color: {tc.get("accent_primary")}; }}
        """)
        self._search_input.textChanged.connect(self._on_search)
        sidebar_layout.addWidget(self._search_input)

        # Category filter buttons
        cat_widget = QWidget()
        cat_widget.setStyleSheet(f"background: {tc.get('bg_surface')};")
        cat_layout = QHBoxLayout(cat_widget)
        cat_layout.setContentsMargins(4, 2, 4, 2)
        cat_layout.setSpacing(2)
        self._active_category = "all"
        _cat_pill = f"""
            QPushButton {{
                background: transparent; color: {tc.get("text_tertiary")};
                font-size: {tc.FONT_XS}px; border: none;
                border-radius: {tc.RADIUS_SM}px; padding: 2px 8px;
            }}
            QPushButton:hover {{ color: {tc.get("text_heading")}; background: {tc.get("bg_hover_subtle")}; }}
            QPushButton:checked {{
                background: {tc.get("accent_primary")}; color: {tc.get("text_on_accent")};
            }}
        """
        self._cat_buttons = {}
        for cat_name in ("All", "Work", "Personal", "Research"):
            btn = QPushButton(cat_name)
            btn.setCheckable(True)
            btn.setFixedHeight(20)
            btn.setStyleSheet(_cat_pill)
            btn.clicked.connect(lambda checked, c=cat_name.lower(): self._filter_category(c))
            cat_layout.addWidget(btn)
            self._cat_buttons[cat_name.lower()] = btn
        self._cat_buttons["all"].setChecked(True)
        sidebar_layout.addWidget(cat_widget)

        self._conv_list = QListWidget()
        self._conv_list.setStyleSheet(f"""
            QListWidget {{
                font-size: {tc.FONT_MD}px; background: {tc.get("bg_surface")}; border: none;
                outline: none;
            }}
            QListWidget::item {{
                padding: 6px 8px; border-radius: {tc.RADIUS_SM}px; margin: 1px 4px;
            }}
            QListWidget::item:selected {{ background: {tc.get("bg_active")}; }}
            QListWidget::item:hover:!selected {{ background: {tc.get("bg_hover_subtle")}; }}
        """)
        self._conv_list.currentRowChanged.connect(self._on_conversation_selected)
        self._conv_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._conv_list.customContextMenuRequested.connect(self._show_conv_context_menu)
        sidebar_layout.addWidget(self._conv_list)

        splitter.addWidget(sidebar)

        # ── Message area ──
        msg_container = QWidget()
        msg_layout = QVBoxLayout(msg_container)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._message_widget = QWidget()
        self._message_layout = QVBoxLayout(self._message_widget)
        self._message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._message_layout.setSpacing(0)
        self._message_layout.setContentsMargins(12, 12, 12, 48)

        self._welcome = QLabel()
        self._welcome.setTextFormat(Qt.TextFormat.RichText)
        q = f"<span style='color:{tc.get('accent_info')};'>&#x275D;</span>"
        sep = f"border-bottom:1px solid {tc.get('border_secondary')};"
        row = f"color:{tc.get('text_primary')}; font-size:13px; padding:6px 0;"
        self._welcome.setText(
            f"<div style='text-align:center; padding:24px;'>"
            f"<div style='font-size:18px; font-weight:600; color:{tc.get('text_heading')}; "
            f"margin-bottom:16px;'>Polyglot AI</div>"
            # Two columns
            f"<table width='100%' cellpadding='0' cellspacing='12'><tr>"
            # Left: coding
            f"<td valign='top' style='background:{tc.get('bg_surface')}; "
            f"border-radius:8px; padding:16px; width:48%;'>"
            f"<div style='font-size:12px; font-weight:600; color:{tc.get('accent_success')}; "
            f"margin-bottom:10px;'>CODE WITH AI</div>"
            f"<div style='{row} {sep}'>{q} Review this file for bugs</div>"
            f"<div style='{row} {sep}'>{q} Write tests for main.py</div>"
            f"<div style='{row}'>{q} Refactor for readability</div>"
            f"<div style='font-size:10px; color:{tc.get('text_muted')}; margin-top:8px;'>"
            f"Open a project first (Ctrl+Shift+O)</div>"
            f"</td>"
            # Right: general chat
            f"<td valign='top' style='background:{tc.get('bg_surface')}; "
            f"border-radius:8px; padding:16px; width:48%;'>"
            f"<div style='font-size:12px; font-weight:600; color:{tc.get('accent_info')}; "
            f"margin-bottom:10px;'>JUST CHAT</div>"
            f"<div style='{row} {sep}'>{q} Explain a concept to me</div>"
            f"<div style='{row} {sep}'>{q} Help me draft an email</div>"
            f"<div style='{row}'>{q} Search for the latest docs</div>"
            f"<div style='font-size:10px; color:{tc.get('text_muted')}; margin-top:8px;'>"
            f"No project needed — just start typing</div>"
            f"</td>"
            f"</tr></table>"
            f"<div style='font-size:11px; color:{tc.get('text_muted')}; margin-top:12px;'>"
            f"Add an API key in Settings (Ctrl+,) to get started</div>"
            f"</div>"
        )
        self._welcome.setWordWrap(True)
        self._welcome.setStyleSheet(
            f"color: {tc.get('text_secondary')}; padding: 20px; "
            f"background-color: {tc.get('bg_surface_raised')}; border-radius: {tc.RADIUS_MD}px;"
        )
        self._message_layout.addWidget(self._welcome)

        self._scroll.setWidget(self._message_widget)
        msg_layout.addWidget(self._scroll, stretch=1)

        # ── Attachment preview bar (hidden by default) ──
        self._attach_bar = QWidget()
        self._attach_bar.setFixedHeight(40)
        self._attach_bar.setStyleSheet(
            f"background: {tc.get('bg_surface_overlay')}; border-top: 1px solid {tc.get('border_primary')};"
        )
        self._attach_bar.hide()
        self._attach_bar_layout = QHBoxLayout(self._attach_bar)
        self._attach_bar_layout.setContentsMargins(12, 4, 12, 4)
        self._attach_bar_layout.setSpacing(6)
        msg_layout.addWidget(self._attach_bar)

        # ── Input area ──
        self._create_arrow_icon()

        input_wrapper = QWidget()
        input_wrapper.setStyleSheet("background: transparent;")
        wrapper_layout = QVBoxLayout(input_wrapper)
        wrapper_layout.setContentsMargins(10, 4, 10, 8)
        wrapper_layout.setSpacing(0)

        input_card = QWidget()
        input_card.setObjectName("inputCard")
        input_card.setStyleSheet(f"""
            QWidget#inputCard {{
                background-color: {tc.get("bg_chat_input")};
                border: 1px solid {tc.get("border_input")};
                border-radius: {tc.RADIUS_LG}px;
            }}
        """)
        card_layout = QVBoxLayout(input_card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(0)

        self._input = ChatInput()
        self._input.setPlaceholderText("Reply... (Shift+Enter for new line)")
        self._input.setMinimumHeight(36)
        self._input.setMaximumHeight(100)
        self._input.setStyleSheet(f"""
            QTextEdit {{
                font-size: {tc.FONT_LG}px; background-color: transparent;
                color: {tc.get("text_heading")}; border: none;
                padding: 8px 14px 6px 14px;
                selection-background-color: {tc.get("bg_active")};
            }}
        """)
        self._input.submit_requested.connect(self._on_send)
        self._input.file_dropped.connect(self._add_attachment_from_path)
        self._input.image_pasted.connect(self._add_pasted_image)
        card_layout.addWidget(self._input)

        # Toolbar — clean layout: [attach] [model] — [status] — [send/stop]
        toolbar = QWidget()
        toolbar.setObjectName("cardToolbar")
        toolbar.setFixedHeight(34)
        toolbar.setStyleSheet("background: transparent; border: none;")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 0, 8, 4)
        toolbar_layout.setSpacing(4)

        _icon_btn_style = f"""
            QPushButton {{
                background-color: transparent; border: none;
                border-radius: {tc.RADIUS_SM}px; padding: 4px;
            }}
            QPushButton:hover {{ background-color: {tc.get("bg_hover")}; }}
        """

        # Attach button — painted icon
        self._plus_btn = QPushButton()
        self._plus_btn.setObjectName("toolbarIconBtn")
        self._plus_btn.setFixedSize(28, 28)
        self._plus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plus_btn.setToolTip("Attach files, templates, and more")
        self._plus_btn.setIcon(self._make_toolbar_icon("plus"))
        self._plus_btn.setStyleSheet(f"""
            #toolbarIconBtn {{
                background-color: transparent; border: none;
                border-radius: {tc.RADIUS_SM}px; padding: 4px;
            }}
            #toolbarIconBtn:hover {{ background-color: {tc.get("bg_hover")}; }}
        """)
        self._plus_btn.clicked.connect(self._show_plus_menu)
        toolbar_layout.addWidget(self._plus_btn)

        # Model combo — compact
        from polyglot_ai.ui.widgets.styled_combo import StyledComboBox

        self._model_combo = StyledComboBox()
        self._model_combo.setFixedHeight(28)
        self._model_combo.setMaximumWidth(200)
        self._model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._populate_default_models()
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        toolbar_layout.addWidget(self._model_combo)

        # Plan mode toggle (small pill)
        self._plan_btn = QPushButton("Plan")
        self._plan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plan_btn.setToolTip("Ask AI to plan before coding")
        self._plan_btn.setFixedHeight(24)
        self._plan_btn.setCheckable(True)
        self._plan_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {tc.get("text_tertiary")};
                font-size: {tc.FONT_SM}px; border: 1px solid {tc.get("border_card")};
                border-radius: {tc.RADIUS_LG}px; padding: 2px 10px;
            }}
            QPushButton:hover {{ color: {tc.get("text_heading")}; border-color: {tc.get("border_input")}; }}
            QPushButton:checked {{
                background: {tc.get("accent_primary")}; color: {tc.get("text_on_accent")};
                border-color: {tc.get("accent_primary")};
            }}
        """)
        self._plan_btn.clicked.connect(self._toggle_plan_mode)
        self._plan_mode = False
        toolbar_layout.addWidget(self._plan_btn)

        # Web search toggle — icon button
        self._search_btn = QPushButton()
        self._search_btn.setObjectName("toolbarSearchBtn")
        self._search_btn.setFixedSize(28, 28)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setToolTip("Enable web search for current information")
        self._search_btn.setCheckable(True)
        self._search_btn.setIcon(self._make_toolbar_icon("search"))
        self._search_btn.setStyleSheet(f"""
            #toolbarSearchBtn {{
                background-color: transparent; border: none;
                border-radius: {tc.RADIUS_SM}px; padding: 4px;
            }}
            #toolbarSearchBtn:hover {{ background-color: {tc.get("bg_hover")}; }}
            #toolbarSearchBtn:checked {{
                background-color: {tc.get("accent_info")};
                border-radius: {tc.RADIUS_SM}px;
            }}
        """)
        self._search_btn.clicked.connect(self._toggle_search_mode)
        self._search_mode = False
        toolbar_layout.addWidget(self._search_btn)

        # Templates button — painted icon
        self._template_btn = QPushButton()
        self._template_btn.setObjectName("toolbarTplBtn")
        self._template_btn.setFixedSize(28, 28)
        self._template_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._template_btn.setToolTip("Prompt templates")
        self._template_btn.setIcon(self._make_toolbar_icon("template"))
        self._template_btn.setStyleSheet(f"""
            #toolbarTplBtn {{
                background-color: transparent; border: none;
                border-radius: {tc.RADIUS_SM}px; padding: 4px;
            }}
            #toolbarTplBtn:hover {{ background-color: {tc.get("bg_hover")}; }}
        """)
        self._template_btn.clicked.connect(self._show_template_menu)
        toolbar_layout.addWidget(self._template_btn)

        toolbar_layout.addStretch()

        # Status area (streaming indicator + token count)
        self._status_widget = QWidget()
        self._status_widget.setStyleSheet("background: transparent;")
        status_layout = QHBoxLayout(self._status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)

        self._streaming_dot = QLabel("●")
        self._streaming_dot.setStyleSheet(
            f"color: {tc.get('accent_success')}; font-size: 8px; background: transparent;"
        )
        self._streaming_dot.hide()
        status_layout.addWidget(self._streaming_dot)

        self._token_label = QLabel("")
        self._token_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; background: transparent;"
        )
        status_layout.addWidget(self._token_label)

        self._cap_label = QLabel("")
        self._cap_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; background: transparent;"
        )
        status_layout.addWidget(self._cap_label)
        toolbar_layout.addWidget(self._status_widget)

        # Send / Stop button (transforms during streaming)
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setFixedHeight(28)
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setToolTip("Stop generation")
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: {tc.FONT_SM}px; font-weight: bold;
                padding: 2px 12px; background-color: {tc.get("accent_danger")};
                color: {tc.get("text_on_accent")}; border: none; border-radius: {tc.RADIUS_SM}px;
            }}
            QPushButton:hover {{ background-color: {tc.get("accent_danger_hover")}; }}
        """)
        self._stop_btn.clicked.connect(self._stop_generation)
        self._stop_btn.hide()
        toolbar_layout.addWidget(self._stop_btn)

        self._send_btn = QPushButton()
        self._send_btn.setFixedSize(32, 32)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("Send message (Enter)")
        self._send_btn.setIcon(self._create_send_icon())
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #ececec; border: none; border-radius: 16px;
                padding: 4px;
            }}
            QPushButton:hover {{ background-color: #ffffff; }}
            QPushButton:pressed {{ background-color: {tc.get("text_primary")}; }}
            QPushButton:disabled {{ background-color: {tc.get("bg_hover")}; }}
        """)
        self._send_btn.clicked.connect(self._on_send)
        toolbar_layout.addWidget(self._send_btn)

        card_layout.addWidget(toolbar)

        # Agent status indicator (shown during streaming)
        self._agent_status_label = QLabel("")
        self._agent_status_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')}; padding: 2px 8px; background: transparent;"
        )
        self._agent_status_label.setVisible(False)
        card_layout.addWidget(self._agent_status_label)

        wrapper_layout.addWidget(input_card)
        msg_layout.addWidget(input_wrapper)
        splitter.addWidget(msg_container)
        splitter.setSizes([0, 400])

        layout.addWidget(splitter)

    def _populate_default_models(self) -> None:
        self._default_grouped = {
            "OpenAI": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3", "o3-mini", "o4-mini"],
            "Anthropic": [
                "claude-opus-4-6",
                "claude-sonnet-4-6",
                "claude-haiku-4-5",
                "claude-sonnet-4-5",
                "claude-sonnet-4-0",
            ],
            "Google": [
                "gemini-3.1-pro-preview",
                "gemini-3-flash-preview",
                "gemini-3.1-flash-lite-preview",
            ],
            "xAI (Grok)": [
                "grok-4.20-0309-reasoning",
                "grok-4.20-0309-non-reasoning",
                "grok-4-1-fast-reasoning",
                "grok-4-1-fast-non-reasoning",
            ],
        }
        provider_data_map = {
            "OpenAI": "openai",
            "Anthropic": "anthropic",
            "Google": "google",
            "xAI (Grok)": "xai",
        }
        for provider_display, models in self._default_grouped.items():
            self._model_combo.addHeader(f"── {provider_display} ──")
            for m in models:
                caps = _MODEL_CAPS.get(m, {})
                desc = caps.get("desc", "")
                full_id = f"{provider_data_map[provider_display]}:{m}"
                self._model_combo.addItemWithDesc(m, desc, full_id)
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == "openai:gpt-5.4":
                self._model_combo.setCurrentIndex(i)
                break

    def _on_model_changed(self, index: int) -> None:
        """Update capability label when model changes."""
        full_id = self._model_combo.itemData(index)
        if not full_id:
            self._cap_label.setText("")
            return
        model_id = full_id.split(":", 1)[1] if ":" in str(full_id) else str(full_id)
        caps = _MODEL_CAPS.get(model_id, {})
        parts = []
        if caps.get("vision"):
            parts.append("Vision")
        if caps.get("tools"):
            parts.append("Tools")
        if caps.get("reasoning"):
            parts.append("Reasoning")
        if caps.get("fast"):
            parts.append("Fast")
        self._cap_label.setText(" · ".join(parts))

    # ─── Plus menu ──────────────────────────────────────────────────

    # ─── Drag-and-drop ─────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            self._show_drop_overlay()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._hide_drop_overlay()

    def dropEvent(self, event) -> None:
        self._hide_drop_overlay()
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path:
                    self._add_attachment_from_path(path)
            event.acceptProposedAction()
        elif mime.hasText():
            self._input.setPlainText(mime.text())
            event.acceptProposedAction()

    def _show_drop_overlay(self) -> None:
        if not self._drop_overlay:
            self._drop_overlay = QLabel("Drop files here", self)
            self._drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._drop_overlay.setStyleSheet(
                f"background: rgba(0, 120, 212, 0.15); border: 2px dashed {tc.get('accent_primary')}; "
                f"border-radius: {tc.RADIUS_MD}px; color: {tc.get('accent_primary')}; font-size: 16px; font-weight: bold;"
            )
        self._drop_overlay.setGeometry(self.rect())
        self._drop_overlay.show()
        self._drop_overlay.raise_()

    def _hide_drop_overlay(self) -> None:
        if self._drop_overlay:
            self._drop_overlay.hide()

    def _show_plus_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {tc.get("bg_surface_overlay")}; border: 1px solid {tc.get("border_input")};
                border-radius: 10px; padding: 6px 4px;
            }}
            QMenu::item {{
                padding: 8px 16px 8px 12px; color: {tc.get("text_heading")};
                border-radius: {tc.RADIUS_MD}px; margin: 2px 4px;
            }}
            QMenu::item:selected {{ background-color: {tc.get("bg_hover")}; }}
            QMenu::separator {{ height: 1px; background-color: {tc.get("bg_hover")}; margin: 4px 12px; }}
        """)

        attach_icon = self._create_menu_icon("paperclip")
        folder_icon = self._create_menu_icon("folder")
        terminal_icon = self._create_menu_icon("terminal")
        settings_icon = self._create_menu_icon("gear")

        add_files = QAction(attach_icon, "  Add files or photos", menu)
        add_files.triggered.connect(self._attach_file)
        menu.addAction(add_files)

        open_folder = QAction(folder_icon, "  Open project folder", menu)
        open_folder.triggered.connect(self._open_project_from_menu)
        menu.addAction(open_folder)

        menu.addSeparator()

        open_terminal = QAction(terminal_icon, "  Run in terminal", menu)
        open_terminal.triggered.connect(self._open_terminal_from_menu)
        menu.addAction(open_terminal)

        mcp_icon = self._create_menu_icon("plug")
        open_mcp = QAction(mcp_icon, "  MCP Servers", menu)
        open_mcp.triggered.connect(self._open_mcp_from_menu)
        menu.addAction(open_mcp)

        menu.addSeparator()

        open_settings = QAction(settings_icon, "  Settings", menu)
        open_settings.triggered.connect(self._open_settings_from_menu)
        menu.addAction(open_settings)

        btn_pos = self._plus_btn.mapToGlobal(self._plus_btn.rect().topLeft())
        menu_height = menu.sizeHint().height()
        menu.exec(btn_pos - QPoint(0, menu_height + 4))

    # ─── Attachments ────────────────────────────────────────────────

    def _attach_file(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add files or photos",
            "",
            "All Files (*);;Images (*.png *.jpg *.jpeg *.gif *.webp);;Code Files (*.py *.js *.ts *.html *.css)",
        )
        for f in files:
            self._add_attachment_from_path(f)

    def _add_attachment_from_path(self, file_path: str) -> None:
        """Add a file attachment with preview."""
        p = Path(file_path)
        if not p.exists():
            return

        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "application/octet-stream"

        # Copy to attachments dir
        _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        import uuid

        dest = _ATTACH_DIR / f"{uuid.uuid4().hex}_{p.name}"
        shutil.copy2(p, dest)

        attach = {
            "path": str(dest),
            "original": str(p),
            "filename": p.name,
            "mime_type": mime,
            "size": p.stat().st_size,
        }
        self._pending_attachments.append(attach)
        self._update_attach_bar()

    def _add_pasted_image(self, pixmap: QPixmap) -> None:
        """Handle image paste from clipboard."""
        _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        import uuid

        filename = f"pasted_{uuid.uuid4().hex[:8]}.png"
        dest = _ATTACH_DIR / filename
        pixmap.save(str(dest), "PNG")

        attach = {
            "path": str(dest),
            "original": "clipboard",
            "filename": filename,
            "mime_type": "image/png",
            "size": dest.stat().st_size,
        }
        self._pending_attachments.append(attach)
        self._update_attach_bar()

    def _update_attach_bar(self) -> None:
        """Show/update the attachment preview bar."""
        # Clear existing
        while self._attach_bar_layout.count():
            item = self._attach_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._pending_attachments:
            self._attach_bar.hide()
            return

        self._attach_bar.show()
        for i, attach in enumerate(self._pending_attachments):
            chip = QWidget()
            chip.setStyleSheet(
                f"background: {tc.get('bg_hover')}; border-radius: {tc.RADIUS_MD}px; padding: 2px;"
            )
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 2, 4, 2)
            chip_layout.setSpacing(4)

            # Icon or thumbnail
            if attach["mime_type"].startswith("image/"):
                thumb = QLabel()
                pm = QPixmap(attach["path"]).scaled(
                    24,
                    24,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                thumb.setPixmap(pm)
                chip_layout.addWidget(thumb)

            name = QLabel(attach["filename"][:20])
            name.setStyleSheet(
                f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px; background: transparent;"
            )
            chip_layout.addWidget(name)

            size_kb = attach["size"] / 1024
            size_label = QLabel(f"({size_kb:.0f}KB)")
            size_label.setStyleSheet(
                f"color: {tc.get('text_muted')}; font-size: 10px; background: transparent;"
            )
            chip_layout.addWidget(size_label)

            remove_btn = QPushButton("✕")
            remove_btn.setFixedSize(18, 18)
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {tc.get('text_tertiary')}; border: none; font-size: {tc.FONT_MD}px; }}"
                f"QPushButton:hover {{ color: #ff4444; }}"
            )
            idx = i
            remove_btn.clicked.connect(lambda checked, x=idx: self._remove_attachment(x))
            chip_layout.addWidget(remove_btn)

            self._attach_bar_layout.addWidget(chip)

        self._attach_bar_layout.addStretch()

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self._pending_attachments):
            self._pending_attachments.pop(index)
            self._update_attach_bar()

    # ─── Conversation management ────────────────────────────────────

    def _show_conv_context_menu(self, position) -> None:
        item = self._conv_list.itemAt(position)
        if not item:
            return

        conv_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {tc.get("bg_surface_overlay")}; border: 1px solid {tc.get("border_menu")};
                padding: 4px 0; color: {tc.get("text_primary")}; font-size: {tc.FONT_MD}px;
            }}
            QMenu::item {{ padding: 4px 20px; }}
            QMenu::item:selected {{ background-color: {tc.get("bg_active")}; }}
            QMenu::separator {{ height: 1px; background: {tc.get("border_menu")}; margin: 4px 8px; }}
        """)

        rename_act = menu.addAction("Rename...")
        rename_act.triggered.connect(lambda: self._rename_conversation(item, conv_id))

        pin_act = menu.addAction("Pin / Unpin")
        pin_act.triggered.connect(lambda: self._pin_conversation(conv_id))

        menu.addSeparator()

        export_act = menu.addAction("Export as text...")
        export_act.triggered.connect(lambda: self._export_conversation(conv_id))

        menu.addSeparator()

        delete_act = menu.addAction("Delete")
        delete_act.triggered.connect(lambda: self._delete_conversation(item, conv_id))

        menu.exec(self._conv_list.viewport().mapToGlobal(position))

    def _rename_conversation(self, item: QListWidgetItem, conv_id: int) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename Conversation", "New name:", text=item.text()
        )
        if ok and new_name:
            item.setText(new_name)
            if self._db:
                from polyglot_ai.core.async_utils import safe_task

                safe_task(self._db.rename_conversation(conv_id, new_name), name="db_rename")

    def _delete_conversation(self, item: QListWidgetItem, conv_id: int) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Conversation",
            f"Delete '{item.text()}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            row = self._conv_list.row(item)
            self._conv_list.takeItem(row)
            if self._current_conversation and self._current_conversation.id == conv_id:
                self._new_conversation()
            if self._db:
                from polyglot_ai.core.async_utils import safe_task

                safe_task(self._db.delete_conversation(conv_id), name="db_delete")

    def _pin_conversation(self, conv_id: int) -> None:
        if self._db:
            from polyglot_ai.core.async_utils import safe_task

            safe_task(self._db.pin_conversation(conv_id), name="db_pin")

    def _export_conversation(self, conv_id: int) -> None:
        async def do_export():
            if not self._db:
                return
            messages = await self._db.get_messages(conv_id)
            lines = []
            for msg in messages:
                role = msg.get("role", "?").upper()
                content = msg.get("content", "")
                lines.append(f"[{role}]\n{content}\n")
            text = "\n".join(lines)
            from polyglot_ai.core.async_utils import run_blocking

            await run_blocking(Path(path).write_text, text, "utf-8")

        # Show file dialog synchronously (before async), then write in thread
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Conversation", "conversation.txt", "Text Files (*.txt)"
        )
        if not path:
            return

        from polyglot_ai.core.async_utils import safe_task

        safe_task(do_export(), name="export_conversation")

    def _on_search(self, query: str) -> None:
        """Filter conversation list by search query."""
        query = query.lower().strip()
        for i in range(self._conv_list.count()):
            item = self._conv_list.item(i)
            if item:
                item.setHidden(bool(query) and query not in item.text().lower())

    # ─── Stop generation ────────────────────────────────────────────

    def _toggle_plan_mode(self) -> None:
        """Toggle plan mode — AI plans before coding."""
        self._plan_mode = not self._plan_mode
        if self._plan_mode:
            self._plan_btn.setText("▾ Plan ✓")
            self._plan_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {tc.get("bg_feedback_pos")}; color: {tc.get("accent_success_muted")}; font-size: {tc.FONT_MD}px;
                    border: 1px solid #2a5a3a; border-radius: {tc.RADIUS_LG}px;
                    padding: 4px 12px;
                    font-family: -apple-system, 'Segoe UI', sans-serif;
                }}
                QPushButton:hover {{ background: #1f4a35; }}
            """)
            self._input.setPlaceholderText("Ask a question with /plan...")
        else:
            self._plan_btn.setText("▾ Plan")
            self._plan_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {tc.get("text_tertiary")}; font-size: {tc.FONT_MD}px;
                    border: 1px solid {tc.get("border_card")}; border-radius: {tc.RADIUS_LG}px;
                    padding: 4px 12px;
                    font-family: -apple-system, 'Segoe UI', sans-serif;
                }}
                QPushButton:hover {{ background: {tc.get("bg_user_bubble_long")}; color: {tc.get("text_heading")}; border-color: #666; }}
            """)
            self._input.setPlaceholderText("Reply... (Shift+Enter for new line)")

    def _toggle_search_mode(self) -> None:
        """Toggle web search mode."""
        self._search_mode = not self._search_mode

    async def _handle_plan_creation(self, tool_call, assistant_text: str) -> None:
        """Handle create_plan tool call — parse, display, and offer execution."""
        from polyglot_ai.core.ai.plan_parser import parse_plan_from_tool_call

        try:
            plan = parse_plan_from_tool_call(
                tool_call.arguments,
                original_request=self._current_conversation.messages[-2].content
                if len(self._current_conversation.messages) >= 2
                else "",
            )
        except Exception as e:
            logger.error("Failed to parse plan: %s", e)
            self._add_system_message(f"Failed to parse plan: {e}")
            return

        self._current_plan = plan
        logger.info("Plan created: %s (%d steps)", plan.title, len(plan.steps))

        # Show plan summary in chat
        step_list = "\n".join(
            f"  {i + 1}. **{s.title}**"
            + (f" — {', '.join(s.files_affected)}" if s.files_affected else "")
            for i, s in enumerate(plan.steps)
        )
        plan_summary = (
            f"## 📋 {plan.title}\n\n"
            f"{plan.summary}\n\n"
            f"### Steps\n{step_list}\n\n"
            f"*Switch to the **Plan** tab to review, approve, and execute.*"
        )
        if self._current_assistant_msg:
            self._current_assistant_msg.set_final_content(plan_summary)

        # Send plan to the Plan panel
        window = self.window()
        if hasattr(window, "_plan_panel"):
            window._plan_panel.set_plan(plan)
            window._plan_panel._on_execute = self._execute_plan
            # Switch to Plan tab
            if hasattr(window, "_right_tabs"):
                for i in range(window._right_tabs.count()):
                    if window._right_tabs.tabText(i).strip().startswith("📋"):
                        window._right_tabs.setCurrentIndex(i)
                        break

        # Add tool result to conversation so context stays consistent
        self._current_conversation.messages.append(
            Message(
                role="tool",
                content=f"Plan created: {plan.title} with {len(plan.steps)} steps",
                tool_call_id=tool_call.id,
            )
        )

        self._set_streaming_ui(False)
        self._set_agent_status("")

    def _execute_plan(self, plan) -> None:
        """Start executing a plan — called from Plan panel."""
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_plan_execution(plan), name="plan_execution")

    async def _run_plan_execution(self, plan) -> None:
        """Execute a plan step by step."""
        from polyglot_ai.core.ai.plan_executor import PlanExecutor
        from polyglot_ai.core.ai.plan_models import PlanStatus, PlanStepStatus

        # Get provider
        full_id, display_model = self._get_selected_model()
        if not full_id or not self._provider_manager:
            self._add_system_message("No provider available for plan execution.")
            return

        result = self._provider_manager.get_provider_for_model(full_id)
        if not result:
            self._add_system_message(f"No provider found for model: {display_model}")
            return

        provider, model_id = result

        # Build system prompt (inform the builder which tools are registered
        # right now so tool-dependent directives like sequential-thinking are
        # only emitted when the corresponding tool actually exists).
        system_prompt = None
        if self._context_builder:
            if self._tools:
                self._context_builder.set_available_tools(
                    [t.get("name", "") for t in self._tools if isinstance(t, dict)]
                )
            from polyglot_ai.core.async_utils import run_blocking

            system_prompt = await run_blocking(self._context_builder.build_system_prompt)

        # Create executor
        from polyglot_ai.core.bridge import EventBus

        executor = PlanExecutor(
            provider=provider,
            model_id=model_id,
            tool_registry=self._tool_registry,
            event_bus=EventBus(),
            system_prompt=system_prompt,
        )

        # Set conversation context
        if self._current_conversation:
            messages = self._current_conversation.get_api_messages()
            executor.set_messages(messages)

        self._set_streaming_ui(True)
        self._set_agent_status("Executing plan...")

        # Stream callback to show step progress in chat
        def on_stream(step_index, delta_text):
            step = plan.steps[step_index] if step_index < len(plan.steps) else None
            if step:
                self._set_agent_status(f"Step {step_index + 1}: {step.title}")
            # Update plan panel
            window = self.window()
            if hasattr(window, "_plan_panel"):
                window._plan_panel.update_plan()

        # Tool approval callback — use the same inline card the
        # main streaming flow uses, so the user experience is
        # consistent and the chat transcript records the decision.
        async def on_tool_approval(tool_name, args):
            return await self._request_tool_approval(tool_name, args)

        try:
            plan.approve_all()
            await executor.execute(plan, on_stream=on_stream, on_tool_approval=on_tool_approval)

            # Show completion in chat
            if plan.status == PlanStatus.COMPLETED:
                completed = sum(1 for s in plan.steps if s.status == PlanStepStatus.COMPLETED)
                self._add_system_message(
                    f"✅ Plan completed! {completed}/{len(plan.steps)} steps executed successfully."
                )
            elif plan.status == PlanStatus.PAUSED:
                self._add_system_message("⏸ Plan paused. Check the Plan tab for details.")
            elif plan.status == PlanStatus.FAILED:
                self._add_system_message("❌ Plan failed. Check the Plan tab for error details.")

        except Exception as e:
            logger.error("Plan execution error: %s", e)
            self._add_system_message(f"Plan execution error: {e}")
        finally:
            self._set_streaming_ui(False)
            self._set_agent_status("")
            # Update plan panel
            window = self.window()
            if hasattr(window, "_plan_panel"):
                window._plan_panel.update_plan()

    def _connect_github(self) -> None:
        """Open GitHub connection consent dialog."""
        from polyglot_ai.ui.dialogs.github_connect_dialog import GitHubConnectDialog

        dialog = GitHubConnectDialog(self)
        if dialog.exec():
            token = dialog.get_token()
            if token:
                window = self.window()
                if hasattr(window, "_mcp_client"):
                    try:
                        window._mcp_client.install_from_catalog(
                            "github", {"GITHUB_PERSONAL_ACCESS_TOKEN": token}
                        )
                        from polyglot_ai.core.async_utils import safe_task

                        safe_task(window._mcp_client.connect("github"), name="mcp_connect_github")
                        self._github_btn.setText("⌥ GitHub ✓")
                        self._github_btn.setStyleSheet(f"""
                            QPushButton {{
                                background: {tc.get("bg_feedback_pos")}; color: {tc.get("accent_success_muted")}; font-size: {tc.FONT_MD}px;
                                border: 1px solid #2a5a3a; border-radius: {tc.RADIUS_LG}px;
                                padding: 4px 12px;
                                font-family: -apple-system, 'Segoe UI', sans-serif;
                            }}
                            QPushButton:hover {{ background: #1f4a35; }}
                        """)
                        self._add_system_message(
                            "GitHub connected! The AI can now access your repositories."
                        )
                    except Exception as e:
                        self._add_system_message(f"Failed to connect GitHub: {e}")
                else:
                    self._add_system_message("MCP client not available. Open a project first.")

    def _stop_generation(self) -> None:
        """Cancel the current streaming task."""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            logger.info("Generation stopped by user")

    def _set_streaming_ui(self, streaming: bool) -> None:
        """Toggle between send and stop buttons, show streaming indicator."""
        self._streaming = streaming
        self._send_btn.setVisible(not streaming)
        self._stop_btn.setVisible(streaming)
        self._input.setReadOnly(streaming)
        # Streaming dot indicator
        if hasattr(self, "_streaming_dot"):
            self._streaming_dot.setVisible(streaming)
        if streaming:
            self._set_agent_status("Thinking...")
        else:
            self._set_agent_status("")

    def _set_agent_status(self, status: str) -> None:
        """Show a brief status text below the input (e.g. 'Reading file...', 'Running command...')."""
        if not hasattr(self, "_agent_status_label"):
            return
        if status:
            self._agent_status_label.setText(f"⏳ {status}")
            self._agent_status_label.setVisible(True)
        else:
            self._agent_status_label.setVisible(False)

    # ─── Slash Commands ────────────────────────────────────────────

    def _handle_slash_command(self, text: str) -> bool:
        """Handle /commands. Returns True if handled."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/clear":
            self._clear_messages()
            self._current_conversation = None
            self._add_system_message("Conversation cleared.")
            return True

        if cmd == "/new":
            self._new_conversation()
            return True

        if cmd == "/model":
            if arg:
                # Try to select the requested model
                for i in range(self._model_combo.count()):
                    if arg.lower() in self._model_combo.itemText(i).lower():
                        self._model_combo.setCurrentIndex(i)
                        self._add_system_message(
                            f"Switched to model: {self._model_combo.itemText(i).strip()}"
                        )
                        return True
                self._add_system_message(
                    f"Model '{arg}' not found. Available models are in the dropdown."
                )
            else:
                _, display = self._get_selected_model()
                self._add_system_message(f"Current model: {display}")
            return True

        if cmd == "/review":
            self._run_code_review(arg)
            return True

        if cmd == "/status":
            project_root = self._get_project_root()
            provider_count = (
                len(self._provider_manager.get_all_providers()) if self._provider_manager else 0
            )
            msg_count = (
                len(self._current_conversation.messages) if self._current_conversation else 0
            )
            mcp_servers = self._mcp_client.connected_servers if self._mcp_client else []
            mcp_tools = len(self._mcp_client.available_tools) if self._mcp_client else 0
            self._add_system_message(
                f"**Project:** {project_root or 'None'}\n"
                f"**Providers:** {provider_count} active\n"
                f"**Messages:** {msg_count} in current conversation\n"
                f"**MCP Servers:** {len(mcp_servers)} connected ({mcp_tools} tools)"
            )
            return True

        if cmd == "/fix":
            # Ask AI to fix the last error or a specified issue
            issue = arg or "the last error or failing test"
            self._inject_ai_prompt(
                f"Please analyze and fix: {issue}. Read the relevant files, identify the problem, and propose a fix."
            )
            return True

        if cmd == "/test":
            # Ask AI to run tests and handle failures
            test_cmd = arg or ""
            if test_cmd:
                self._inject_ai_prompt(
                    f"Run this test command: `{test_cmd}`. If it fails, analyze the output and fix the issues."
                )
            else:
                self._inject_ai_prompt(
                    "Detect the test framework for this project (pytest, jest, go test, etc.), "
                    "run the tests, and if any fail, analyze the output and propose fixes."
                )
            return True

        if cmd == "/explain":
            target = arg or "the current project"
            self._inject_ai_prompt(
                f"Explain {target} clearly and concisely. Include purpose, key components, and how they fit together."
            )
            return True

        if cmd == "/commit":
            msg = arg or ""
            if msg:
                self._inject_ai_prompt(f'Stage all changes and commit with message: "{msg}"')
            else:
                self._inject_ai_prompt(
                    "Look at the current git diff, generate a clear conventional commit message, "
                    "then stage and commit the changes. Show me the message before committing."
                )
            return True

        if cmd == "/git":
            if arg:
                self._inject_ai_prompt(f"Run `git {arg}` and show me the output.")
            else:
                self._inject_ai_prompt(
                    "Show me the current git status including branch, staged/unstaged changes, and recent commits."
                )
            return True

        if cmd == "/workflow":
            self._handle_workflow_command(arg)
            return True

        if cmd == "/help":
            self._add_system_message(
                "**Available commands:**\n"
                "• `/clear` — Clear conversation\n"
                "• `/new` — Start new conversation\n"
                "• `/model [name]` — Show or switch model\n"
                "• `/review [branch]` — Review code changes\n"
                "• `/fix [issue]` — Fix an error or failing test\n"
                "• `/test [command]` — Run tests and fix failures\n"
                "• `/explain [target]` — Explain code or project\n"
                "• `/commit [message]` — Stage and commit changes\n"
                "• `/git [command]` — Run a git command\n"
                "• `/workflow [name] [--key value]` — Run a multi-step workflow\n"
                "• `/status` — Show session info\n"
                "• `/help` — Show this help"
            )
            return True

        # Unknown slash command — don't consume, let it go as a message
        return False

    def _inject_ai_prompt(self, prompt: str) -> None:
        """Send a prompt to the AI as if the user typed it."""
        self._input.setPlainText(prompt)
        self._on_send()

    def _run_code_review(self, branch: str = "") -> None:
        """Run a code review — switches to Review tab and triggers the review engine."""
        project_root = self._get_project_root()
        if not project_root:
            self._add_system_message("Open a project first to use /review.")
            return

        # Switch to the Review tab and trigger its Run Review
        window = self.window()
        if hasattr(window, "_right_tabs") and hasattr(window, "_review_panel"):
            # Set the review mode based on branch arg
            if branch:
                window._review_panel._mode_combo.setCurrentIndex(2)  # Branch vs Main
            # Switch to review tab
            for i in range(window._right_tabs.count()):
                if window._right_tabs.tabText(i).strip().startswith("🔍"):
                    window._right_tabs.setCurrentIndex(i)
                    break
            # Trigger the review
            window._review_panel._on_run_review()
            self._add_system_message(
                "Review started — switch to the **🔍 Review** tab to see results."
            )
            return

        # Fallback: run in chat if Review panel isn't available.
        # Offload to async task so subprocess.run doesn't block the
        # Qt event loop (git diff can take seconds on large repos).
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_code_review_fallback(project_root, branch), name="code_review_fallback")

    async def _run_code_review_fallback(self, project_root: str, branch: str) -> None:
        """Run git diff in a thread and feed it to the AI as a review prompt."""
        import subprocess

        from polyglot_ai.core.async_utils import run_blocking

        def _get_diff() -> str:
            if branch:
                r = subprocess.run(
                    ["git", "diff", branch],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                    timeout=15,
                )
            else:
                r = subprocess.run(
                    ["git", "diff", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                    timeout=15,
                )
                if not r.stdout.strip():
                    r = subprocess.run(
                        ["git", "diff"],
                        capture_output=True,
                        text=True,
                        cwd=project_root,
                        timeout=15,
                    )
            return r.stdout.strip()

        try:
            diff = await run_blocking(_get_diff)
            if not diff:
                self._add_system_message("No changes found to review.")
                return

            if len(diff) > 15000:
                diff = diff[:15000] + "\n\n... (truncated — diff too large)"

            review_prompt = (
                f"Please review the following code changes and provide feedback on:\n"
                f"- Potential bugs or issues\n"
                f"- Code quality improvements\n"
                f"- Security concerns\n"
                f"- Suggestions\n\n"
                f"```diff\n{diff}\n```"
            )
            self._input.setPlainText(review_prompt)
            self._on_send()

        except FileNotFoundError:
            self._add_system_message("Git is not installed or this is not a git repository.")
        except subprocess.TimeoutExpired:
            self._add_system_message("Git diff timed out.")

    # ─── Send / Stream ──────────────────────────────────────────────

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text and not self._pending_attachments:
            return
        if self._streaming:
            return
        if self._workflow_running:
            self._add_system_message(
                "A workflow is running. Wait for it to finish, or click Stop to cancel."
            )
            return

        # Handle slash commands
        if text.startswith("/"):
            self._input.clear()
            if self._handle_slash_command(text):
                return

        if not self._provider_manager or not self._provider_manager.has_providers:
            self._add_system_message("Please sign in with ChatGPT or add an API key in Settings.")
            return

        full_id, display_model = self._get_selected_model()
        if not full_id:
            self._add_system_message("Please select a model from the dropdown.")
            return

        result = self._provider_manager.get_provider_for_model(full_id)
        if not result:
            self._add_system_message(f"No provider found for model: {display_model}")
            return

        self._input.clear()
        self._welcome.hide()

        if self._current_conversation is None:
            self._current_conversation = Conversation(model=full_id or display_model)
            self._persisted_message_count = 0

        # Resolve @file mentions — read referenced files into context
        import re as _re

        mentioned_files: list[str] = []
        project_root = self._get_project_root()
        if project_root:
            for match in _re.finditer(r"@([\w./\-]+)", text):
                fpath = Path(project_root) / match.group(1)
                if fpath.is_file():
                    mentioned_files.append(match.group(1))

        # Build message content with attachments
        from polyglot_ai.core.ai.models import Attachment

        content = text
        if mentioned_files:
            for mf in mentioned_files:
                try:
                    fc = (Path(project_root) / mf).read_text(encoding="utf-8", errors="replace")
                    content += f"\n\n--- {mf} ---\n```\n{fc}\n```"
                except Exception:
                    pass
        attachment_info = []
        message_attachments: list[Attachment] = []

        if self._pending_attachments:
            for att in self._pending_attachments:
                attachment_info.append(att)
                if att["mime_type"].startswith("image/"):
                    # Store as image attachment for vision API
                    message_attachments.append(
                        Attachment(
                            path=att["path"],
                            filename=att["filename"],
                            mime_type=att["mime_type"],
                            size=att.get("size", 0),
                        )
                    )
                elif att["mime_type"].startswith("text/") or att["filename"].endswith(
                    (
                        ".py",
                        ".js",
                        ".ts",
                        ".html",
                        ".css",
                        ".json",
                        ".yaml",
                        ".yml",
                        ".toml",
                        ".md",
                        ".rst",
                        ".txt",
                        ".sh",
                        ".bash",
                        ".sql",
                        ".xml",
                        ".cfg",
                        ".ini",
                        ".env",
                        ".rs",
                        ".go",
                        ".java",
                        ".c",
                        ".cpp",
                        ".h",
                        ".rb",
                        ".php",
                        ".swift",
                        ".kt",
                    )
                ):
                    try:
                        file_content = Path(att["path"]).read_text(
                            encoding="utf-8", errors="replace"
                        )
                        # Scan for secrets before sending to AI provider
                        from polyglot_ai.core.security import (
                            scan_content_for_secrets,
                            is_secret_file,
                        )

                        if is_secret_file(Path(att["filename"])):
                            content += (
                                f"\n\n⚠️ Skipped attachment **{att['filename']}** "
                                "— file name matches a known secret pattern "
                                "(e.g. .env, credentials). Remove secrets before attaching."
                            )
                            continue
                        secret_hits = scan_content_for_secrets(file_content)
                        if secret_hits:
                            content += (
                                f"\n\n⚠️ Skipped attachment **{att['filename']}** "
                                f"— detected {len(secret_hits)} secret pattern(s). "
                                "Remove secrets before attaching."
                            )
                            continue

                        content += f"\n\n--- {att['filename']} ---\n```\n{file_content}\n```"
                    except Exception:
                        content += f"\n\n[Attached: {att['filename']}]"
                else:
                    content += f"\n\n[Attached: {att['filename']} ({att['mime_type']})]"
            self._pending_attachments.clear()
            self._update_attach_bar()

        user_msg = Message(
            role="user",
            content=content,
            attachments=message_attachments if message_attachments else None,
        )
        self._current_conversation.messages.append(user_msg)

        # Show user message (with attachment chips)
        display_text = text
        if attachment_info:
            chips = " ".join(f"📎 {a['filename']}" for a in attachment_info)
            display_text = f"{chips}\n\n{text}" if text else chips
        self._add_message_widget("user", display_text)

        self._stream_task = asyncio.ensure_future(self._stream_response())

    async def _stream_response(self) -> None:
        if not self._provider_manager or not self._current_conversation:
            return

        full_id, display_model = self._get_selected_model()
        if not full_id:
            return

        result = self._provider_manager.get_provider_for_model(full_id)
        if not result:
            self._add_system_message("Provider not available.")
            return

        provider, model_id = result

        self._set_streaming_ui(True)
        self._current_conversation.model = full_id

        system_prompt = None
        if self._context_builder and self._context_builder._project_root:
            # Offload to thread pool — build_system_prompt walks the
            # filesystem and reads every source file, which can block
            # the Qt event loop for 1-5s on large projects and trigger
            # the OS "not responding" dialog.
            from polyglot_ai.core.async_utils import run_blocking

            system_prompt = await run_blocking(self._context_builder.build_system_prompt)
        if not system_prompt:
            system_prompt = (
                "You are Polyglot AI, a helpful general-purpose assistant. "
                "You can answer questions on any topic, have conversations, "
                "and help with coding tasks. When you need current information, "
                "use the web_search tool. For general knowledge questions, "
                "answer directly without using tools."
            )

        # Plan mode: instruct AI to use create_plan tool
        if self._plan_mode:
            plan_instruction = (
                "\n\n[PLAN MODE ACTIVE] You MUST call the `create_plan` tool before "
                "writing any code. Analyze the user's request and create a structured "
                "implementation plan with clear, actionable steps. Each step should:\n"
                "- Have a short, clear title\n"
                "- Describe what will be done\n"
                "- List the files that will be created or modified\n"
                "Do NOT write code or use file_write/shell_exec yet. ONLY call create_plan."
            )
            system_prompt = (system_prompt or "") + plan_instruction

        # Web search mode: instruct AI to use web_search tool
        if getattr(self, "_search_mode", False):
            search_instruction = (
                "\n\n[WEB SEARCH ENABLED] You have access to the web_search tool. "
                "When the user asks about recent events, current information, documentation, "
                "or anything that benefits from up-to-date data, use the web_search tool to "
                "find relevant information. Include source links in your response."
            )
            system_prompt = (system_prompt or "") + search_instruction

        # Check if model supports vision for image attachments
        caps = _MODEL_CAPS.get(model_id, {})
        has_vision = caps.get("vision", False)
        messages = self._current_conversation.get_api_messages(include_images=has_vision)

        self._add_separator()
        model_label = f"{display_model} ({provider.display_name})"

        # Show thinking indicator while waiting for first token
        thinking_widget = self._create_thinking_indicator()
        self._message_layout.addWidget(thinking_widget)
        self._scroll_to_bottom()
        first_token_received = False

        self._current_assistant_msg = ChatMessage("assistant", "", model=model_label)
        self._current_assistant_msg.on_apply_code = self._apply_code_blocks
        self._current_assistant_msg.on_run_command = self._run_command
        self._current_assistant_msg.on_regenerate = lambda: self._regenerate_last()

        full_content = ""
        tool_calls_data: dict[int, dict] = {}
        usage_info = None

        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model_id,
                tools=self._tools,
                system_prompt=system_prompt,
            ):
                # Replace thinking indicator with actual message on first content
                if not first_token_received and (chunk.delta_content or chunk.tool_calls):
                    first_token_received = True
                    thinking_widget.deleteLater()
                    self._message_layout.removeWidget(thinking_widget)
                    self._message_layout.addWidget(self._current_assistant_msg)

                if chunk.delta_content:
                    full_content += chunk.delta_content
                    self._current_assistant_msg.append_content(chunk.delta_content)
                    self._scroll_to_bottom()

                if chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        idx = tc["index"]
                        if idx not in tool_calls_data:
                            tool_calls_data[idx] = {
                                "id": tc.get("id", ""),
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("id"):
                            tool_calls_data[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            tool_calls_data[idx]["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            tool_calls_data[idx]["function"]["arguments"] += func["arguments"]

                if chunk.usage:
                    usage_info = chunk.usage

            tool_calls_list = None
            if tool_calls_data:
                logger.info(
                    "Tool calls accumulated: %s",
                    {
                        k: {
                            "id": v["id"],
                            "name": v["function"]["name"],
                            "args_len": len(v["function"]["arguments"]),
                        }
                        for k, v in tool_calls_data.items()
                    },
                )
                tool_calls_list = [
                    ToolCall(
                        id=tc["id"],
                        function_name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in tool_calls_data.values()
                    if tc["function"]["name"]  # skip tool calls with empty names
                ]
                if not tool_calls_list:
                    logger.warning("All tool calls filtered out (empty names)")
                    tool_calls_list = None

            assistant_msg = Message(
                role="assistant",
                content=full_content if full_content else None,
                tool_calls=tool_calls_list,
                model=model_id,
                tokens_in=usage_info.get("prompt_tokens") if usage_info else None,
                tokens_out=usage_info.get("completion_tokens") if usage_info else None,
            )
            self._current_conversation.messages.append(assistant_msg)

            if tool_calls_list:
                logger.info(
                    "Executing %d tool call(s): %s",
                    len(tool_calls_list),
                    [(tc.function_name, tc.id) for tc in tool_calls_list],
                )
                # Check for create_plan tool call — intercept it
                plan_tc = next(
                    (tc for tc in tool_calls_list if tc.function_name == "create_plan"),
                    None,
                )
                if plan_tc:
                    await self._handle_plan_creation(plan_tc, full_content)
                else:
                    await self._execute_tool_calls(
                        tool_calls_list, provider, model_id, display_model, system_prompt
                    )

            if full_content and not (
                tool_calls_list and any(tc.function_name == "create_plan" for tc in tool_calls_list)
            ):
                await self._auto_apply(full_content)

            if usage_info:
                total = usage_info.get("total_tokens", "?")
                self._token_label.setText(f"Tokens: {total}")

            await self._persist_conversation()

        except asyncio.CancelledError:
            # User stopped generation
            if full_content and self._current_assistant_msg:
                self._current_assistant_msg.append_content("\n\n*[Generation stopped]*")
                self._current_conversation.messages.append(
                    Message(
                        role="assistant",
                        content=full_content + "\n\n[Generation stopped]",
                        model=model_id,
                    )
                )
                await self._persist_conversation()
            self._add_system_message("Generation stopped.")

        except Exception as e:
            logger.exception("Error during streaming")
            # Clean up thinking indicator if still showing
            if not first_token_received:
                thinking_widget.deleteLater()
                self._message_layout.removeWidget(thinking_widget)
            error_msg = str(e)[:200]
            self._add_error_message(
                f"Error: {error_msg}", provider, model_id, display_model, system_prompt
            )

        finally:
            # Clean up thinking indicator if stream ended with no content
            if not first_token_received:
                try:
                    thinking_widget.deleteLater()
                    self._message_layout.removeWidget(thinking_widget)
                except RuntimeError:
                    pass
            self._set_streaming_ui(False)
            self._current_assistant_msg = None
            self._stream_task = None

    # ─── Regenerate ─────────────────────────────────────────────────

    def _regenerate_last(self) -> None:
        """Remove last assistant message and re-stream."""
        if not self._current_conversation or self._streaming:
            return

        # Remove last assistant message from conversation
        while (
            self._current_conversation.messages
            and self._current_conversation.messages[-1].role == "assistant"
        ):
            self._current_conversation.messages.pop()

        # Remove last assistant widget from UI
        for i in range(self._message_layout.count() - 1, -1, -1):
            widget = self._message_layout.itemAt(i).widget()
            if isinstance(widget, ChatMessage) and widget._role == "assistant":
                widget.deleteLater()
                self._message_layout.takeAt(i)
                break
            elif widget and widget is not self._welcome:
                # Remove separator too
                if widget.maximumHeight() <= 2:
                    widget.deleteLater()
                    self._message_layout.takeAt(i)
                    break

        # Re-stream
        self._stream_task = asyncio.ensure_future(self._stream_response())

    # ─── Edit & Resend ──────────────────────────────────────────────

    def _edit_and_resend(self, widget: ChatMessage, content: str) -> None:
        """Edit a previous user message and resend from that point."""
        if not self._current_conversation or self._streaming:
            return

        # Find the message index by matching content
        message_index = None
        for i, msg in enumerate(self._current_conversation.messages):
            if msg.role == "user" and msg.content == content:
                message_index = i
                break

        if message_index is None:
            # Fallback: find last user message with similar content
            for i in range(len(self._current_conversation.messages) - 1, -1, -1):
                if self._current_conversation.messages[i].role == "user":
                    message_index = i
                    break

        if message_index is None:
            return

        msg = self._current_conversation.messages[message_index]

        new_text, ok = QInputDialog.getMultiLineText(
            self, "Edit Message", "Edit your message:", msg.content or ""
        )
        if not ok or not new_text.strip():
            return

        # Truncate conversation to that point
        self._current_conversation.messages = self._current_conversation.messages[:message_index]

        # Clear UI from that point
        self._clear_messages()
        self._welcome.hide()
        for m in self._current_conversation.messages:
            if m.role in ("user", "assistant", "tool"):
                self._add_message_widget(m.role, m.content or "")

        # Add edited message and resend
        user_msg = Message(role="user", content=new_text.strip())
        self._current_conversation.messages.append(user_msg)
        self._add_message_widget("user", new_text.strip())

        self._stream_task = asyncio.ensure_future(self._stream_response())

    # ─── Error recovery ─────────────────────────────────────────────

    def _add_error_message(
        self, error_text: str, provider=None, model_id=None, display_model=None, system_prompt=None
    ) -> None:
        """Show error with retry button."""
        self._add_separator()
        error_widget = QWidget()
        error_widget.setStyleSheet("background: transparent;")
        error_layout = QVBoxLayout(error_widget)
        error_layout.setContentsMargins(42, 4, 8, 4)
        error_layout.setSpacing(6)

        error_label = QLabel(f"⚠ {error_text}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet(
            f"color: #ff6b6b; font-size: {tc.FONT_BASE}px; background: transparent;"
        )
        error_layout.addWidget(error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        retry_btn = QPushButton("🔄 Retry")
        retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        retry_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: {tc.FONT_MD}px; padding: 5px 14px;
                background-color: {tc.get("accent_primary")}; color: white;
                border: none; border-radius: {tc.RADIUS_MD}px;
            }}
            QPushButton:hover {{ background-color: {tc.get("accent_primary_hover")}; }}
        """)
        retry_btn.clicked.connect(lambda: self._retry_last(error_widget))
        btn_row.addWidget(retry_btn)

        btn_row.addStretch()
        error_layout.addLayout(btn_row)

        self._message_layout.addWidget(error_widget)
        self._scroll_to_bottom()

    def _retry_last(self, error_widget: QWidget) -> None:
        """Retry the last failed request."""
        error_widget.deleteLater()
        if self._current_conversation and not self._streaming:
            # Remove the failed assistant message if any
            while (
                self._current_conversation.messages
                and self._current_conversation.messages[-1].role == "assistant"
                and not self._current_conversation.messages[-1].content
            ):
                self._current_conversation.messages.pop()
            self._stream_task = asyncio.ensure_future(self._stream_response())

    # ─── Tool execution ─────────────────────────────────────────────

    async def _execute_tool_calls(
        self, tool_calls_list, provider, model_id, display_model, system_prompt
    ) -> None:
        _tool_status_map = {
            "file_read": "Reading file...",
            "file_write": "Writing file...",
            "file_patch": "Patching file...",
            "file_delete": "Deleting file...",
            "dir_create": "Creating directory...",
            "dir_delete": "Deleting directory...",
            "file_search": "Searching files...",
            "list_directory": "Listing directory...",
            "shell_exec": "Running command...",
            "web_search": "Searching web...",
            "git_status": "Checking git status...",
            "git_diff": "Getting git diff...",
            "git_log": "Getting git log...",
            "git_commit": "Committing changes...",
            "git_show_file": "Reading file from git...",
        }
        for tool_call in tool_calls_list:
            logger.info(
                "Executing tool: %s (id=%s, needs_approval=%s)",
                tool_call.function_name,
                tool_call.id,
                self._tool_registry.needs_approval(tool_call.function_name)
                if self._tool_registry
                else "no_registry",
            )
            status = _tool_status_map.get(
                tool_call.function_name, f"Running {tool_call.function_name}..."
            )
            self._set_agent_status(status)
            # Don't pollute the assistant message with tool status

            # Check if this is an MCP tool
            is_mcp = self._mcp_client and self._mcp_client.is_mcp_tool(tool_call.function_name)

            if (
                not is_mcp
                and self._tool_registry
                and self._tool_registry.needs_approval(tool_call.function_name)
            ):
                approved = await self._request_tool_approval(
                    tool_call.function_name, tool_call.arguments
                )
                if not approved:
                    self._add_separator()
                    self._add_message_widget(
                        "tool", f"**{tool_call.function_name}** rejected by user."
                    )
                    self._current_conversation.messages.append(
                        Message(
                            role="tool",
                            content="User rejected this tool call.",
                            tool_call_id=tool_call.id,
                        )
                    )
                    continue

            if is_mcp:
                import json as _json

                try:
                    args = _json.loads(tool_call.arguments) if tool_call.arguments else {}
                except (ValueError, TypeError):
                    args = {}
                result = await self._mcp_client.call_tool(tool_call.function_name, args)
            elif self._tool_registry:
                result = await self._tool_registry.execute(
                    tool_call.function_name, tool_call.arguments
                )
            elif tool_call.function_name == "web_search":
                # Standalone mode (no project) — web_search still works
                import json as _json

                try:
                    args = _json.loads(tool_call.arguments) if tool_call.arguments else {}
                except (ValueError, TypeError):
                    args = {}
                from polyglot_ai.core.ai.tools.shell_tools import web_search

                result = await web_search(args)
            else:
                result = f"Error: Open a project to use '{tool_call.function_name}'"
            # Show compact tool status — not the raw output
            _tool_done_labels = {
                "web_search": "Searched the web",
                "file_read": "Read file",
                "file_search": "Searched files",
                "list_directory": "Listed directory",
                "git_status": "Checked git status",
                "git_diff": "Got git diff",
                "git_log": "Got git log",
                "git_show_file": "Read file from git",
                "shell_exec": "Ran command",
                "file_write": "Wrote file",
                "file_patch": "Patched file",
                "file_delete": "Deleted file",
                "dir_create": "Created directory",
                "dir_delete": "Deleted directory",
                "git_commit": "Committed changes",
            }
            done_label = _tool_done_labels.get(
                tool_call.function_name, f"Ran {tool_call.function_name}"
            )
            # Show compact inline status — not the raw tool output
            from PyQt6.QtWidgets import QLabel

            status_label = QLabel(f"  {done_label}")
            status_label.setStyleSheet(
                "color: #888; font-size: 12px; font-style: italic; padding: 2px 0;"
            )
            self._message_layout.addWidget(status_label)

            self._current_conversation.messages.append(
                Message(role="tool", content=result, tool_call_id=tool_call.id)
            )

        self._current_assistant_msg = None
        await self._stream_followup(provider, model_id, display_model, system_prompt)

    async def _request_tool_approval(self, tool_name: str, arguments: str) -> bool:
        # Inline approval — append a one-line approve/reject row to
        # the chat stream and await the user's click. The row matches
        # the existing italic-gray tool-status label aesthetic so it
        # blends into the conversation instead of breaking it up with
        # a popup.
        from polyglot_ai.ui.panels.inline_approval_card import InlineApprovalCard

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        row = InlineApprovalCard(tool_name, arguments, parent=self._message_widget)

        def _on_decided(approved: bool) -> None:
            if not future.done():
                future.set_result(approved)

        row.decided.connect(_on_decided)
        self._message_layout.addWidget(row)
        self._scroll_to_bottom()
        return await future

    async def _stream_followup(
        self, provider, model_id, display_model, system_prompt, _depth: int = 0
    ) -> None:
        if not self._current_conversation or _depth > 5:
            return

        logger.info("Starting follow-up stream (depth=%d)", _depth)
        self._set_agent_status("Analyzing results...")
        messages = self._current_conversation.get_api_messages()

        self._add_separator()
        model_label = f"{display_model} ({provider.display_name})"
        self._current_assistant_msg = ChatMessage("assistant", "", model=model_label)
        self._current_assistant_msg.on_apply_code = self._apply_code_blocks
        self._current_assistant_msg.on_run_command = self._run_command
        self._current_assistant_msg.on_regenerate = lambda: self._regenerate_last()
        self._message_layout.addWidget(self._current_assistant_msg)

        full_content = ""
        tool_calls_data: dict[int, dict] = {}
        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model_id,
                tools=self._tools,
                system_prompt=system_prompt,
            ):
                if chunk.delta_content:
                    full_content += chunk.delta_content
                    self._current_assistant_msg.append_content(chunk.delta_content)
                    self._scroll_to_bottom()
                if chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        idx = tc["index"]
                        if idx not in tool_calls_data:
                            tool_calls_data[idx] = {
                                "id": tc.get("id", ""),
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("id"):
                            tool_calls_data[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            tool_calls_data[idx]["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            tool_calls_data[idx]["function"]["arguments"] += func["arguments"]

            logger.info(
                "Follow-up stream done, content length: %d, tool_calls: %d",
                len(full_content),
                len(tool_calls_data),
            )

            # Build tool calls list
            tool_calls_list = None
            if tool_calls_data:
                tool_calls_list = [
                    ToolCall(
                        id=tc["id"],
                        function_name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in tool_calls_data.values()
                    if tc["function"]["name"]
                ]
                if not tool_calls_list:
                    tool_calls_list = None

            # Save assistant message
            self._current_conversation.messages.append(
                Message(
                    role="assistant",
                    content=full_content if full_content else None,
                    tool_calls=tool_calls_list,
                    model=model_id,
                )
            )

            # Execute any tool calls and recurse
            if tool_calls_list:
                self._current_assistant_msg = None
                await self._execute_tool_calls(
                    tool_calls_list, provider, model_id, display_model, system_prompt
                )
            elif full_content:
                await self._auto_apply(full_content)

            await self._persist_conversation()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error during follow-up streaming")
        finally:
            self._set_streaming_ui(False)
            self._current_assistant_msg = None

    # ─── Conversation persistence ───────────────────────────────────

    async def _persist_conversation(self) -> None:
        if not self._db or not self._current_conversation:
            return

        conv = self._current_conversation
        if conv.id is None:
            title = conv.messages[0].content[:50] if conv.messages else "New Chat"
            # If a task is active, prefix the conversation title so it's
            # easy to spot in the conversation list.
            if self._active_task is not None:
                task_title = getattr(self._active_task, "title", "")
                if task_title:
                    title = f"[{task_title[:30]}] {title}"[:80]
            conv.id = await self._db.create_conversation(title, conv.model)
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, conv.id)
            self._conv_list.insertItem(0, item)
            # Bind this freshly-created conversation to the active task
            # so future activations of the same task land back here.
            if self._task_manager is not None and self._active_task is not None:
                try:
                    self._task_manager.update_active(chat_session_id=str(conv.id))
                    self._task_manager.add_note(
                        "chat_started",
                        f"Started conversation '{title}'",
                        data={"conversation_id": conv.id},
                    )
                except Exception:
                    logger.exception("chat_panel: could not bind conversation to task")

        new_messages = conv.messages[self._persisted_message_count :]
        for msg in new_messages:
            await self._db.insert_message(
                conv.id,
                msg.role,
                content=msg.content,
                tool_calls=[
                    {"id": tc.id, "function": {"name": tc.function_name, "arguments": tc.arguments}}
                    for tc in msg.tool_calls
                ]
                if msg.tool_calls
                else None,
                tool_call_id=msg.tool_call_id,
                model=msg.model,
                tokens_in=msg.tokens_in,
                tokens_out=msg.tokens_out,
            )
        # Append a single rolled-up note per persist call so the task
        # timeline doesn't get spammed with one entry per token. We log
        # how many user/assistant messages were just persisted plus the
        # last assistant reply preview.
        if (
            self._task_manager is not None
            and self._active_task is not None
            and self._task_manager.active is not None
            and new_messages
        ):
            try:
                self._record_chat_note(new_messages)
            except Exception:
                logger.exception("chat_panel: could not record chat note on task")
        self._persisted_message_count = len(conv.messages)

    def _record_chat_note(self, new_messages: list) -> None:
        """Append a single timeline note summarising the latest exchange."""
        if self._task_manager is None:
            return
        user_msgs = [m for m in new_messages if m.role == "user"]
        ai_msgs = [m for m in new_messages if m.role == "assistant"]
        if not user_msgs and not ai_msgs:
            return
        if ai_msgs and ai_msgs[-1].content:
            preview = ai_msgs[-1].content.strip().splitlines()[0][:120]
            text = f"AI replied: {preview}"
            kind = "ai_response"
        elif user_msgs:
            preview = user_msgs[-1].content.strip().splitlines()[0][:120]
            text = f"You asked: {preview}"
            kind = "user_message"
        else:
            return
        self._task_manager.add_note(
            kind,
            text,
            data={
                "user_messages": len(user_msgs),
                "ai_messages": len(ai_msgs),
            },
        )

    # ─── Conversation loading ───────────────────────────────────────

    def _filter_category(self, category: str) -> None:
        """Filter conversation list by category."""
        self._active_category = category
        for name, btn in self._cat_buttons.items():
            btn.setChecked(name == category)
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self.populate_conversations(), name="populate_conversations")

    async def populate_conversations(self) -> None:
        if not self._db:
            return
        self._conv_list.clear()
        category = getattr(self, "_active_category", "all")
        conversations = await self._db.list_conversations(category=category)
        for conv in conversations:
            title = conv["title"]
            if conv.get("pinned"):
                title = f"📌 {title}"
            cat = conv.get("category", "all")
            if cat and cat != "all":
                cat_icons = {"work": "💼", "personal": "👤", "research": "🔬"}
                title = f"{cat_icons.get(cat, '')} {title}"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, conv["id"])
            self._conv_list.addItem(item)

    def _new_conversation(self) -> None:
        full_id, display = self._get_selected_model()
        self._current_conversation = Conversation(model=full_id or display)
        self._persisted_message_count = 0
        self._clear_messages()
        try:
            self._welcome.show()
        except RuntimeError:
            pass
        logger.info("Started new conversation")

    def _clear_messages(self) -> None:
        while self._message_layout.count() > 0:
            item = self._message_layout.takeAt(0)
            widget = item.widget()
            if widget and widget is not self._welcome:
                widget.deleteLater()
        self._welcome.setParent(None)
        self._message_layout.addWidget(self._welcome)

    def _on_conversation_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self._conv_list.item(row)
        if not item:
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._load_conversation(conv_id), name="load_conversation")

    async def _load_conversation(self, conv_id: int) -> None:
        if not self._db:
            return
        conv_data = await self._db.fetchone(
            "SELECT model FROM conversations WHERE id = ?", (conv_id,)
        )
        stored_model = conv_data["model"] if conv_data else "gpt-5.4"
        messages = await self._db.get_messages(conv_id)
        self._clear_messages()
        self._welcome.hide()

        self._current_conversation = Conversation(id=conv_id, model=stored_model)
        self._persisted_message_count = 0

        for msg_data in messages:
            role = msg_data["role"]
            content = msg_data.get("content", "")
            tool_calls = msg_data.get("tool_calls")
            parsed_tool_calls = None
            if tool_calls:
                parsed_tool_calls = [
                    ToolCall(
                        id=tc.get("id", ""),
                        function_name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", ""),
                    )
                    for tc in tool_calls
                ]
            if role in ("user", "assistant", "tool", "system"):
                if role in ("user", "assistant", "tool"):
                    msg_widget = ChatMessage(role, content or "")
                    msg_widget.message_db_id = msg_data.get("id")
                    msg_widget.on_fork = self._fork_from_message
                    if role == "assistant":
                        msg_widget.on_regenerate = lambda: self._regenerate_last()
                    self._message_layout.addWidget(msg_widget)
                self._current_conversation.messages.append(
                    Message(
                        role=role,
                        content=content,
                        tool_calls=parsed_tool_calls,
                        tool_call_id=msg_data.get("tool_call_id"),
                        model=msg_data.get("model"),
                        tokens_in=msg_data.get("tokens_in"),
                        tokens_out=msg_data.get("tokens_out"),
                    )
                )

        self._persisted_message_count = len(self._current_conversation.messages)
        self._scroll_to_bottom()

    # ─── Model selection ────────────────────────────────────────────

    def _get_selected_model(self) -> tuple[str | None, str]:
        full_id = self._model_combo.currentData()
        display = self._model_combo.currentText().strip()
        if not full_id or display.startswith("──"):
            return None, display
        # Strip capability badges from display
        for badge in (" 👁", " 🧠", " ⚡"):
            display = display.replace(badge, "")
        return full_id, display.strip()

    # ─── Auto-apply ─────────────────────────────────────────────────

    async def _auto_apply(self, content: str) -> None:
        import re
        from polyglot_ai.core.ai.code_applier import parse_code_blocks

        project_root = self._get_project_root()
        if not project_root:
            return

        blocks = parse_code_blocks(content)
        commands = re.findall(r"^\$ (.+)$", content, re.MULTILINE)

        if not blocks and not commands:
            return

        if self._current_assistant_msg:
            clean = content
            clean = re.sub(r"```\w+\s+[\w./\-]+(?:\.\w+)?\s*\n.*?```", "", clean, flags=re.DOTALL)
            clean = re.sub(r"^\$ .+$", "", clean, flags=re.MULTILINE)
            clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
            if not clean:
                clean = "I've prepared the changes."
            self._current_assistant_msg.set_final_content(clean)

        # Send proposed changes to the Changes panel for review
        if blocks:
            window = self.window()
            if hasattr(window, "_changeset_panel"):
                for block in blocks:
                    original = ""
                    if project_root:
                        target = project_root / block["path"]
                        if target.exists():
                            try:
                                original = target.read_text(encoding="utf-8", errors="replace")
                            except OSError:
                                pass
                    window._changeset_panel.add_change(
                        block["path"], original, block.get("content", "")
                    )

        self._add_separator()

        prompt_parts = []
        if blocks:
            file_list = ", ".join(f"**{b['path']}**" for b in blocks)
            prompt_parts.append(f"Apply changes to {file_list}?")
        if commands:
            for cmd in commands:
                prompt_parts.append(f"Run `{cmd}`?")

        approval_msg = ChatMessage("system", "\n".join(prompt_parts))
        self._message_layout.addWidget(approval_msg)

        btn_bar = QWidget()
        btn_bar.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(42, 2, 8, 6)
        btn_layout.setSpacing(8)

        if blocks:
            apply_btn = QPushButton("Yes, apply")
            apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: {tc.FONT_MD}px; padding: 5px 14px;
                    background-color: {tc.get("accent_primary")}; color: white;
                    border: none; border-radius: {tc.RADIUS_MD}px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {tc.get("accent_primary_hover")}; }}
            """)
            apply_btn.clicked.connect(lambda: self._approve_and_apply(blocks, btn_bar))
            btn_layout.addWidget(apply_btn)

        if commands:
            for cmd in commands:
                run_btn = QPushButton("Run")
                run_btn.setToolTip(cmd)
                run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                run_btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: {tc.FONT_MD}px; padding: 5px 14px;
                        background-color: {tc.get("bg_hover")}; color: {tc.get("text_heading")};
                        border: none; border-radius: {tc.RADIUS_MD}px;
                    }}
                    QPushButton:hover {{ background-color: {tc.get("bg_hover")}; }}
                """)
                run_btn.clicked.connect(lambda checked, c=cmd: self._approve_and_run(c))
                btn_layout.addWidget(run_btn)

        no_btn = QPushButton("No, skip")
        no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        no_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: {tc.FONT_MD}px; padding: 5px 14px;
                background-color: transparent; color: {tc.get("text_tertiary")};
                border: 1px solid {tc.get("border_card")}; border-radius: {tc.RADIUS_MD}px;
            }}
            QPushButton:hover {{ background-color: {tc.get("bg_hover")}; color: {tc.get("text_primary")}; }}
        """)
        no_btn.clicked.connect(lambda: self._reject_changes(btn_bar))
        btn_layout.addWidget(no_btn)

        btn_layout.addStretch()
        self._message_layout.addWidget(btn_bar)
        self._scroll_to_bottom()

    def _approve_and_apply(self, blocks: list[dict], bar: QWidget) -> None:
        from polyglot_ai.core.ai.code_applier import apply_code_block

        project_root = self._get_project_root()
        if not project_root:
            return

        results = []
        for block in blocks:
            ok, msg = apply_code_block(project_root, block)
            results.append(f"{'✓' if ok else '✗'} {msg}")
            window = self.window()
            if hasattr(window, "audit"):
                window.audit.log(
                    "file_apply",
                    {
                        "path": block["path"],
                        "success": ok,
                        "message": msg,
                    },
                )

        bar.setParent(None)
        bar.deleteLater()
        self._add_system_message("**Changes applied:**\n" + "\n".join(results))

        window = self.window()
        if hasattr(window, "editor_panel"):
            for block in blocks:
                file_path = project_root / block["path"]
                if file_path.exists():
                    window.editor_panel.open_file(file_path)

    def _approve_and_run(self, command: str) -> None:
        project_root = self._get_project_root()
        if not project_root:
            return
        self._add_system_message(f"**Running:** `{command}`")
        window = self.window()
        if hasattr(window, "audit"):
            window.audit.log("command_run", {"command": command})

        async def do_run():
            from polyglot_ai.core.ai.code_applier import run_command_safe

            output, code = await run_command_safe(project_root, command, user_approved=True)
            status = "✓" if code == 0 else f"✗ Exit {code}"
            self._add_system_message(f"**{status}**\n```\n{output}\n```")

        from polyglot_ai.core.async_utils import safe_task

        safe_task(do_run(), name="run_command")

    def _reject_changes(self, bar: QWidget) -> None:
        bar.setParent(None)
        bar.deleteLater()
        self._add_system_message("Changes rejected.")
        window = self.window()
        if hasattr(window, "audit"):
            window.audit.log("changes_rejected")

    def _apply_code_blocks(self, blocks: list[dict]) -> None:
        from polyglot_ai.core.ai.code_applier import apply_code_block

        project_root = self._get_project_root()
        if not project_root:
            self._add_system_message("No project open. Open a project first (File → Open Project).")
            return
        results = []
        for block in blocks:
            ok, msg = apply_code_block(project_root, block)
            results.append(f"{'✓' if ok else '✗'} {msg}")
        self._add_system_message("\n".join(results))
        window = self.window()
        if hasattr(window, "editor_panel"):
            for block in blocks:
                file_path = project_root / block["path"]
                if file_path.exists():
                    window.editor_panel.open_file(file_path)

    def _run_command(self, command: str) -> None:
        from polyglot_ai.core.ai.code_applier import run_command_safe

        project_root = self._get_project_root()
        if not project_root:
            self._add_system_message("No project open.")
            return
        self._add_system_message(f"Running: `{command}`...")

        async def do_run():
            # user_approved=True — the user clicked the "Run" button,
            # which is explicit approval. Skip the command allowlist.
            output, code = await run_command_safe(project_root, command, user_approved=True)
            status = "✓" if code == 0 else f"✗ Exit {code}"
            self._add_system_message(f"**{status}**\n```\n{output}\n```")

        from polyglot_ai.core.async_utils import safe_task

        safe_task(do_run(), name="run_install_command")

    # ─── UI helpers ─────────────────────────────────────────────────

    def _get_project_root(self):
        window = self.window()
        if hasattr(window, "file_explorer") and window.file_explorer.project_root:
            return window.file_explorer.project_root
        return None

    def _create_thinking_indicator(self) -> QWidget:
        """Create an animated thinking indicator shown while AI is processing."""
        from PyQt6.QtCore import QTimer

        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        # Animated dots label
        dots_label = QLabel("●  ●  ●")
        dots_label.setStyleSheet(
            f"color: {tc.get('border_input')}; font-size: 18px; background: transparent; letter-spacing: 2px;"
        )
        layout.addWidget(dots_label)
        layout.addStretch()

        # Animate the dots opacity
        widget._dot_state = 0

        def animate():
            widget._dot_state = (widget._dot_state + 1) % 4
            s = widget._dot_state
            colors = ["#888", "#666", "#444"]
            # Rotate which dot is brightest
            c = [colors[(0 - s) % 3], colors[(1 - s) % 3], colors[(2 - s) % 3]]
            dots_label.setText(
                f'<span style="color:{c[0]}; font-size:18px;">●</span>'
                f'  <span style="color:{c[1]}; font-size:18px;">●</span>'
                f'  <span style="color:{c[2]}; font-size:18px;">●</span>'
            )

        timer = QTimer(widget)
        timer.timeout.connect(animate)
        timer.start(400)
        widget._timer = timer  # prevent GC

        return widget

    def _add_separator(self) -> None:
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {tc.get('bg_card')}; margin: 0px 40px;")
        self._message_layout.addWidget(sep)

    def _add_message_widget(
        self,
        role: str,
        content: str,
        model: str | None = None,
        db_id: int | None = None,
    ) -> None:
        if self._message_layout.count() > 1:
            self._add_separator()
        widget = ChatMessage(role, content, model=model)
        widget.message_db_id = db_id
        widget.on_fork = self._fork_from_message
        if role == "assistant":
            widget.on_apply_code = self._apply_code_blocks
            widget.on_run_command = self._run_command
            widget.on_regenerate = lambda: self._regenerate_last()
        elif role == "user":
            widget.on_edit = lambda w, c: self._edit_and_resend(w, c)
        self._message_layout.addWidget(widget)
        self._scroll_to_bottom()

    def _add_system_message(self, text: str) -> None:
        if self._message_layout.count() > 1:
            self._add_separator()
        widget = ChatMessage("system", text)
        self._message_layout.addWidget(widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            50,
            lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ),
        )

    # ─── Conversation forking ────────────────────────────────────────

    def _fork_from_message(self, msg_widget: ChatMessage) -> None:
        """Fork the current conversation from a specific message."""
        if not self._db or not self._current_conversation or not self._current_conversation.id:
            return
        if not msg_widget.message_db_id:
            return
        from polyglot_ai.core.async_utils import safe_task

        safe_task(
            self._do_fork(self._current_conversation.id, msg_widget.message_db_id),
            name="fork_conversation",
        )

    async def _do_fork(self, conv_id: int, fork_message_id: int) -> None:
        new_conv_id = await self._db.fork_conversation(conv_id, fork_message_id)
        self._add_system_message("Conversation forked. Switching to the new branch...")
        await self.populate_conversations()
        await self._load_conversation(new_conv_id)
        # Select the new conversation in the sidebar
        for i in range(self._conv_list.count()):
            item = self._conv_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == new_conv_id:
                self._conv_list.setCurrentRow(i)
                break

    # ─── Prompt templates ─────────────────────────────────────────

    _BUILTIN_TEMPLATES = [
        # ── Code Quality ──
        (
            "Bug hunt",
            "Look at the open file and find bugs — logic errors, off-by-one mistakes, unhandled edge cases, race conditions, or incorrect assumptions. For each bug, explain the problem, show where it is, and suggest a fix.",
            "review",
        ),
        (
            "Security audit",
            "Audit this code for security vulnerabilities:\n- Injection attacks (SQL, command, XSS)\n- Authentication/authorization flaws\n- Data exposure or leaks\n- Unsafe deserialization\n- Hardcoded secrets\n\nRate each finding as Critical/High/Medium/Low and provide a fix.",
            "review",
        ),
        (
            "Performance review",
            "Analyze this code for performance issues:\n- Unnecessary allocations or copies\n- O(n²) or worse algorithms that could be faster\n- Missing caching opportunities\n- Database N+1 queries\n- Blocking calls that should be async\n\nSuggest concrete optimizations with before/after examples.",
            "review",
        ),
        # ── Writing Code ──
        (
            "Write unit tests",
            "Write thorough unit tests for this code. Include:\n- Happy path tests\n- Edge cases (empty input, None, boundary values)\n- Error conditions (invalid input, exceptions)\n- Use descriptive test names that explain the scenario\n\nUse the project's existing test framework and patterns.",
            "testing",
        ),
        (
            "Add type hints",
            "Add complete Python type hints to all functions, methods, and class attributes in this file. Use modern syntax (X | None instead of Optional[X]). Add return types, parameter types, and generic types where appropriate.",
            "refactoring",
        ),
        (
            "Refactor this",
            "Refactor this code to be cleaner and more maintainable:\n- Extract repeated logic into helper functions\n- Simplify complex conditionals\n- Improve variable and function names\n- Reduce nesting depth\n- Keep the same behavior — no functional changes\n\nShow the full refactored code.",
            "refactoring",
        ),
        # ── Understanding ──
        (
            "Explain this code",
            "Explain this code in plain English:\n1. What does it do? (one-sentence summary)\n2. How does it work? (step by step)\n3. What are the key design decisions?\n4. What are the gotchas or non-obvious parts?",
            "understanding",
        ),
        (
            "How would you improve this?",
            "Review this code and suggest improvements. Don't make changes yet — just list what you'd do differently and why. Consider:\n- Architecture and design patterns\n- Error handling\n- Testability\n- Readability\n- Edge cases",
            "understanding",
        ),
        # ── Documentation ──
        (
            "Generate docstrings",
            "Add clear docstrings to all public functions, methods, and classes in this file. Include:\n- One-line summary\n- Args with types and descriptions\n- Returns description\n- Raises (if applicable)\n- Brief usage example for complex functions\n\nFollow the project's existing docstring style.",
            "documentation",
        ),
        (
            "Write README section",
            "Based on this code, write a clear README section that explains:\n- What this module/component does\n- How to use it (with code examples)\n- Configuration options\n- Common pitfalls",
            "documentation",
        ),
        # ── Debugging ──
        (
            "Debug this error",
            "I'm getting an error with this code. Help me debug it:\n1. Identify the likely root cause\n2. Explain why it's happening\n3. Show the fix\n4. Suggest how to prevent similar issues\n\n[Paste your error message below]",
            "debugging",
        ),
        (
            "Add error handling",
            "Add robust error handling to this code:\n- Catch specific exceptions (not bare except)\n- Add meaningful error messages\n- Log errors appropriately\n- Handle edge cases gracefully\n- Add input validation where missing\n\nKeep the happy path clean and readable.",
            "debugging",
        ),
        # ── DevOps ──
        (
            "Review Dockerfile",
            "Review this Dockerfile for:\n- Security (running as root, exposed secrets, base image trust)\n- Layer efficiency (ordering, caching, multi-stage)\n- Size optimization (unnecessary packages, cleanup)\n- Best practices (HEALTHCHECK, LABEL, non-root user)\n\nSuggest an improved version.",
            "devops",
        ),
        (
            "Review Terraform",
            "Review this Terraform configuration for:\n- Security (overly permissive IAM, public exposure, missing encryption)\n- Best practices (resource naming, tagging, modules)\n- State management concerns\n- Missing variables or outputs\n\nRate each finding and suggest fixes.",
            "devops",
        ),
        (
            "Review Kubernetes manifest",
            "Review this Kubernetes manifest for:\n- Security (privilege escalation, runAsRoot, missing network policies)\n- Resource limits and requests\n- Readiness/liveness probes\n- Labels and selectors consistency\n- Best practices for production readiness\n\nSuggest improvements.",
            "devops",
        ),
        (
            "Review CI/CD pipeline",
            "Review this CI/CD workflow for:\n- Security (secret handling, artifact trust, permissions)\n- Efficiency (caching, parallelism, conditional steps)\n- Reliability (retry logic, timeout settings, failure notifications)\n- Best practices for the CI system in use\n\nSuggest improvements.",
            "devops",
        ),
        (
            "Explain infrastructure",
            "Explain this infrastructure code in plain English:\n1. What resources does it create?\n2. How do they connect to each other?\n3. What are the security boundaries?\n4. What would happen if I apply/deploy this?\n5. Any risks or gotchas?",
            "devops",
        ),
        # ── Data Engineering ──
        (
            "Write SQL query",
            "Help me write a SQL query. I need:\n- [Describe what data you need]\n\nConsider:\n- Performance (proper JOINs, indexes, avoiding SELECT *)\n- Readability (CTEs over subqueries where clearer)\n- Edge cases (NULLs, duplicates, empty results)\n\nExplain the query logic step by step.",
            "data",
        ),
        (
            "Explain data pipeline",
            "Explain this data pipeline:\n1. What data does it process and where does it come from?\n2. What transformations are applied?\n3. Where does the output go?\n4. What are the failure modes?\n5. How would you monitor this in production?",
            "data",
        ),
        (
            "Optimize query",
            "Analyze this SQL query for performance:\n- Identify slow operations (full scans, cartesian products)\n- Suggest index additions\n- Recommend query rewrites\n- Consider partitioning strategies\n- Show EXPLAIN plan interpretation if applicable\n\nProvide the optimized version.",
            "data",
        ),
    ]

    async def _init_builtin_templates(self) -> None:
        """Seed built-in templates — replaces old builtins if count changed."""
        if not self._db:
            return
        existing = await self._db.list_prompt_templates()
        builtin_count = sum(1 for t in existing if t.get("is_builtin"))

        if builtin_count == len(self._BUILTIN_TEMPLATES):
            return  # Already up to date

        # Remove old builtins and re-seed
        for t in existing:
            if t.get("is_builtin"):
                await self._db.execute("DELETE FROM prompt_templates WHERE id = ?", (t["id"],))
        for name, content, category in self._BUILTIN_TEMPLATES:
            await self._db.create_prompt_template(name, content, category, is_builtin=True)

    def _show_template_menu(self) -> None:
        """Show template selection popup.

        Fetches templates asynchronously, then displays the menu
        synchronously. The menu.exec() call must NOT run inside an
        async task because it starts a nested Qt event loop that
        conflicts with qasync's asyncio loop.
        """
        if not self._db:
            return
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._fetch_and_show_templates(), name="fetch_templates")

    async def _fetch_and_show_templates(self) -> None:
        """Fetch templates from DB, then hand off to sync menu display."""
        templates = await self._db.list_prompt_templates()
        if not templates:
            await self._init_builtin_templates()
            templates = await self._db.list_prompt_templates()
        # Schedule synchronous menu display outside the async task
        # so menu.exec() doesn't block the asyncio event loop.
        from functools import partial

        QTimer.singleShot(0, partial(self._show_template_menu_sync, templates))

    def _show_template_menu_sync(self, templates: list[dict]) -> None:
        """Build and display the template menu synchronously."""
        from polyglot_ai.ui import theme_colors as tc

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {tc.get("bg_surface_overlay")}; border: 1px solid {tc.get("border_input")};
                border-radius: {tc.RADIUS_MD}px; padding: 6px 4px; min-width: 240px;
            }}
            QMenu::item {{
                padding: 6px 16px; color: {tc.get("text_primary")};
                border-radius: {tc.RADIUS_SM}px; margin: 1px 4px;
            }}
            QMenu::item:selected {{ background: {tc.get("bg_hover")}; }}
            QMenu::item:disabled {{ color: {tc.get("text_muted")}; background: transparent; }}
            QMenu::separator {{ height: 1px; background: {tc.get("border_secondary")}; margin: 4px 12px; }}
        """)

        # Group templates by category
        categories = {}
        custom = []
        for tmpl in templates:
            if tmpl.get("is_builtin"):
                cat = tmpl.get("category", "other")
                categories.setdefault(cat, []).append(tmpl)
            else:
                custom.append(tmpl)

        category_labels = {
            "review": "Code Quality",
            "testing": "Writing Code",
            "refactoring": "Writing Code",
            "understanding": "Understanding",
            "documentation": "Documentation",
            "debugging": "Debugging",
            "devops": "DevOps",
            "data": "Data Engineering",
        }

        shown_sections = set()
        for tmpl in templates:
            if tmpl.get("is_builtin"):
                cat = tmpl.get("category", "other")
                section = category_labels.get(cat, cat.title())
                if section not in shown_sections:
                    if shown_sections:
                        menu.addSeparator()
                    header = menu.addAction(f"  {section}")
                    header.setEnabled(False)
                    shown_sections.add(section)

            prefix = "" if tmpl.get("is_builtin") else "✦ "
            act = menu.addAction(f"    {prefix}{tmpl['name']}")
            content = tmpl["content"]
            act.triggered.connect(lambda checked, c=content: self._insert_template(c))

        menu.addSeparator()
        add_act = menu.addAction("  + Add custom template...")
        add_act.triggered.connect(self._add_custom_template)

        # Show menu above input
        pos = self._input.mapToGlobal(QPoint(0, -menu.sizeHint().height()))
        menu.exec(pos)

    def _insert_template(self, content: str) -> None:
        self._input.setPlainText(content)
        self._input.setFocus()

    def _add_custom_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Template Name", "Name:")
        if not ok or not name:
            return
        content, ok = QInputDialog.getMultiLineText(self, "Template Content", "Prompt template:")
        if not ok or not content:
            return
        if self._db:
            from polyglot_ai.core.async_utils import safe_task

            safe_task(self._db.create_prompt_template(name, content), name="db_create_template")

    # ─── Public API ─────────────────────────────────────────────────

    async def populate_models(self) -> None:
        """Refresh model list from providers."""
        if not self._provider_manager:
            return
        # Keep current default models, add live ones if available
        pass

    def set_provider_manager(self, pm: ProviderManager) -> None:
        self._provider_manager = pm

    def set_database(self, db: Database) -> None:
        self._db = db

    def set_context_builder(self, cb: ContextBuilder) -> None:
        self._context_builder = cb
        # Scan project files for @mention
        self._refresh_project_files()

    def set_event_bus(self, event_bus) -> None:
        """Wire the chat panel into the task lifecycle.

        Subscribes to ``task:changed`` so the chat panel automatically
        switches to the task's conversation (creating one if needed)
        and informs the context builder about the active task so the
        system prompt is scoped accordingly.
        """
        from polyglot_ai.core.task_manager import (
            EVT_TASK_CHANGED,
            get_task_manager,
        )

        self._event_bus = event_bus
        self._task_manager = get_task_manager()

        def _on_task_changed(task=None, **_):
            self._on_active_task_changed(task)

        event_bus.subscribe(EVT_TASK_CHANGED, _on_task_changed)

    def _on_active_task_changed(self, task) -> None:
        """Switch the chat to the task's conversation and update the prompt.

        - If the task already has a ``chat_session_id`` we load that
          conversation.
        - If not, we leave the current conversation alone but bind the
          next-created conversation to the task (handled in
          ``_save_current_conversation``).
        - Either way we hand the task off to the context builder so the
          system prompt block at the top reflects the new task.
        """
        if self._context_builder is not None:
            try:
                self._context_builder.set_active_task(task)
            except Exception:
                logger.exception("chat_panel: failed to set active task on context builder")

        self._active_task = task
        if task is None:
            return

        session_id = getattr(task, "chat_session_id", None)
        if session_id:
            try:
                conv_id = int(session_id)
            except (TypeError, ValueError):
                logger.warning(
                    "chat_panel: task %s has non-integer chat_session_id %r",
                    getattr(task, "id", "?"),
                    session_id,
                )
                return

            # Guard: if the current conversation is ALREADY this task's
            # session, skip the reload. Otherwise we'd race with
            # ``_persist_conversation`` which calls update_active()
            # (fires task:changed) BEFORE it finishes inserting the new
            # messages, causing the UI to briefly reload an empty
            # conversation and drop the assistant reply the user just saw.
            if self._current_conversation is not None and self._current_conversation.id == conv_id:
                return

            from polyglot_ai.core.async_utils import safe_task

            safe_task(self._load_conversation(conv_id), name="task_conv_load")

    def _refresh_project_files(self) -> None:
        """Scan project for files and feed to @mention popup."""
        project_root = self._get_project_root()
        if not project_root:
            return
        import os

        root = Path(project_root)
        skip_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            "dist",
            "build",
            ".tox",
            ".eggs",
        }
        files: list[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                for f in filenames:
                    rel = os.path.relpath(os.path.join(dirpath, f), root)
                    files.append(rel)
                if len(files) > 5000:
                    break
        except OSError:
            pass
        files.sort()
        self._input.set_project_files(files)

    def set_tools(self, tools: list[dict], registry) -> None:
        self._tools = tools
        self._tool_registry = registry
        # Make sure the bootstrap button reflects the registry state
        # the moment wiring completes (e.g. if the registry was re-
        # created on project open and was already active elsewhere).
        self._refresh_bootstrap_label()

    def _toggle_bootstrap_mode(self) -> None:
        """Turn the 15-minute bootstrap window on or off.

        No-op when no tool registry has been wired yet — the button
        should never be clickable before a project is open, but be
        defensive so a stray click doesn't crash the UI.
        """
        if self._tool_registry is None:
            return
        if self._tool_registry.is_bootstrap_active():
            self._tool_registry.disable_bootstrap_mode()
        else:
            self._tool_registry.enable_bootstrap_mode()
            self._bootstrap_timer.start()
        self._refresh_bootstrap_label()

    def _refresh_bootstrap_label(self) -> None:
        """Update button text/state. Stops its own timer on expiry."""
        if self._tool_registry is None or not self._tool_registry.is_bootstrap_active():
            self._bootstrap_btn.setText("  Bootstrap")
            self._bootstrap_btn.setIcon(self._make_unlock_icon())
            self._bootstrap_btn.setStyleSheet(
                f"QPushButton {{ font-size: {tc.FONT_SM}px; padding: 2px 10px; "
                f"background: {tc.get('bg_input')}; color: #fff; "
                f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; }}"
                f"QPushButton:hover {{ background: {tc.get('bg_hover')}; "
                f"border-color: {tc.get('accent_primary')}; }}"
            )
            if self._bootstrap_timer.isActive():
                self._bootstrap_timer.stop()
            return
        remaining = self._tool_registry.bootstrap_seconds_remaining()
        mins, secs = divmod(remaining, 60)
        self._bootstrap_btn.setText(f"  Bootstrap · {mins}:{secs:02d}")
        self._bootstrap_btn.setIcon(self._make_lock_icon())
        self._bootstrap_btn.setStyleSheet(
            f"QPushButton {{ font-size: {tc.FONT_SM}px; padding: 2px 10px; "
            f"background: {tc.get('accent_warning')}; color: #fff; "
            "border: none; border-radius: 4px; font-weight: 600; }"
        )

    # ── Workflow execution ──────────────────────────────────────────

    def _handle_workflow_command(self, arg: str) -> None:
        """Handle ``/workflow [name] [--key value ...]``."""
        from polyglot_ai.core.workflow_engine import (
            WorkflowLoader,
            parse_workflow_args,
            validate_inputs,
        )

        project_root = self._get_project_root()

        if not arg.strip():
            # List available workflows
            workflows = WorkflowLoader.list_workflows(project_root)
            if not workflows:
                self._add_system_message(
                    "No workflows found. Add YAML files to "
                    "`.polyglot/workflows/` or run `/workflow seed` to "
                    "create the built-in defaults."
                )
                return
            lines = ["**Available workflows:**"]
            for wf in workflows:
                inputs_hint = ""
                required = [i for i in wf.inputs if i.required]
                if required:
                    inputs_hint = " " + " ".join(f"--{i.name} <value>" for i in required)
                lines.append(f"• `/workflow {wf.slug}{inputs_hint}` — {wf.description}")
            self._add_system_message("\n".join(lines))
            return

        if arg.strip() == "seed":
            if not project_root:
                self._add_system_message("Open a project first to seed workflows.")
                return
            count = WorkflowLoader.seed_defaults(project_root)
            self._add_system_message(
                f"Seeded {count} workflow(s) to `.polyglot/workflows/`. "
                "Run `/workflow` to see the list."
            )
            return

        name, inputs = parse_workflow_args(arg)
        definition, load_error = WorkflowLoader.load(name, project_root)
        if not definition:
            msg = f"Workflow '{name}' not found. Run `/workflow` to see available workflows."
            if load_error:
                msg = f"Workflow '{name}' could not be loaded: {load_error}"
            self._add_system_message(msg)
            return

        ok, filled_inputs, missing = validate_inputs(definition, inputs)
        if not ok:
            missing_hints = ", ".join(f"`--{m}`" for m in missing)
            self._add_system_message(
                f"Missing required inputs for **{definition.name}**: {missing_hints}\n\n"
                f"Usage: `/workflow {name} {' '.join(f'--{m} <value>' for m in missing)}`"
            )
            return

        self._start_workflow(definition, filled_inputs)

    def _start_workflow(self, definition, inputs: dict[str, str]) -> None:
        """Kick off a workflow — creates conversation if needed, then runs steps."""
        if self._workflow_running:
            self._add_system_message("A workflow is already running.")
            return
        if not self._provider_manager or not self._provider_manager.has_providers:
            self._add_system_message("Please sign in or add an API key first.")
            return

        full_id, display_model = self._get_selected_model()
        if not full_id:
            self._add_system_message("Please select a model from the dropdown.")
            return

        # Ensure a conversation exists
        if self._current_conversation is None:
            self._current_conversation = Conversation(model=full_id or display_model)
            self._persisted_message_count = 0

        self._welcome.hide()
        self._workflow_running = True

        # Show workflow start banner
        input_summary = ", ".join(f"{k}={v}" for k, v in inputs.items())
        self._add_system_message(
            f"**⚡ Starting workflow: {definition.name}**\n"
            f"{definition.description}\n"
            f"Inputs: {input_summary}\n"
            f"Steps: {len(definition.steps)}"
        )

        # Record on active task
        if hasattr(self, "_task_manager") and self._task_manager:
            try:
                self._task_manager.add_note(
                    "workflow_started",
                    f"Workflow started: {definition.name}",
                    data={
                        "workflow": definition.slug,
                        "inputs": inputs,
                        "steps": len(definition.steps),
                    },
                    category="workflow",
                )
            except Exception:
                logger.debug("Failed to record workflow_started note", exc_info=True)

        from polyglot_ai.core.async_utils import safe_task

        try:
            safe_task(
                self._run_workflow_steps(definition, inputs),
                name=f"workflow_{definition.slug}",
                on_error=lambda e: self._on_workflow_error(definition, inputs, e),
            )
        except Exception:
            self._workflow_running = False
            self._add_system_message("Failed to start workflow.")

    async def _run_workflow_steps(self, definition, inputs: dict[str, str]) -> None:
        """Execute each workflow step by injecting its prompt and streaming."""
        from polyglot_ai.core.ai.models import Message
        from polyglot_ai.core.workflow_engine import render_step_prompt

        completed = 0
        try:
            for i, step in enumerate(definition.steps):
                if not self._workflow_running:
                    break  # cancelled
                if self._current_conversation is None:
                    self._add_system_message("Conversation closed during workflow.")
                    break

                # Show step header
                self._add_system_message(f"**Step {i + 1}/{len(definition.steps)}: {step.name}**")

                # Render and inject the step prompt as a user message
                prompt = render_step_prompt(step, inputs)
                # Prefix with autonomous instruction so AI doesn't ask for
                # permission — the user already approved by launching the workflow.
                prompt = (
                    "[AUTONOMOUS WORKFLOW MODE — Do NOT ask for permission or "
                    "confirmation. Execute everything in this step immediately. "
                    "If something fails, fix it and retry. Never say 'Should I "
                    "go ahead?' — just do it.]\n\n" + prompt
                )
                self._current_conversation.messages.append(Message(role="user", content=prompt))
                self._add_message_widget("user", prompt)

                # Stream the AI response (reuses full tool-calling loop)
                await self._stream_response()
                completed += 1

        except asyncio.CancelledError:
            self._add_system_message("Workflow cancelled.")
        except Exception as e:
            logger.exception("Workflow step failed")
            self._add_system_message(f"Workflow error at step {completed + 1}: {e}")
        finally:
            self._finish_workflow(definition, inputs, completed)

    def _finish_workflow(self, definition, inputs: dict[str, str], steps_completed: int) -> None:
        """Clean up after workflow completes or fails."""
        self._workflow_running = False
        total = len(definition.steps)
        status = "completed" if steps_completed == total else "partial"

        self._add_system_message(
            f"**⚡ Workflow finished: {definition.name}** — "
            f"{steps_completed}/{total} steps {status}"
        )

        # Record on active task
        if hasattr(self, "_task_manager") and self._task_manager:
            try:
                self._task_manager.add_note(
                    "workflow_run",
                    f"Workflow {status}: {definition.name} ({steps_completed}/{total} steps)",
                    data={
                        "workflow": definition.slug,
                        "inputs": inputs,
                        "steps_completed": steps_completed,
                        "steps_total": total,
                        "status": status,
                    },
                    category="workflow",
                )
            except Exception:
                logger.debug("Failed to record workflow_run note", exc_info=True)

        # Publish to panel state for AI visibility
        try:
            from polyglot_ai.core import panel_state

            panel_state.set_last_workflow_run(
                {
                    "workflow": definition.slug,
                    "name": definition.name,
                    "status": status,
                    "steps_completed": steps_completed,
                    "steps_total": total,
                    "inputs": inputs,
                }
            )
        except Exception:
            logger.debug("Failed to publish workflow state", exc_info=True)

    def _on_workflow_error(self, definition, inputs: dict, error: Exception) -> None:
        """Callback for workflow task failures — ensures cleanup happens."""
        self._add_system_message(f"Workflow failed: {error}")
        self._finish_workflow(definition, inputs, 0)

    # ── Icon helpers for header buttons ──────────────────────────────

    @staticmethod
    def _make_unlock_icon():
        """White open-padlock icon for the inactive bootstrap button."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.setBrush(QColor(0, 0, 0, 0))
        # Lock body
        p.drawRoundedRect(2, 7, 10, 6, 1.5, 1.5)
        # Open shackle
        p.drawArc(4, 1, 6, 8, 0, 180 * 16)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _make_lock_icon():
        """White closed-padlock icon for the active bootstrap button."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.setBrush(QColor(0, 0, 0, 0))
        # Lock body
        p.drawRoundedRect(2, 7, 10, 6, 1.5, 1.5)
        # Closed shackle
        p.drawArc(4, 2, 6, 8, 0, 180 * 16)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _make_plus_icon():
        """White plus icon for the new-conversation button."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.8)
        p.setPen(pen)
        # Horizontal line
        p.drawLine(3, 7, 11, 7)
        # Vertical line
        p.drawLine(7, 3, 7, 11)
        p.end()
        return QIcon(pm)

    def prefill_input(self, text: str) -> None:
        """Public API: load text into the chat input box and focus it.

        Used by features like the test explorer's "Fix with AI" action
        to seed the chat with diagnostic context.
        """
        try:
            self._input.setPlainText(text)
            self._input.setFocus()
        except Exception:
            logger.exception("chat_panel: prefill_input failed")

    def refresh_mcp_tools(self, mcp_client) -> None:
        """Merge built-in tool defs with the MCP client's current tool list.

        Called when MCP servers connect/disconnect so the chat panel's
        tool snapshot stays in sync with reality. Without this, the
        sequential-thinking directive (and any other MCP tools) wouldn't
        become available until the next project-open.
        """
        if self._tool_registry is None:
            return
        builtin = self._tool_registry.get_tool_definitions()
        mcp_defs = mcp_client.get_tool_definitions() if mcp_client else []
        self._tools = builtin + mcp_defs
        logger.debug(
            "chat_panel: refreshed tool list (%d builtin + %d mcp)",
            len(builtin),
            len(mcp_defs),
        )

    def set_mcp_client(self, mcp_client) -> None:
        self._mcp_client = mcp_client
        # Check if GitHub is already connected
        if mcp_client and "github" in mcp_client.connected_servers:
            self._github_btn.setText("⌥ GitHub ✓")
            self._github_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {tc.get("bg_feedback_pos")}; color: {tc.get("accent_success_muted")}; font-size: {tc.FONT_MD}px;
                    border: 1px solid #2a5a3a; border-radius: {tc.RADIUS_LG}px;
                    padding: 4px 12px;
                    font-family: -apple-system, 'Segoe UI', sans-serif;
                }}
                QPushButton:hover {{ background: #1f4a35; }}
            """)

    @property
    def model_combo(self) -> QComboBox:
        return self._model_combo

    @property
    def send_button(self) -> QPushButton:
        return self._send_btn

    # ─── Icon creation (cached) ─────────────────────────────────────

    @staticmethod
    def _make_toolbar_icon(icon_type: str):
        """Create white toolbar icons (plus, template, etc.)."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 20
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if icon_type == "plus":
            # + icon
            painter.drawLine(10, 4, 10, 16)
            painter.drawLine(4, 10, 16, 10)
        elif icon_type == "search":
            # Magnifying glass icon
            from PyQt6.QtCore import QRectF

            painter.drawEllipse(QRectF(3, 3, 10, 10))
            painter.drawLine(12, 12, 17, 17)
        elif icon_type == "template":
            # Document/list icon (three horizontal lines with a corner fold)
            painter.drawLine(5, 5, 15, 5)
            painter.drawLine(5, 10, 15, 10)
            painter.drawLine(5, 15, 12, 15)

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_send_icon():
        """Up-arrow send icon (dark on light circle, like ChatGPT)."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 18
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#1a1a1a"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        # Up arrow: vertical line + chevron
        p.drawLine(9, 14, 9, 5)
        p.drawLine(9, 5, 5, 9)
        p.drawLine(9, 5, 13, 9)
        p.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_menu_icon(icon_type: str):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap as QPixmap2

        size = 18
        pixmap = QPixmap2(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#b0b0b0"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if icon_type == "paperclip":
            path = QPainterPath()
            path.moveTo(QPointF(12, 4))
            path.cubicTo(QPointF(15, 1), QPointF(17, 4), QPointF(14, 7))
            path.lineTo(QPointF(7, 14))
            path.cubicTo(QPointF(3, 17), QPointF(1, 14), QPointF(4, 11))
            path.lineTo(QPointF(10, 5))
            painter.drawPath(path)
        elif icon_type == "folder":
            painter.drawRoundedRect(2, 5, 14, 10, 2, 2)
            painter.drawRoundedRect(2, 4, 6, 3, 1, 1)
        elif icon_type == "terminal":
            painter.drawRoundedRect(2, 3, 14, 12, 2, 2)
            painter.drawLine(5, 7, 8, 9)
            painter.drawLine(8, 9, 5, 11)
            painter.drawLine(10, 12, 14, 12)
        elif icon_type == "plug":
            # Plug/connector icon for MCP
            painter.drawLine(9, 2, 9, 6)
            painter.drawLine(6, 2, 6, 6)
            painter.drawRoundedRect(4, 6, 10, 5, 2, 2)
            painter.drawLine(7, 11, 7, 14)
            painter.drawLine(10, 11, 10, 14)
            painter.drawLine(5, 14, 12, 14)
        elif icon_type == "gear":
            from PyQt6.QtCore import QRectF

            painter.drawEllipse(QRectF(5, 5, 8, 8))
            for angle in range(0, 360, 45):
                import math

                r = 8.5
                x = 9 + r * math.cos(math.radians(angle))
                y = 9 + r * math.sin(math.radians(angle))
                painter.drawLine(9, 9, int(x), int(y))

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_plus_icon() -> str:
        import tempfile

        cache_dir = tempfile.mkdtemp(prefix="codex_icons_")
        path = f"{cache_dir}/plus.png"
        from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap as QPixmap2

        pixmap = QPixmap2(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(8, 3, 8, 13)
        painter.drawLine(3, 8, 13, 8)
        painter.end()
        pixmap.save(path, "PNG")
        return path

    @staticmethod
    def _create_arrow_icon() -> str:
        import tempfile

        cache_dir = tempfile.mkdtemp(prefix="codex_icons_")
        path = f"{cache_dir}/arrow.png"
        from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap as QPixmap2

        pixmap = QPixmap2(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(2, 4, 6, 8)
        painter.drawLine(6, 8, 10, 4)
        painter.end()
        pixmap.save(path, "PNG")
        return path

    def _open_project_from_menu(self) -> None:
        window = self.window()
        if hasattr(window, "_action_open_project"):
            window._action_open_project.trigger()

    def _open_mcp_from_menu(self) -> None:
        """Open Settings dialog directly on MCP Servers tab."""
        window = self.window()
        from polyglot_ai.ui.dialogs.settings_dialog import SettingsDialog

        # Get keyring and settings from the main window
        if hasattr(window, "db"):
            dialog = SettingsDialog(window._settings, window._keyring, window)
            if hasattr(window, "_mcp_client"):
                dialog.set_mcp_client(window._mcp_client)
            # Pre-select MCP tab (index 4)
            dialog._nav_list.setCurrentRow(4)
            if dialog.exec():
                # Re-register providers if keys changed
                if hasattr(window, "_on_settings_saved"):
                    window._on_settings_saved()
        else:
            # Fallback: just open settings normally
            if hasattr(window, "_action_settings"):
                window._action_settings.trigger()

    def _open_settings_from_menu(self) -> None:
        window = self.window()
        if hasattr(window, "_action_settings"):
            window._action_settings.trigger()

    def _open_terminal_from_menu(self) -> None:
        """Show and focus the integrated terminal panel."""
        window = self.window()
        if hasattr(window, "_action_toggle_terminal"):
            # Ensure terminal is visible
            if not window._action_toggle_terminal.isChecked():
                window._action_toggle_terminal.toggle()
        if hasattr(window, "terminal_panel"):
            window.terminal_panel.setFocus()


class FileMentionPopup(QWidget):
    """Popup for @file mention fuzzy search."""

    file_selected = None  # Set by ChatInput

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("""
            QWidget { background: #2d2d2d; border: 1px solid #555; border-radius: 6px; }
            QListWidget { background: transparent; border: none; color: #d4d4d4;
                          font-size: 13px; font-family: monospace; }
            QListWidget::item { padding: 4px 8px; border-radius: 3px; }
            QListWidget::item:selected { background: #094771; }
            QListWidget::item:hover { background: #3e3e40; }
        """)
        from PyQt6.QtWidgets import QListWidget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._list = QListWidget()
        self._list.setMaximumHeight(200)
        self._list.itemActivated.connect(self._on_select)
        layout.addWidget(self._list)
        self._files: list[str] = []

    def set_files(self, files: list[str]) -> None:
        self._files = files

    def update_filter(self, query: str) -> None:
        self._list.clear()
        q = query.lower()
        matches = [f for f in self._files if q in f.lower()][:15]
        for m in matches:
            self._list.addItem(m)
        if matches:
            self._list.setCurrentRow(0)

    def _on_select(self, item) -> None:
        if self.file_selected:
            self.file_selected(item.text())
        self.hide()

    def select_current(self) -> None:
        item = self._list.currentItem()
        if item:
            self._on_select(item)

    def move_selection(self, delta: int) -> None:
        row = self._list.currentRow() + delta
        row = max(0, min(row, self._list.count() - 1))
        self._list.setCurrentRow(row)


class ChatInput(QTextEdit):
    """Text input with Enter-to-send, drag-drop files, clipboard paste, and @mention."""

    from PyQt6.QtCore import pyqtSignal as _pyqtSignal

    submit_requested = _pyqtSignal()
    file_dropped = _pyqtSignal(str)
    image_pasted = _pyqtSignal(QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._mention_popup = FileMentionPopup(self)
        self._mention_popup.file_selected = self._insert_mention
        self._mention_start = -1  # cursor pos where @ was typed
        self._project_files: list[str] = []

    def set_project_files(self, files: list[str]) -> None:
        """Update available files for @mention."""
        self._project_files = files
        self._mention_popup.set_files(files)

    def keyPressEvent(self, event):
        # Handle mention popup navigation
        if self._mention_popup.isVisible():
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._mention_popup.select_current()
                return
            if event.key() == Qt.Key.Key_Escape:
                self._mention_popup.hide()
                return
            if event.key() == Qt.Key.Key_Down:
                self._mention_popup.move_selection(1)
                return
            if event.key() == Qt.Key.Key_Up:
                self._mention_popup.move_selection(-1)
                return
            if event.key() == Qt.Key.Key_Tab:
                self._mention_popup.select_current()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submit_requested.emit()
            return

        super().keyPressEvent(event)

        # Check for @ trigger
        text = self.toPlainText()
        cursor_pos = self.textCursor().position()
        if event.text() == "@" and self._project_files:
            self._mention_start = cursor_pos
            self._show_mention_popup("")
        elif self._mention_popup.isVisible() and self._mention_start >= 0:
            # Update filter as user types after @
            if cursor_pos > self._mention_start:
                query = text[self._mention_start : cursor_pos]
                self._show_mention_popup(query)
            else:
                self._mention_popup.hide()

    def _show_mention_popup(self, query: str) -> None:
        self._mention_popup.update_filter(query)
        # Position above the cursor
        cursor_rect = self.cursorRect()
        pos = self.mapToGlobal(cursor_rect.topLeft())
        self._mention_popup.setFixedWidth(min(400, self.width()))
        self._mention_popup.move(pos.x(), pos.y() - self._mention_popup.sizeHint().height() - 4)
        self._mention_popup.show()

    def _insert_mention(self, filepath: str) -> None:
        """Replace @query with @filepath."""
        cursor = self.textCursor()
        # Select from @ to current position
        cursor.setPosition(self._mention_start - 1)  # -1 for the @ char
        cursor.setPosition(
            cursor.position() + (self.textCursor().position() - self._mention_start + 1),
            cursor.MoveMode.KeepAnchor,
        )
        cursor.insertText(f"@{filepath} ")
        self.setTextCursor(cursor)
        self._mention_start = -1

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or source.hasText()

    def insertFromMimeData(self, source):
        """Handle paste — images from clipboard, files from drag."""
        if source.hasImage():
            image = source.imageData()
            if image:
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    self.image_pasted.emit(pixmap)
                    return

        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    self.file_dropped.emit(url.toLocalFile())
            return

        # Fall back to text
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    self.file_dropped.emit(url.toLocalFile())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
