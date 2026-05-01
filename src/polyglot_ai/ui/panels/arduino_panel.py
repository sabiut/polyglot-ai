"""Arduino panel — compile & upload a project to a microcontroller.

Layout (top → bottom):

    1. Your project       ← what's loaded; empty state offers a
                            "Browse starters / Start blank" choice;
                            loaded state shows file path, language
                            chip, and a read-only code preview
    2. Plug in your board ← auto-refreshing detection line
    3. Upload             ← one big button + status feed + Ask AI

The panel calls ``ArduinoService`` directly so its status text stays
in plain language. Progress streams from the service's async
generators into the GUI thread via Qt signals — never via the bare
``EventBus``, which is documented as not thread-safe (see
``core/bridge.py``).

Designed to work for newcomers (the wizard flow + plain language)
without sandboxing power users (Advanced reveals board / port /
drive overrides; existing projects auto-load when a folder is
opened in the IDE).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.arduino import (
    Board,
    Language,
    board_for_fqbn,
    boards_for_language,
)
from polyglot_ai.core.arduino.project import (
    DetectedProject,
    create_blank,
    detect_in,
    language_for_file,
)
from polyglot_ai.core.arduino.service import (
    ArduinoService,
    DetectedBoard,
    StepUpdate,
)
from polyglot_ai.core.arduino.starters import copy_starter
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


# Polling cadence for board detection. Frequent enough that plugging
# a board in feels instant; cheap enough that a bored panel doesn't
# fork ``arduino-cli`` every second.
_BOARD_POLL_MS = 2500


# QSettings key for the auto-Ask preference. Stored via QSettings
# rather than the SQLite SettingsManager because (a) it's a tiny
# boolean toggle that doesn't need to round-trip with the rest of
# the app config, and (b) QSettings persists to the OS preferences
# location automatically, with no async hop.
_AUTO_ASK_KEY = "arduino/auto_ask_on_failure"


def _load_auto_ask_pref() -> bool:
    """Return the stored preference, defaulting to ``False``.

    Default-False on purpose: a brand-new user shouldn't have a chat
    round-trip fired the first time they hit a compile error. The
    checkbox in the Advanced panel makes the opt-in explicit.
    """
    return QSettings().value(_AUTO_ASK_KEY, False, type=bool)


def _save_auto_ask_pref(value: bool) -> None:
    QSettings().setValue(_AUTO_ASK_KEY, bool(value))


# Friendly labels for the language chip in step 1. The same mapping
# powers tooltips elsewhere — kept here so a future locale swap is
# a one-place change.
_LANGUAGE_DISPLAY: dict[Language, str] = {
    Language.CPP: "C++",
    Language.MICROPYTHON: "Python (MicroPython)",
    Language.CIRCUITPYTHON: "Python (CircuitPython)",
}


# ── Reusable little widgets ────────────────────────────────────────


class _StepBadge(QLabel):
    """Round, accent-coloured "1 / 2 / 3" anchor for each section."""

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(str(number), parent)
        self.setFixedSize(28, 28)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; "
            "border-radius: 14px; "
            f"font-size: {tc.FONT_BASE}px; font-weight: 700;"
        )


class _StepCard(QFrame):
    """A rounded card that wraps one wizard step."""

    def __init__(self, number: int, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stepCard")
        # The closing literal is a *plain* string, so ``}`` is the
        # right closer — not ``}}``. The f-string ``{{`` opens with
        # one brace; matching it with ``}}`` (which a plain string
        # passes through verbatim as two characters) emits malformed
        # CSS that Qt reports as ``Could not parse stylesheet of
        # object QFrame``.
        self.setStyleSheet(
            f"#stepCard {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 10px; }"
        )

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(20, 16, 20, 18)
        self._outer.setSpacing(14)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        head.addWidget(_StepBadge(number))
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_XL}px; font-weight: 700; "
            "background: transparent;"
        )
        head.addWidget(title_label)
        head.addStretch()
        self._outer.addLayout(head)

    def add_widget(self, widget: QWidget) -> None:
        self._outer.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._outer.addLayout(layout)


class _LanguageChip(QLabel):
    """Read-only pill that displays the project's detected language.

    The previous design exposed three toggle buttons to pick a
    language. That was wrong: language is a property of the project
    files (``.ino`` / ``main.py`` / ``code.py``), not a control.
    The chip removes the misconception that the user picks language
    independently — change the language by changing the project.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_secondary')}; "
            f"border: 1px solid {tc.get('border_card')}; "
            "border-radius: 10px; "
            f"padding: 3px 12px; font-size: {tc.FONT_SM}px; font-weight: 600;"
        )

    def show_language(self, language: Language) -> None:
        self.setText(_LANGUAGE_DISPLAY[language])


class _ReadOnlyCodeView(QPlainTextEdit):
    """A monospace, read-only code preview for the loaded entry file.

    Read-only by design: the panel hands editing off to the main
    editor panel via "Open in editor" so we don't end up with a
    second source of truth.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        # Hide the cursor — read-only views with a blinking caret
        # invite confusion.
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = QFont(tc.FONT_CODE.split(",")[0].strip().strip('"'))
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        self.setMinimumHeight(180)
        self.setMaximumHeight(360)
        # Note the closing brace: the trailing literal is ``"}"`` (one
        # plain ``}``) — not ``"}}"``. ``}}`` here is a non-f-string,
        # so the doubled brace would emit two literal ``}`` and Qt's
        # stylesheet parser would warn ``Could not parse stylesheet``
        # on the QFrame that wraps QPlainTextEdit's viewport.
        self.setStyleSheet(
            f"QPlainTextEdit {{ "
            f"background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 6px; padding: 10px; "
            "}"
        )

    # Keep a few read-only safe shortcuts; swallow the rest so a
    # stray Backspace doesn't sound a system bell.
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(self.tr("Copy")) or event.key() in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            super().keyPressEvent(event)
            return
        event.ignore()


# ── Main panel ─────────────────────────────────────────────────────


class ArduinoPanel(QWidget):
    """Top-level panel hosted in :class:`ArduinoWindow`."""

    # Service updates arrive on the GUI thread via this signal so the
    # service itself can stay Qt-free.
    _step_received = pyqtSignal(object)  # StepUpdate

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._service = ArduinoService()
        self._project_root: Path | None = None

        # Currently-loaded project. ``None`` means we're in the empty
        # state and step 1 shows the "Pick starter / Start blank"
        # buttons. Set by :meth:`_load_project`.
        self._project: DetectedProject | None = None

        # Board detection state.
        self._detected: list[DetectedBoard] = []
        self._board: Board | None = None
        self._port: str | None = None
        self._cp_drive: Path | None = None
        self._busy = False

        self._build_ui()

        # Cross-thread step updates → GUI slot.
        self._step_received.connect(self._on_step_update)

        # Periodic board detection.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._kick_detect)
        self._poll_timer.start(_BOARD_POLL_MS)
        QTimer.singleShot(50, self._kick_detect)  # immediate first scan

        self._render_project_state()
        self._refresh_upload_button()
        # Initial snapshot — even an empty state is useful so the AI
        # knows the panel exists and there's no project loaded yet.
        self._publish_panel_state()

    # ── External wiring ─────────────────────────────────────────────

    def set_project_root(self, path: Path | None) -> None:
        """Hook called by the main window when the IDE project changes.

        We auto-load the panel from the project root if it looks
        like an Arduino project — that way "Open Project" in the
        IDE menu is the same as "use my existing sketch" without
        requiring a separate picker in this panel.
        """
        self._project_root = Path(path) if path else None
        if self._project_root is not None and self._project is None:
            detected = detect_in(self._project_root)
            if detected is not None:
                self._load_project(detected, announce=True)

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_hero())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background: {tc.get('bg_base')};")
        outer.addWidget(scroll, 1)

        body = QWidget()
        scroll.setWidget(body)
        body.setStyleSheet(f"background: {tc.get('bg_base')};")
        col = QVBoxLayout(body)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(18)

        col.addWidget(self._build_step1_project())
        col.addWidget(self._build_step2_board())
        col.addWidget(self._build_step3_upload())

        # Advanced overrides (collapsed by default) sit at the foot.
        self._advanced_panel = self._build_advanced_panel()
        self._advanced_panel.setVisible(False)
        col.addWidget(self._advanced_panel)

        col.addStretch()

    def _build_hero(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("arduinoHero")
        wrap.setStyleSheet(
            f"#arduinoHero {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h = QHBoxLayout(wrap)
        h.setContentsMargins(28, 18, 20, 18)
        h.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title = QLabel("Build something with your Arduino")
        title.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_2XL}px; font-weight: 700; "
            "background: transparent;"
        )
        text_col.addWidget(title)
        sub = QLabel(
            "Pick or open a project, plug in your board, and press "
            "Upload. We'll handle the tricky bits."
        )
        sub.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_BASE}px; "
            "background: transparent;"
        )
        sub.setWordWrap(True)
        text_col.addWidget(sub)
        h.addLayout(text_col, 1)

        self._advanced_toggle = QPushButton("Advanced  ▾")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setStyleSheet(
            "QPushButton { "
            f"background: transparent; color: {tc.get('text_secondary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 6px; "
            f"padding: 6px 12px; font-size: {tc.FONT_SM}px; font-weight: 600; "
            "} "
            f"QPushButton:hover {{ color: {tc.get('text_primary')}; "
            f"border-color: {tc.get('border_input')}; }} "
            f"QPushButton:checked {{ color: {tc.get('text_on_accent')}; "
            f"background: {tc.get('accent_primary')}; "
            f"border-color: {tc.get('accent_primary')}; }}"
        )
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)
        h.addWidget(self._advanced_toggle, 0, Qt.AlignmentFlag.AlignTop)

        return wrap

    # Step 1 — project ----------------------------------------------

    def _build_step1_project(self) -> QWidget:
        card = _StepCard(1, "Your project")

        # The two states swap inside a QStackedWidget so the layout
        # never jumps between empty and loaded.
        self._project_stack = QStackedWidget()
        self._project_stack.addWidget(self._build_empty_project_state())
        self._project_stack.addWidget(self._build_loaded_project_state())
        card.add_widget(self._project_stack)
        return card

    def _build_empty_project_state(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        msg = QLabel("📂  No project loaded yet.")
        msg.setStyleSheet(
            f"color: {tc.get('text_primary')}; "
            f"font-size: {tc.FONT_LG}px; "
            "background: transparent; padding: 6px 4px;"
        )
        v.addWidget(msg)

        sub = QLabel(
            "Pick a project to start with, or scaffold a blank sketch you'll fill in yourself."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_BASE}px; "
            "background: transparent;"
        )
        v.addWidget(sub)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        starter_btn = QPushButton("📦  Pick a starter")
        starter_btn.setMinimumHeight(40)
        starter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        starter_btn.setStyleSheet(self._primary_button_qss())
        starter_btn.clicked.connect(lambda: self._open_change_dialog(initial_tab=0))
        blank_btn = QPushButton("📄  Start blank")
        blank_btn.setMinimumHeight(40)
        blank_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        blank_btn.setStyleSheet(self._secondary_button_qss())
        blank_btn.clicked.connect(lambda: self._open_change_dialog(initial_tab=1))
        existing_btn = QPushButton("📂  Open existing")
        existing_btn.setMinimumHeight(40)
        existing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        existing_btn.setStyleSheet(self._secondary_button_qss())
        existing_btn.clicked.connect(lambda: self._open_change_dialog(initial_tab=2))
        btn_row.addWidget(starter_btn)
        btn_row.addWidget(blank_btn)
        btn_row.addWidget(existing_btn)
        btn_row.addStretch()
        v.addLayout(btn_row)
        return page

    def _build_loaded_project_state(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # Top row — file name + language chip
        top = QHBoxLayout()
        top.setSpacing(10)
        self._file_label = QLabel("…")
        self._file_label.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_LG}px; font-weight: 600; "
            "background: transparent;"
        )
        top.addWidget(self._file_label)
        self._lang_chip = _LanguageChip()
        top.addWidget(self._lang_chip)
        top.addStretch()
        v.addLayout(top)

        # Path under the file name
        self._path_label = QLabel("")
        self._path_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        self._path_label.setWordWrap(True)
        v.addWidget(self._path_label)

        # Action row
        actions = QHBoxLayout()
        actions.setSpacing(8)
        open_btn = QPushButton("Open in editor")
        open_btn.setStyleSheet(self._secondary_button_qss())
        open_btn.clicked.connect(self._open_in_editor)
        actions.addWidget(open_btn)
        change_btn = QPushButton("Change project…")
        change_btn.setStyleSheet(self._secondary_button_qss())
        change_btn.clicked.connect(lambda: self._open_change_dialog())
        actions.addWidget(change_btn)
        actions.addStretch()
        self._show_code_btn = QPushButton("▸ Show code")
        self._show_code_btn.setCheckable(True)
        self._show_code_btn.setStyleSheet(self._secondary_button_qss())
        self._show_code_btn.toggled.connect(self._on_toggle_code_preview)
        actions.addWidget(self._show_code_btn)
        v.addLayout(actions)

        # Code preview (hidden by default)
        self._code_view = _ReadOnlyCodeView()
        self._code_view.setVisible(False)
        v.addWidget(self._code_view)
        return page

    # Step 2 — board -------------------------------------------------

    def _build_step2_board(self) -> QWidget:
        card = _StepCard(2, "Plug in your board")
        det_row = QHBoxLayout()
        det_row.setContentsMargins(0, 0, 0, 0)
        det_row.setSpacing(10)
        self._detection_label = QLabel("Looking for your board…")
        self._detection_label.setStyleSheet(
            f"color: {tc.get('text_primary')}; "
            f"font-size: {tc.FONT_LG}px; "
            "background: transparent; padding: 6px 4px;"
        )
        self._detection_label.setWordWrap(True)
        det_row.addWidget(self._detection_label, 1)
        refresh_btn = QPushButton("↻  Look again")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(self._secondary_button_qss())
        refresh_btn.clicked.connect(self._kick_detect)
        det_row.addWidget(refresh_btn)
        card.add_layout(det_row)
        return card

    # Step 3 — upload + status --------------------------------------

    def _build_step3_upload(self) -> QWidget:
        card = _StepCard(3, "Upload!")
        self._upload_button = QPushButton("Upload to Arduino")
        self._upload_button.setMinimumHeight(60)
        self._upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upload_button.clicked.connect(self._on_upload_clicked)
        card.add_widget(self._upload_button)

        self._status_view = QTextEdit()
        self._status_view.setReadOnly(True)
        self._status_view.setMinimumHeight(140)
        self._status_view.setStyleSheet(
            f"QTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 6px; padding: 10px; "
            f"font-size: {tc.FONT_BASE}px; }}"
        )
        self._status_view.setPlaceholderText(
            "Progress and any messages will appear here once you press Upload. ✨"
        )
        card.add_widget(self._status_view)

        self._ai_help_button = QPushButton("💬  Ask AI for help")
        self._ai_help_button.setVisible(False)
        self._ai_help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_help_button.setMinimumHeight(38)
        self._ai_help_button.clicked.connect(self._on_ask_ai)
        self._ai_help_button.setStyleSheet(self._primary_button_qss())
        card.add_widget(self._ai_help_button)
        return card

    # Advanced panel ------------------------------------------------

    def _build_advanced_panel(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 6px; }} "
            f"QLabel {{ color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; background: transparent; }} "
            f"QComboBox {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; "
            f"border-radius: 3px; padding: 3px 6px; "
            f"font-size: {tc.FONT_SM}px; }}"
        )
        v = QVBoxLayout(wrap)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        hdr = QLabel("Advanced overrides")
        hdr.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_BASE}px; "
            "font-weight: 600; background: transparent;"
        )
        v.addWidget(hdr)

        # Board override
        row = QHBoxLayout()
        row.addWidget(QLabel("Board:"))
        self._board_combo = QComboBox()
        self._refresh_board_combo()
        self._board_combo.currentIndexChanged.connect(self._on_board_override)
        row.addWidget(self._board_combo, 1)
        v.addLayout(row)

        # Port override
        row = QHBoxLayout()
        row.addWidget(QLabel("Port:"))
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.currentTextChanged.connect(self._on_port_override)
        row.addWidget(self._port_combo, 1)
        v.addLayout(row)

        # CIRCUITPY drive picker
        row = QHBoxLayout()
        row.addWidget(QLabel("CIRCUITPY drive:"))
        self._drive_label = QLabel("(auto)")
        self._drive_label.setStyleSheet(f"color: {tc.get('text_muted')};")
        row.addWidget(self._drive_label, 1)
        pick = QPushButton("Pick…")
        pick.clicked.connect(self._pick_cp_drive)
        row.addWidget(pick)
        v.addLayout(row)

        # Auto-Ask toggle. Default off: a brand-new user shouldn't
        # have an AI round-trip fire the first time their upload
        # fails. The opt-in lives in Advanced because the kid path
        # is the manual "Ask AI for help" button — the auto-flow is
        # for users who've decided they want it.
        self._auto_ask_box = QCheckBox("Automatically ask AI when upload fails")
        self._auto_ask_box.setStyleSheet(
            f"QCheckBox {{ color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; padding-top: 6px; }}"
        )
        self._auto_ask_box.setToolTip(
            "When checked, a failed upload sends the error and project "
            "context to the chat panel and presses Send for you. The "
            "chat window pops to the front so you see the AI's reply."
        )
        self._auto_ask_box.setChecked(_load_auto_ask_pref())
        self._auto_ask_box.toggled.connect(_save_auto_ask_pref)
        v.addWidget(self._auto_ask_box)
        return wrap

    # ── Button styles (shared) ─────────────────────────────────────

    @staticmethod
    def _primary_button_qss() -> str:
        return (
            "QPushButton { "
            f"background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; "
            "border: none; border-radius: 6px; "
            f"padding: 8px 16px; font-size: {tc.FONT_BASE}px; "
            "font-weight: 600; "
            "} "
            f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )

    @staticmethod
    def _secondary_button_qss() -> str:
        return (
            "QPushButton { "
            f"background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 6px; "
            f"padding: 7px 14px; font-size: {tc.FONT_BASE}px; "
            f"font-weight: 500; "
            "} "
            f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }}"
        )

    # ── Project lifecycle ──────────────────────────────────────────

    def _open_change_dialog(self, *, initial_tab: int = 0) -> None:
        """Show the modal that swaps the current project.

        ``initial_tab`` selects the starting tab — 0 = starter,
        1 = blank, 2 = open existing — so the empty-state buttons
        can deep-link straight to the right page.
        """
        # Imported lazily so the panel can be unit-tested without a
        # display attached when only the dialog needs Qt widgets.
        from polyglot_ai.ui.dialogs.arduino_change_dialog import (
            ArduinoChangeDialog,
        )

        default_target = self._project_root if self._project_root is not None else Path.home()
        dialog = ArduinoChangeDialog(default_target, parent=self.window())
        if 0 <= initial_tab < 3:
            dialog._stack.setCurrentIndex(initial_tab)
            for idx, btn in enumerate(
                (dialog._tab_starter, dialog._tab_blank, dialog._tab_existing)
            ):
                btn.setChecked(idx == initial_tab)
            dialog._on_tab_changed(initial_tab)
        if not dialog.exec():
            return
        result = dialog.result_value
        if result is None:
            return
        try:
            self._apply_change_result(result)
        except FileExistsError as exc:
            self._append_status(
                f"A file already exists at {exc}. Pick a different name or location.",
                kind="fail",
            )
        except OSError as exc:
            self._append_status(f"Couldn't create the project: {exc}", kind="fail")

    def _apply_change_result(self, result) -> None:
        """Materialise a :class:`ChangeResult` from the dialog."""
        if result.starter is not None:
            entry = copy_starter(result.starter, result.target_dir)
            language = language_for_file(entry) or result.starter.language
            project = DetectedProject(entry, entry.parent, language)
            self._load_project(project, announce=True)
            self._append_status(f"Loaded '{result.starter.name}' into {entry.parent}.", kind="ok")
            self._announce_next_step()
            return

        if result.blank_name is not None and result.blank_language is not None:
            project = create_blank(result.target_dir, result.blank_name, result.blank_language)
            self._load_project(project, announce=True)
            self._append_status(f"Created blank project at {project.project_dir}.", kind="ok")
            # Blank projects ship boilerplate, not real code — tell
            # the user the next move is to *write* something, not
            # plug a board in. Auto-expand the code preview so the
            # boilerplate is visible without an extra click.
            self._append_status(
                "The file just has empty setup() and loop() / a "
                "single print() — you'll want to add your own code.",
                kind="progress",
            )
            self._show_code_btn.setChecked(True)
            self._append_status(
                "Click 'Open in editor' above to start writing your code.",
                kind="hint",
            )
            return

        if result.existing is not None:
            self._load_project(result.existing, announce=True)
            self._append_status(
                f"Opened existing project: {result.existing.entry_file}.",
                kind="ok",
            )
            self._announce_next_step()

    def _load_project(self, project: DetectedProject, *, announce: bool = False) -> None:
        self._project = project
        self._render_project_state()
        if announce:
            logger.info("arduino: loaded project %s", project.entry_file)
        self._refresh_upload_button()
        self._publish_panel_state()

    # ── Snapshot for the chat panel ────────────────────────────────

    # Code-preview budget surfaced to the AI. Arduino sketches are
    # almost always shorter than this — pinned so a freak large
    # auto-generated sketch can't blow the token budget for the
    # rest of the system prompt.
    _CHAT_CODE_BUDGET = 3000

    def _publish_panel_state(self) -> None:
        """Push a compact snapshot for the chat panel's context.

        Called whenever the project, board, or upload-readiness
        changes. Read by ``core.ai.context.ContextBuilder`` so the
        AI sees what's loaded without each panel needing to wire
        itself into the chat plumbing.
        """
        from polyglot_ai.core import panel_state

        try:
            snapshot = self._build_state_snapshot()
        except Exception:
            logger.exception("arduino: failed to build panel snapshot")
            return
        panel_state.set_last_arduino_state(snapshot)

    def _build_state_snapshot(self) -> dict:
        """Pure builder for the Arduino panel snapshot."""
        snapshot: dict = {
            "loaded": self._project is not None,
            "toolchains": self._toolchain_summary(),
            "board": self._board_summary(),
        }
        if self._project is None:
            return snapshot

        ready, why = self._upload_readiness()
        code, source = self._read_code_for_snapshot()
        snapshot.update(
            {
                "entry_file": str(self._project.entry_file),
                "project_dir": str(self._project.project_dir),
                "language": self._project.language.value,
                "language_display": _LANGUAGE_DISPLAY[self._project.language],
                "ready_to_upload": ready,
                "blocker": None if ready else why,
                "code": code,
                # ``buffer`` = the editor has unsaved edits; the AI
                # should know to suggest saving before uploading.
                # ``disk`` = code reflects what's on disk (the safe
                # default).
                "code_source": source,
            }
        )
        return snapshot

    def _read_code_for_snapshot(self) -> tuple[str, str]:
        """Read the entry-file's source, clipped to the chat budget.

        Prefers an open editor buffer over the on-disk file — the
        user's most recent edits are what they're asking the AI
        about, even if Ctrl+S hasn't been pressed yet.

        Returns ``(text, source)`` where ``source`` is ``"buffer"``
        when a live, modified editor tab supplied the text and
        ``"disk"`` otherwise (so the snapshot can reflect "are
        these changes saved?" without a separate field).
        """
        if self._project is None:
            return "", "disk"

        buffer_text = self._read_open_buffer(self._project.entry_file)
        if buffer_text is not None:
            return self._clip_for_chat(buffer_text), "buffer"

        try:
            text = self._project.entry_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", "disk"
        return self._clip_for_chat(text), "disk"

    def _clip_for_chat(self, text: str) -> str:
        if len(text) > self._CHAT_CODE_BUDGET:
            return text[: self._CHAT_CODE_BUDGET] + "\n... (truncated)"
        return text

    def _read_open_buffer(self, path: Path) -> str | None:
        """Read the live buffer for ``path`` from the editor panel.

        Returns ``None`` when the editor panel isn't reachable, the
        file isn't open in any tab, or the matching tab's editor
        doesn't expose a text accessor we know how to drive. The
        caller falls back to the on-disk read in any of those cases.
        """
        editor_panel = self._find_editor_panel()
        if editor_panel is None:
            return None
        try:
            target = path.resolve()
        except OSError:
            return None
        # ``EditorPanel`` is a QTabWidget; iterate the tabs.
        for i in range(editor_panel.count()):
            tab = editor_panel.widget(i)
            file_path = getattr(tab, "file_path", None)
            if file_path is None:
                continue
            try:
                if file_path.resolve() != target:
                    continue
            except OSError:
                continue
            # EditorTab → QsciScintilla.text(); DocumentTab →
            # QPlainTextEdit.toPlainText().
            editor = getattr(tab, "editor", None)
            if editor is not None and hasattr(editor, "text"):
                return editor.text()
            source_editor = getattr(tab, "source_editor", None)
            if source_editor is not None and hasattr(source_editor, "toPlainText"):
                return source_editor.toPlainText()
        return None

    def _find_editor_panel(self):
        """Locate ``MainWindow._editor_panel`` from inside this widget.

        The Arduino panel runs inside its own top-level window whose
        parent is the ``MainWindow``; the editor panel hangs off the
        main window. Returns ``None`` if anything in the chain is
        missing (headless tests, alternative hosts, etc.).
        """
        host = self.window()
        parent = host.parent() if host is not None else None
        if parent is None:
            return None
        return getattr(parent, "_editor_panel", None)

    def _board_summary(self) -> dict | None:
        if self._board is None and self._port is None:
            return None
        return {
            "display_name": (
                self._board.display_name if self._board is not None else "Unknown board"
            ),
            "fqbn": self._board.fqbn if self._board is not None else None,
            "port": self._port,
        }

    def _toolchain_summary(self) -> dict:
        tc_state = self._service.detect_toolchains()
        return {
            "can_cpp": tc_state.can_cpp,
            "can_micropython": tc_state.can_micropython,
            "can_circuitpython": tc_state.can_circuitpython,
        }

    def _render_project_state(self) -> None:
        if self._project is None:
            self._project_stack.setCurrentIndex(0)
            return
        self._project_stack.setCurrentIndex(1)
        self._file_label.setText(self._project.entry_file.name)
        self._path_label.setText(str(self._project.project_dir))
        self._lang_chip.show_language(self._project.language)
        # Reset the show-code toggle and clear stale text whenever
        # the project changes — otherwise switching from Blink to
        # Tune would leave Blink's code visible until the user
        # re-clicked the toggle.
        self._show_code_btn.blockSignals(True)
        self._show_code_btn.setChecked(False)
        self._show_code_btn.setText("▸ Show code")
        self._show_code_btn.blockSignals(False)
        self._code_view.setVisible(False)
        self._code_view.setPlainText("")

    def _on_toggle_code_preview(self, checked: bool) -> None:
        if not checked:
            self._code_view.setVisible(False)
            self._show_code_btn.setText("▸ Show code")
            return
        if self._project is None:
            return
        try:
            text = self._project.entry_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"# Couldn't read {self._project.entry_file}: {exc}"
        self._code_view.setPlainText(text)
        self._code_view.setVisible(True)
        self._show_code_btn.setText("▾ Hide code")

    def _open_in_editor(self) -> None:
        """Forward the entry file to the IDE's main editor panel."""
        if self._project is None:
            return
        window = self.window()
        # The Arduino panel lives in its own top-level window, so
        # ``self.window()`` returns that window — we need to climb
        # one more step to the parent MainWindow that owns the
        # editor panel.
        parent = window.parent() if window is not None else None
        editor = getattr(parent, "_editor_panel", None) if parent else None
        if editor is None or not hasattr(editor, "open_file"):
            self._append_status(
                "Can't reach the editor — open it manually for now.",
                kind="fail",
            )
            return
        try:
            editor.open_file(self._project.entry_file)
        except Exception:
            logger.exception("arduino: open_in_editor failed")
            self._append_status("Couldn't open the file in the editor.", kind="fail")

    # ── Board detection (timer-driven) ─────────────────────────────

    def _kick_detect(self) -> None:
        if getattr(self, "_detecting", False):
            return
        self._detecting = True
        try:
            asyncio.ensure_future(self._run_detect())
        except RuntimeError:
            self._detecting = False

    async def _run_detect(self) -> None:
        try:
            boards = await self._service.list_connected_boards()
        except Exception:
            logger.exception("arduino: board detection failed")
            boards = []
        finally:
            self._detecting = False
        self._on_boards_detected(boards)

    def _on_boards_detected(self, boards: list[DetectedBoard]) -> None:
        # Track whether we had a board before this call so we can
        # tell the difference between "still no board" (no need to
        # spam the status feed) and "board just got plugged in"
        # (worth announcing the next step).
        had_board_before = self._board is not None

        self._detected = boards
        if not boards:
            # When detection comes up empty, mention the limitation
            # only if we know it's *because* pyserial is missing —
            # otherwise the user really does just need to plug in.
            tc_state = self._service.detect_toolchains()
            if not tc_state.pyserial_ok and not tc_state.can_cpp:
                self._detection_label.setText(
                    "🔌  Plug in your board with the USB cable, then press "
                    "<b>Look again</b>.<br>"
                    f"<span style='color:{tc.get('text_muted')}; "
                    f"font-size:{tc.FONT_SM}px;'>"
                    "Tip: install pyserial and arduino-cli to detect "
                    "more boards (<code>pip install pyserial</code>).</span>"
                )
            else:
                self._detection_label.setText(
                    "🔌  Plug in your board with the USB cable, then press <b>Look again</b>."
                )
            self._detection_label.setStyleSheet(
                f"color: {tc.get('text_secondary')}; "
                f"font-size: {tc.FONT_LG}px; "
                "background: transparent; padding: 6px 4px;"
            )
            self._board = None
            self._port = None
        else:
            preferred = next((b for b in boards if b.board is not None), boards[0])
            self._board = preferred.board
            self._port = preferred.port
            display = preferred.board.display_name if preferred.board else "Unknown board"
            self._detection_label.setText(
                f"✅  Found: <b>{display}</b>  "
                f"<span style='color:{tc.get('text_muted')}; "
                f"font-size:{tc.FONT_SM}px;'>on {preferred.port}</span>"
            )
            self._detection_label.setStyleSheet(
                f"color: {tc.get('accent_success')}; "
                f"font-size: {tc.FONT_LG}px; font-weight: 600; "
                "background: transparent; padding: 6px 4px;"
            )

        if hasattr(self, "_port_combo"):
            current = self._port_combo.currentText()
            self._port_combo.blockSignals(True)
            self._port_combo.clear()
            for det in boards:
                self._port_combo.addItem(det.port)
            if current:
                self._port_combo.setCurrentText(current)
            self._port_combo.blockSignals(False)

        self._refresh_upload_button()

        # Tell the user what to do next *only* on the rising edge —
        # "we just found a board". Polling the same connected board
        # every 2.5 s without this guard would flood the status
        # feed with hint lines.
        if not had_board_before and self._board is not None and self._project is not None:
            self._announce_next_step()

        # Re-publish the snapshot for the chat panel. Done only when
        # the board state actually changed — re-publishing on every
        # 2.5 s poll would burn cycles for no benefit.
        if (self._board is not None) != had_board_before:
            self._publish_panel_state()

    # ── Advanced overrides ─────────────────────────────────────────

    def _refresh_board_combo(self) -> None:
        # Show every board on every language since the combo is the
        # power-user override path. Filtering would surprise an
        # advanced user trying to flash ``arduino:avr:nano`` to a
        # CircuitPython project, etc.
        self._board_combo.clear()
        for lang in (Language.CPP, Language.MICROPYTHON, Language.CIRCUITPYTHON):
            for b in boards_for_language(lang):
                self._board_combo.addItem(f"{b.display_name}", b.fqbn)

    def _on_advanced_toggled(self, checked: bool) -> None:
        self._advanced_toggle.setText("Advanced ▴" if checked else "Advanced ▾")
        self._advanced_panel.setVisible(checked)

    def _on_board_override(self) -> None:
        fqbn = self._board_combo.currentData()
        if fqbn:
            self._board = board_for_fqbn(fqbn)
            self._refresh_upload_button()

    def _on_port_override(self, text: str) -> None:
        self._port = text.strip() or None
        self._refresh_upload_button()

    def _pick_cp_drive(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self, "Find the CIRCUITPY drive", str(Path.home())
        )
        if picked:
            self._cp_drive = Path(picked)
            self._drive_label.setText(picked)
            self._refresh_upload_button()

    # ── Upload ─────────────────────────────────────────────────────

    @property
    def _language(self) -> Language | None:
        """Convenience accessor — language flows from the project."""
        return self._project.language if self._project is not None else None

    def _refresh_upload_button(self) -> None:
        ready, why = self._upload_readiness()
        self._upload_button.setEnabled(ready and not self._busy)
        self._upload_button.setStyleSheet(self._upload_button_qss(ready))
        self._upload_button.setToolTip("" if ready else why)
        if self._busy:
            label = "Working…"
        elif not ready:
            # Show the *blocker* as the button text — "Plug in your
            # board first" is honest about what to do next; just
            # showing "Upload to Arduino" greyed out left the user
            # guessing why the button wasn't responding. Strip the
            # trailing period so the button doesn't look like a
            # sentence.
            label = (why or "Upload to Arduino").rstrip(".")
        else:
            target = "Arduino" if self._language is Language.CPP else "your board"
            label = f"Upload to {target}"
        self._upload_button.setText(label)

    def _upload_readiness(self) -> tuple[bool, str]:
        if self._project is None:
            return False, "Pick or create a project first."
        if self._board is None:
            return False, "Plug in your board first."
        language = self._language
        assert language is not None
        if not self._board.supports(language):
            return False, (f"This board doesn't run {_LANGUAGE_DISPLAY[language]}.")
        if language is Language.CIRCUITPYTHON:
            if self._cp_drive is None:
                return False, "Open Advanced and pick the CIRCUITPY drive."
        else:
            if not self._port:
                return False, "No serial port detected."
        return True, ""

    def _upload_button_qss(self, enabled: bool) -> str:
        if not enabled:
            return (
                "QPushButton { "
                f"background: {tc.get('bg_surface_raised')}; "
                f"color: {tc.get('text_muted')}; "
                f"border: 1px solid {tc.get('border_secondary')}; "
                "border-radius: 10px; "
                f"font-size: {tc.FONT_XL}px; font-weight: 600; "
                "}"
            )
        return (
            "QPushButton { "
            f"background: {tc.get('accent_success')}; "
            f"color: {tc.get('text_on_accent')}; "
            "border: none; border-radius: 10px; "
            f"font-size: {tc.FONT_XL}px; font-weight: 700; "
            "}"
            f"QPushButton:hover {{ background: {tc.get('accent_success_hover')}; }} "
            "QPushButton:pressed { padding-top: 2px; }"
        )

    def _on_upload_clicked(self) -> None:
        if self._busy:
            return
        ready, why = self._upload_readiness()
        if not ready:
            self._append_status(why, kind="fail")
            return
        self._busy = True
        self._ai_help_button.setVisible(False)
        self._service.last_error_detail = ""
        self._refresh_upload_button()
        self._append_status("— Starting —")
        try:
            asyncio.ensure_future(self._run_upload())
        except RuntimeError:
            self._busy = False
            self._refresh_upload_button()
            self._append_status(
                "Couldn't start the upload — no event loop. Try restarting the app.",
                kind="fail",
            )

    async def _run_upload(self) -> None:
        assert self._board is not None and self._project is not None
        language = self._language
        assert language is not None
        try:
            if language is Language.CPP:
                async for u in self._service.compile_cpp(self._project.project_dir, self._board):
                    self._step_received.emit(u)
                if self._service.last_error_detail:
                    return
                async for u in self._service.upload_cpp(
                    self._project.project_dir, self._board, self._port or ""
                ):
                    self._step_received.emit(u)
            elif language is Language.MICROPYTHON:
                async for u in self._service.upload_micropython(
                    self._project.entry_file, self._port or ""
                ):
                    self._step_received.emit(u)
            else:  # CircuitPython
                async for u in self._service.upload_circuitpython(
                    self._project.entry_file, self._cp_drive or Path()
                ):
                    self._step_received.emit(u)
        except Exception:
            logger.exception("arduino: upload failed")
            self._step_received.emit(StepUpdate("Something went wrong. Try again?", kind="fail"))
        finally:
            self._busy = False
            QTimer.singleShot(0, self._refresh_upload_button)

    # ── Status & AI handoff ────────────────────────────────────────

    def _on_step_update(self, update: StepUpdate) -> None:
        self._append_status(update.message, kind=update.kind)
        if update.kind == "fail" and self._service.last_error_detail:
            self._ai_help_button.setVisible(True)
            if _load_auto_ask_pref():
                # Opt-in path: pre-fill the chat with the error +
                # context AND press Send for the user. The chat
                # window is raised inside ``_on_ask_ai`` so the user
                # immediately sees the model's reply.
                self._append_status(
                    "Auto-asking the AI for help (you can turn this off in Advanced).",
                    kind="hint",
                )
                self._auto_ask_send()
            else:
                self._append_status(
                    "Click 'Ask AI for help' below — the AI will see "
                    "the code and the error and walk you through a fix.",
                    kind="hint",
                )

    def _auto_ask_send(self) -> None:
        """Pre-fill the chat AND programmatically click Send.

        Wraps :meth:`_on_ask_ai` (the manual handoff) so the message
        text and chat-window-raise behaviour stay identical between
        the manual and auto paths — no chance of drift.
        """
        chat = self._find_chat_panel()
        if chat is None:
            self._append_status(
                "Auto-Ask is on but the chat panel isn't reachable. "
                "Open the chat and click 'Ask AI for help' instead.",
                kind="fail",
            )
            return
        # Run the normal handoff (prefill + raise). Then click Send.
        self._on_ask_ai()
        send_btn = getattr(chat, "send_button", None)
        if send_btn is None:
            self._append_status(
                "Auto-Ask couldn't press Send — message is in the "
                "chat box, click Send to submit it.",
                kind="hint",
            )
            return
        # Defer the click one event-loop turn so prefill_input has
        # finished settling the input widget before the send slot
        # reads from it. Without this, a fast handler chain has
        # been observed to grab an empty buffer on Wayland.
        QTimer.singleShot(0, send_btn.click)

    def _append_status(self, message: str, kind: str = "progress") -> None:
        colour = {
            "ok": tc.get("accent_success"),
            "fail": tc.get("accent_danger"),
            "progress": tc.get("text_primary"),
            "hint": tc.get("accent_primary"),
        }.get(kind, tc.get("text_primary"))
        prefix = {"ok": "✅", "fail": "❌", "progress": "•", "hint": "→"}.get(kind, "•")
        self._status_view.append(f'<span style="color: {colour};">{prefix} {message}</span>')

    def _announce_next_step(self) -> None:
        """Tell the user what to do next based on current panel state.

        Called after every milestone (project loaded, board detected)
        so the status feed reads as a guided flow rather than a
        bare list of completed events. Skipped when the user is in
        the middle of an upload — the upload's own status messages
        cover the same ground.
        """
        if self._busy:
            return
        ready, why = self._upload_readiness()
        if ready:
            self._append_status(
                "Ready! Press the green Upload button when you are.",
                kind="hint",
            )
        elif self._project is not None and self._board is None:
            # Most-common path: the user just loaded a project and
            # hasn't plugged a board in yet.
            self._append_status(
                "Now plug in your board with the USB cable, then press Look again.",
                kind="hint",
            )
        elif why:
            self._append_status(why, kind="hint")

    def _on_ask_ai(self) -> None:
        chat = self._find_chat_panel()
        if chat is None or not hasattr(chat, "prefill_input"):
            self._append_status(
                "Can't reach the AI panel — open it and try again.",
                kind="fail",
            )
            return
        chat.prefill_input(self._build_ask_ai_message())
        # Switch focus to the chat panel so the user lands on the
        # pre-filled prompt instead of having to find it.
        try:
            window = chat.window() if hasattr(chat, "window") else None
            if window is not None:
                window.raise_()
                window.activateWindow()
        except Exception:
            logger.debug("arduino: couldn't raise chat window", exc_info=True)

    def _build_ask_ai_message(self) -> str:
        """Pre-fill text for the chat 'Ask AI for help' handoff.

        The system prompt's PANEL STATE block already shows the
        project / board / code, so we don't repeat it. We do
        include the raw stderr / stdout from the failure — that
        isn't published to panel state because it can be huge —
        clipped to 4 KB so a runaway compile log doesn't wipe out
        the chat token budget.
        """
        body = self._service.last_error_detail.strip() or "(no extra detail)"
        return (
            "My Arduino upload failed. The PANEL STATE block above shows "
            "what's loaded, the board, and the code — please use that "
            "context plus the toolchain output below to explain the "
            "problem in simple terms and propose a fix I can apply.\n\n"
            "Toolchain output:\n"
            f"```\n{body[:4000]}\n```"
        )

    def _find_chat_panel(self):
        # The panel is hosted inside an ``ArduinoWindow`` whose
        # parent is the ``MainWindow``. ``self.window()`` returns
        # that hosting window; the chat panel lives on the parent.
        host = self.window()
        parent = host.parent() if host is not None else None
        return getattr(parent, "_chat_panel", None) or getattr(parent, "chat_panel", None)


# ── Top-level window wrapper ───────────────────────────────────────


class ArduinoWindow(QWidget):
    """Standalone window that hosts an :class:`ArduinoPanel`.

    The activity-bar chip icon opens this window instead of toggling
    a sidebar — the wizard layout is too tall and tile-heavy for a
    200 px sidebar pane to be useful. The window is created once
    per :class:`MainWindow` and re-shown on subsequent clicks (so
    chosen project, board and status feed survive a close).

    The owning ``MainWindow`` keeps a strong reference so we don't
    rely on Qt's parent ownership alone — without that, closing the
    window would let the panel get garbage-collected and the next
    open would create a fresh, empty one.
    """

    def __init__(
        self,
        panel: ArduinoPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Arduino — Polyglot AI")
        self.resize(960, 720)
        if parent is not None:
            icon = parent.windowIcon()
            if not icon.isNull():
                self.setWindowIcon(icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel)
        self._panel = panel

    @property
    def panel(self) -> ArduinoPanel:
        return self._panel

    def show_and_raise(self) -> None:
        """Show the window, bring it to front, and give it focus."""
        self.show()
        self.raise_()
        self.activateWindow()


# Compatibility shim — ``QSizePolicy`` import is kept above so this
# module's downstream imports remain stable across the refactor.
_ = QSizePolicy  # noqa: F841
