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
from polyglot_ai.core.arduino.serial_monitor import COMMON_BAUDS, SerialMonitor
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

        # Slug of the starter the active project was scaffolded from
        # (or ``None`` for blanks / opened-existing projects). Used
        # by ``_after_success`` to surface a starter-specific
        # post_upload_hint — turns "Done!" into "Done! Look at
        # your board's LED — it should blink once a second."
        self._active_starter_slug: str | None = None

        # Board detection state.
        self._detected: list[DetectedBoard] = []
        self._board: Board | None = None
        self._port: str | None = None
        self._cp_drive: Path | None = None
        self._busy = False

        # Serial monitor — read-only stream of whatever the board
        # is printing back via Serial.println / print(). Built once
        # per panel and reused across reconnects so a user toggling
        # connect/disconnect doesn't lose the output buffer.
        self._serial_monitor = SerialMonitor()
        self._serial_monitor.line_received.connect(self._on_serial_line)
        self._serial_monitor.connected.connect(self._on_serial_connected)
        self._serial_monitor.disconnected.connect(self._on_serial_disconnected)
        self._serial_monitor.error.connect(self._on_serial_error)

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
        col.addWidget(self._build_step4_monitor())

        # Advanced overrides (collapsed by default) sit at the foot.
        self._advanced_panel = self._build_advanced_panel()
        self._advanced_panel.setVisible(False)
        col.addWidget(self._advanced_panel)

        # Toolchain status footer — small chip row showing
        # arduino-cli ✓/✗, mpremote ✓/✗, pyserial ✓/✗ at a
        # glance. Always visible (not gated by Advanced) because
        # "what's missing on this machine?" is the most useful
        # data point for *first-time* users — exactly the audience
        # the audit flagged as needing it. Refreshed by the same
        # board-detection poll that runs every 2.5s, so installing
        # a missing tool flips the chip to green without a panel
        # restart.
        col.addWidget(self._build_toolchain_footer())

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

        # Inline starter tiles — first three from the catalog. The
        # full picker (with all starters + blank + open-existing
        # tabs) is still one click away via "More starters…", but
        # surfacing the most likely choices inline removes a modal
        # for the common path: a kid sees "Blink a light", "Hello,
        # world!", and "Press a button" right there and clicks one.
        from polyglot_ai.core.arduino.starters import list_starters

        try:
            top_starters = list_starters()[:3]
        except Exception:
            logger.debug("arduino: failed to load starters for inline tiles", exc_info=True)
            top_starters = []
        if top_starters:
            tile_row = QHBoxLayout()
            tile_row.setSpacing(8)
            for starter in top_starters:
                tile_row.addWidget(self._build_starter_tile(starter))
            tile_row.addStretch(1)
            v.addLayout(tile_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        # Primary CTA flips meaning depending on whether tiles are
        # shown above. With tiles, "More starters…" is an honest
        # description — they've already seen three, this opens the
        # rest. Without (catalog failed to load), the original
        # "Pick a starter" stays.
        starter_btn = QPushButton("📦  More starters…" if top_starters else "📦  Pick a starter")
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

    def _build_starter_tile(self, starter) -> QWidget:
        """One emoji + name + blurb tile that loads ``starter`` on click.

        Tiles are deliberately light (no border, just hover
        feedback) so the row reads as "options to try" rather than
        "buttons to commit to." Click loads the starter directly
        into the user's project home, mirroring the change-dialog
        path so language/board/file all end up correct without
        requiring the picker UI.
        """
        tile = QPushButton()
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        # Floor the tile size to ``MinimumExpanding`` rather than
        # ``Fixed`` so a tile with a longer blurb (e.g. the
        # CircuitPython blink starter, whose description spans two
        # lines) gets the height it needs to render its wrapped
        # text. The earlier fixed 72 px cap was clipping the second
        # line on long blurbs and reading as half-truncated text.
        # The minimum width keeps narrow tiles from collapsing
        # when three are crammed into a sidebar; the layout's
        # stretch handles the rest.
        tile.setMinimumWidth(170)
        tile.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )
        # Compose the visible text on the button — emoji big,
        # name bold, blurb thin and wrapped. ``setText`` doesn't
        # render rich text, so we use a layout-on-button trick
        # via QLabel children.
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        emoji_lbl = QLabel(f"{starter.emoji}  {starter.name}")
        emoji_lbl.setStyleSheet(
            f"color: {tc.get('text_primary')}; "
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        emoji_lbl.setWordWrap(True)
        # Vertical alignment — wrapped emoji+name should hug the
        # top so the blurb below sits flush, not centered.
        emoji_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(emoji_lbl)

        if starter.blurb:
            blurb_lbl = QLabel(starter.blurb)
            blurb_lbl.setWordWrap(True)
            blurb_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            blurb_lbl.setStyleSheet(
                f"color: {tc.get('text_secondary')}; "
                f"font-size: {tc.FONT_XS}px; "
                "background: transparent; border: none;"
            )
            layout.addWidget(blurb_lbl)
        # Push everything to the top of the tile so all three tiles
        # in a row line up at the same baseline regardless of how
        # tall their individual content is.
        layout.addStretch(1)

        tile.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"border: 1px solid {tc.get('border_subtle')}; "
            f"border-radius: 6px; text-align: left; padding: 0; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; "
            f"border-color: {tc.get('accent_primary')}; }}"
        )
        tile.clicked.connect(lambda _checked=False, s=starter: self._load_starter_inline(s))
        return tile

    def _load_starter_inline(self, starter) -> None:
        """Load a starter without going through the change dialog.

        The change dialog asks for a target directory; for inline
        tile clicks we use the same default the dialog seeds with
        (the open project root, falling back to ``$HOME``) so a
        tile click is one tap. Filename collisions fall back to
        opening the picker so the user can pick a different
        location instead of quietly failing.
        """
        target_dir = self._project_root if self._project_root is not None else Path.home()
        try:
            entry = copy_starter(starter, target_dir)
        except FileExistsError:
            # Defer to the picker so the user can pick a different
            # destination instead of clobbering existing work.
            self._append_status(
                f"A '{starter.suggested_project_name}' folder already exists "
                "at the default location — opening the picker so you can "
                "choose where to put it.",
                kind="hint",
            )
            self._open_change_dialog(initial_tab=0)
            return
        except Exception as exc:
            self._append_status(
                f"Couldn't copy '{starter.name}': {exc}",
                kind="fail",
            )
            return
        language = language_for_file(entry) or starter.language
        project = DetectedProject(entry, entry.parent, language)
        self._active_starter_slug = starter.slug
        self._load_project(project, announce=True)
        self._append_status(f"Loaded '{starter.name}' into {entry.parent}.", kind="ok")
        self._announce_next_step()

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
        # Click is dispatched through a single handler that picks
        # between "start upload" and "cancel in-flight upload"
        # based on ``self._busy``. Keeping the dispatch in one
        # place means the button can't get into a state where its
        # label and its click handler disagree.
        self._upload_button.clicked.connect(self._on_upload_button_clicked)
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
        # Chain the requirements in the placeholder so a brand-new
        # user knows the order *before* they hunt around for the
        # missing piece. The button label below already shows the
        # current blocker; the placeholder shows the full path so
        # the user can plan ahead.
        self._status_view.setPlaceholderText(
            "Pick a project → plug in your board → press Upload.\n"
            "Progress and messages will show up here. ✨"
        )
        card.add_widget(self._status_view)

        # Ask-AI button — always visible from first show. Earlier
        # the button was hidden until ``_on_step_update`` revealed
        # it on failure, but a successful upload that misbehaves
        # on the board (LED doesn't blink, sensor reads zero, etc.)
        # is exactly when an AI handoff is most useful — and there
        # was no path to it. Label flips between "Ask AI for help"
        # (pre-run / success) and "Explain this error" (after a
        # failure) via ``_refresh_ai_button``.
        self._ai_help_button = QPushButton("💬  Ask AI for help")
        self._ai_help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_help_button.setMinimumHeight(38)
        self._ai_help_button.clicked.connect(self._on_ask_ai)
        self._ai_help_button.setStyleSheet(self._primary_button_qss())
        card.add_widget(self._ai_help_button)
        return card

    def _refresh_ai_button(self) -> None:
        """Pick the right label based on whether a fail was recent."""
        has_error = bool(self._service.last_error_detail)
        if has_error:
            self._ai_help_button.setText("💬  Explain this error")
        else:
            self._ai_help_button.setText("💬  Ask AI for help")

    # Step 4 — serial monitor --------------------------------------

    def _build_step4_monitor(self) -> QWidget:
        """The "See output" card — read-only serial stream.

        Layout: a control row (Connect/Disconnect, baud dropdown,
        Clear) above a monospace output area. Auto-connect happens
        after a successful upload via ``_after_success`` — most
        users want to see output the instant their code starts
        running, so the iteration loop becomes
        Upload → output streams → edit → Upload again with no
        extra clicks.
        """
        card = _StepCard(4, "See output")

        # Subtle helper line above the controls so a first-time
        # user understands what this card is for.
        helper = QLabel(
            "Read what your board is printing — anything from "
            "<code>Serial.println</code> (Arduino) or <code>print()</code> "
            "(MicroPython) shows up here."
        )
        helper.setWordWrap(True)
        helper.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; "
            "background: transparent;"
        )
        card.add_widget(helper)

        # Control row — connect button + baud dropdown + clear.
        control_row = QHBoxLayout()
        control_row.setSpacing(8)

        self._serial_connect_btn = QPushButton("▶  Start monitor")
        self._serial_connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._serial_connect_btn.setStyleSheet(self._primary_button_qss())
        self._serial_connect_btn.clicked.connect(self._on_serial_toggle)
        control_row.addWidget(self._serial_connect_btn)

        baud_label = QLabel("Speed:")
        baud_label.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; background: transparent;"
        )
        control_row.addWidget(baud_label)

        self._baud_combo = QComboBox()
        for baud in COMMON_BAUDS:
            self._baud_combo.addItem(f"{baud}", baud)
        # Default index 0 is the most common modern default
        # (115200) — see ``COMMON_BAUDS``'s ordering.
        self._baud_combo.setCurrentIndex(0)
        self._baud_combo.setStyleSheet(
            f"QComboBox {{ background: {tc.get('bg_input')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 4px; padding: 4px 8px; "
            f"font-size: {tc.FONT_SM}px; }}"
        )
        control_row.addWidget(self._baud_combo)

        clear_btn = QPushButton("Clear")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(self._secondary_button_qss())
        clear_btn.clicked.connect(self._on_serial_clear)
        control_row.addWidget(clear_btn)
        control_row.addStretch(1)
        card.add_layout(control_row)

        # Output area — monospace, read-only, auto-scroll to the
        # bottom on append. ``QPlainTextEdit`` (not ``QTextEdit``)
        # because plain text views handle 100k+ lines without the
        # rich-text overhead that would make a chatty board lag.
        self._serial_view = QPlainTextEdit()
        self._serial_view.setReadOnly(True)
        self._serial_view.setMinimumHeight(160)
        self._serial_view.setMaximumBlockCount(5000)  # cap memory
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._serial_view.setFont(mono)
        self._serial_view.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_terminal')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 6px; padding: 8px; "
            f"font-size: {tc.FONT_BASE}px; }}"
        )
        self._serial_view.setPlaceholderText(
            "Press Start monitor after uploading code to see what "
            "your board prints. We'll auto-start after a successful "
            "upload."
        )
        card.add_widget(self._serial_view)

        return card

    # Toolchain footer ---------------------------------------------

    def _build_toolchain_footer(self) -> QWidget:
        """Always-visible chip row showing what's installed.

        Three chips — one per tool the panel can use. Each is a
        QLabel styled as a pill. Green when present, red when
        missing. The label text is the tool name + ✓/✗; the
        tooltip carries the install hint so a hover gives an
        actionable "run this to fix it" line without needing
        another popup.

        ``_refresh_toolchain_chips`` is called from this method
        (initial paint), from ``_kick_detect`` (so re-detection
        also refreshes status — no separate timer needed), and
        whenever a board is detected/lost.
        """
        wrap = QFrame()
        wrap.setObjectName("toolchainFooter")
        wrap.setStyleSheet(
            f"#toolchainFooter {{ background: transparent; "
            f"border-top: 1px solid {tc.get('border_subtle')}; "
            f"padding: 8px 0 0 0; }}"
        )
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(8)

        intro = QLabel("Toolchains:")
        intro.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        row.addWidget(intro)

        # Build a chip per tool. ``_toolchain_chips`` lets the
        # refresh method find them again without scanning children.
        self._toolchain_chips: dict[str, QLabel] = {}
        for key, label in (
            ("arduino-cli", "arduino-cli"),
            ("mpremote", "mpremote"),
            ("pyserial", "pyserial"),
        ):
            chip = QLabel(f"… {label}")
            chip.setStyleSheet(self._chip_qss(present=False))
            self._toolchain_chips[key] = chip
            row.addWidget(chip)

        row.addStretch(1)
        # Initial paint with whatever the service reports right now.
        self._refresh_toolchain_chips()
        return wrap

    def _chip_qss(self, *, present: bool) -> str:
        """QSS for the chip pill — green for present, red for missing."""
        bg = tc.get("accent_success") if present else tc.get("accent_danger")
        return (
            f"QLabel {{ background: {bg}; "
            f"color: #fff; border-radius: 9px; "
            f"padding: 2px 10px; font-size: {tc.FONT_XS}px; "
            f"font-weight: 600; }}"
        )

    def _refresh_toolchain_chips(self) -> None:
        """Paint each chip according to the current toolchain state.

        Cheap — ``detect_toolchains`` is just ``shutil.which`` plus
        an ``import serial`` probe. Safe to call on every detection
        poll, which is exactly what we do.
        """
        if not hasattr(self, "_toolchain_chips"):
            return
        tcs = self._service.detect_toolchains()
        states = {
            "arduino-cli": (tcs.arduino_cli is not None, "Install Arduino CLI"),
            "mpremote": (tcs.mpremote is not None, "pip install --user mpremote"),
            "pyserial": (tcs.pyserial_ok, "pip install pyserial"),
        }
        for key, chip in self._toolchain_chips.items():
            present, install_hint = states[key]
            mark = "✓" if present else "✗"
            chip.setText(f"{mark} {key}")
            chip.setStyleSheet(self._chip_qss(present=present))
            chip.setToolTip(
                f"{key} is installed and ready."
                if present
                else f"{key} not found. Fix: {install_hint}"
            )

    # Advanced panel ------------------------------------------------

    def _build_advanced_panel(self) -> QWidget:
        wrap = QFrame()
        # Closing literals on plain (non-f) string lines must use a
        # single ``}`` — ``}}`` in a plain string is two literal
        # characters, which leaves a stray ``}`` mid-stylesheet
        # that Qt reports as "Could not parse stylesheet". Same
        # subtlety as the ``_StepCard`` ctor and the wipe button.
        # All three rule closers below switched to f-strings (or
        # single-``}`` plain strings) so the ``}}`` shorthand
        # stays consistent across the whole multi-line literal.
        wrap.setStyleSheet(
            f"QFrame {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 6px; }} "
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

        # ── Erase user code (destructive — Advanced only) ──
        #
        # Lives down in Advanced (not on the main wizard) precisely
        # because it's destructive: a kid clicking around shouldn't
        # be one tap away from wiping their work. The confirmation
        # dialog spells out exactly what's about to disappear.
        # Three language paths:
        #
        #   - MicroPython: ``mpremote rm :main.py`` + soft-reset
        #   - CircuitPython: delete code.py from the CIRCUITPY drive
        #   - Arduino C++: flash a minimal empty sketch (closest
        #     analog to "blank slate" since flash is one binary)
        #
        # Firmware (the interpreter / bootloader) is never touched
        # — every wipe is recoverable by uploading a new project.
        v.addSpacing(8)
        wipe_section = QLabel("Reset board")
        wipe_section.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_SM}px; "
            "font-weight: 600; background: transparent; padding-top: 4px;"
        )
        v.addWidget(wipe_section)
        wipe_blurb = QLabel(
            "Remove your code from the board — useful when a buggy "
            "program hangs, or you want to start fresh. Firmware stays "
            "intact; you can upload again any time."
        )
        wipe_blurb.setWordWrap(True)
        wipe_blurb.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_XS}px; background: transparent;"
        )
        v.addWidget(wipe_blurb)

        self._wipe_button = QPushButton("🗑  Erase user code")
        self._wipe_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wipe_button.setMinimumHeight(34)
        # NB: the closer for the ``:hover`` rule is a *plain*
        # string, so it must use a single ``}`` — using ``}}`` in
        # a non-f-string passes ``}}`` through verbatim and leaves
        # a stray ``}`` mid-stylesheet, which Qt reports as
        # "Could not parse stylesheet of object QPushButton". The
        # ``_StepCard`` ctor up at line 134 has the same comment
        # for the same reason — easy mistake to make when most
        # lines around it are f-strings.
        self._wipe_button.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('accent_danger')}; "
            f"border: 1px solid {tc.get('accent_danger')}; "
            f"border-radius: 6px; padding: 4px 12px; "
            f"font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_danger')}; "
            "color: #fff; }"
            f"QPushButton:disabled {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_muted')}; "
            f"border-color: {tc.get('border_secondary')}; }}"
        )
        self._wipe_button.clicked.connect(self._on_wipe_clicked)
        v.addWidget(self._wipe_button)
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
            self._active_starter_slug = result.starter.slug
            self._load_project(project, announce=True)
            self._append_status(f"Loaded '{result.starter.name}' into {entry.parent}.", kind="ok")
            self._announce_next_step()
            return

        if result.blank_name is not None and result.blank_language is not None:
            # Blank scaffold isn't a starter — clear the slug so a
            # later upload doesn't show the previous starter's hint.
            self._active_starter_slug = None
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
            # Existing-project loads aren't tied to a starter slug.
            self._active_starter_slug = None
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
        # Disambiguation hint — ``detect_in`` deterministically picks
        # ``.ino`` over ``code.py`` / ``main.py`` when both exist in
        # the same folder, but to a Python user that silent choice
        # looks like a bug. Surface it on the status feed so the
        # user knows which file's about to be flashed and how to
        # switch.
        self._maybe_warn_ambiguous_language(project)

    def _maybe_warn_ambiguous_language(self, project: DetectedProject) -> None:
        """Tell the user when more than one entry-file was found.

        ``detect_in`` picks ``.ino`` first, then ``code.py`` for
        CircuitPython, then ``main.py`` for MicroPython — silently.
        When the user opens a folder containing more than one of
        those, we say so explicitly and point at the override path.
        """
        folder = project.project_dir
        candidates: list[str] = []
        if next(folder.glob("*.ino"), None):
            candidates.append("*.ino (Arduino)")
        if (folder / "code.py").is_file():
            candidates.append("code.py (CircuitPython)")
        if (folder / "main.py").is_file():
            candidates.append("main.py (MicroPython)")
        if len(candidates) <= 1:
            return
        active = project.entry_file.name
        self._append_status(
            f"This folder has both {', '.join(candidates)}. Using <b>{active}</b>. "
            "Use Change project… to switch.",
            kind="hint",
        )

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
        # Refresh the toolchain footer on every detection tick so a
        # newly-installed tool flips its chip from red to green
        # without requiring a panel restart. ``detect_toolchains``
        # is just ``shutil.which`` + an import probe — same cost
        # as the empty-state branch in ``_on_boards_detected``.
        self._refresh_toolchain_chips()
        # Same cadence for CircuitPython drive auto-discovery —
        # cheap (a few ``Path.is_dir`` checks) and means the user
        # gets "Drive: CIRCUITPY ✓" without needing to dig through
        # Advanced after plugging the board in.
        self._maybe_autodetect_cp_drive()
        self._detecting = True
        try:
            asyncio.ensure_future(self._run_detect())
        except RuntimeError:
            self._detecting = False

    def _maybe_autodetect_cp_drive(self) -> None:
        """Scan common mount roots for a CircuitPython drive.

        Only runs when:
          - the active project is CircuitPython (no point scanning
            for a drive when the user is uploading C++ / MicroPython)
          - no drive is currently set (don't override a user's
            explicit override from Advanced)

        ``ArduinoService.find_circuitpython_drive`` does a few
        ``Path.is_dir`` checks against /run/media/$USER, /media/$USER,
        /Volumes — milliseconds per call.
        """
        if self._project is None:
            return
        if self._language is not Language.CIRCUITPYTHON:
            return
        if self._cp_drive is not None:
            return
        # Pull the label from the catalog when we know the board,
        # otherwise default to ``CIRCUITPY`` which is what every
        # off-the-shelf Adafruit board uses.
        label = "CIRCUITPY"
        if self._board is not None and self._board.cp_drive_label:
            label = self._board.cp_drive_label
        found = self._service.find_circuitpython_drive(label)
        if found is None:
            return
        self._cp_drive = found
        self._append_status(
            f"Found your CircuitPython drive at <code>{found}</code> — you can press Upload now.",
            kind="ok",
        )
        self._refresh_upload_button()

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
            #
            # Copy is intentionally written so the user understands
            # detection is *automatic* (we re-poll every 2.5s) —
            # the previous wording made it sound like the only way
            # to detect was the manual button, and patient users
            # would sit waiting after plugging in.
            tc_state = self._service.detect_toolchains()
            if not tc_state.pyserial_ok and not tc_state.can_cpp:
                self._detection_label.setText(
                    "🔌  Plug in your board with the USB cable — "
                    "we'll spot it automatically.<br>"
                    f"<span style='color:{tc.get('text_muted')}; "
                    f"font-size:{tc.FONT_SM}px;'>"
                    "Tip: install pyserial and arduino-cli to detect "
                    "more boards (<code>pip install pyserial</code>).</span>"
                )
            else:
                self._detection_label.setText(
                    "🔌  Plug in your board with the USB cable — "
                    "we'll spot it automatically.<br>"
                    f"<span style='color:{tc.get('text_muted')}; "
                    f"font-size:{tc.FONT_SM}px;'>"
                    "Or press <b>Look again</b> to re-scan now.</span>"
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
            self._maybe_warn_dialout_group()

        # Re-publish the snapshot for the chat panel. Done only when
        # the board state actually changed — re-publishing on every
        # 2.5 s poll would burn cycles for no benefit.
        if (self._board is not None) != had_board_before:
            self._publish_panel_state()

    def _maybe_warn_dialout_group(self) -> None:
        """One-shot, kid-friendly warning about dialout group membership.

        Without group membership the kernel rejects the ``open()``
        on ``/dev/ttyUSB0`` and arduino-cli upload fails with a
        permission-denied error a 10-year-old can't act on. We
        surface a friendly hint with a one-click *Copy command*
        button and grown-up framing — "ask a grown-up to run this
        in a terminal" — instead of expecting the kid to know
        what ``sudo`` is.

        Latched per-panel so the message appears once when the
        first board is plugged in, not every poll.
        """
        if getattr(self, "_dialout_warned", False):
            return
        if self._service.user_in_dialout_group():
            return
        self._dialout_warned = True
        import getpass

        try:
            user = getpass.getuser()
        except Exception:
            user = "$USER"
        cmd = f"sudo usermod -aG dialout {user}"

        self._append_status(
            "Your computer needs one-time permission to talk to your board. "
            "Ask a grown-up to run this in a terminal, then log out and back in:",
            kind="hint",
        )
        self._append_status(f"<code>{cmd}</code>", kind="hint")

        # ``QApplication.clipboard`` works without any prior import
        # — qApp is always available once QApplication exists. Done
        # via QTimer so the slot runs on the GUI thread regardless
        # of which thread fired the dialout warning. (Today it's
        # always the GUI thread, but the indirection costs nothing.)
        from PyQt6.QtWidgets import QApplication

        def copy_to_clipboard() -> None:
            QApplication.clipboard().setText(cmd)
            self._append_status(
                "Command copied to clipboard. Paste it into a terminal.",
                kind="ok",
            )

        # Inline button below the hint — one tap, kid-actionable.
        copy_btn = QPushButton("📋  Copy the command")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setMinimumHeight(32)
        copy_btn.setStyleSheet(self._secondary_button_qss())
        copy_btn.clicked.connect(copy_to_clipboard)
        # Insert into the upload card under the AI help button so it
        # sits in the same visual region as other one-shot actions.
        # ``parent()`` walk avoids hard-coding the card index.
        ai_btn_parent = self._ai_help_button.parent()
        if ai_btn_parent is not None:
            layout = ai_btn_parent.layout()
            if layout is not None:
                layout.addWidget(copy_btn)

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
        # Cancel-while-busy: when an upload is in flight the
        # button stays enabled but flips to ``Cancel`` and routes
        # to ``_on_cancel_clicked``. The previous behaviour
        # (disable + label "Working…") left the user with no way
        # to abort a wrong-board upload, forcing them to wait out
        # the 180 s timeout.
        self._upload_button.setEnabled(self._busy or (ready and not self._busy))
        self._upload_button.setStyleSheet(
            self._cancel_button_qss() if self._busy else self._upload_button_qss(ready)
        )
        self._upload_button.setToolTip(
            "Cancel the upload and release the board." if self._busy else ("" if ready else why)
        )
        if self._busy:
            label = "✕  Cancel upload"
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

        # Toolchain gate — surface a missing CLI as the upload
        # blocker rather than letting the user click Upload, watch
        # compile or upload spin, then see "Arduino CLI isn't
        # installed yet" in the status feed. We've already told
        # them via the first-launch dependency dialog; the button
        # label keeps the same actionable message visible.
        language = self._language
        if language is not None:
            tc_state = self._service.detect_toolchains()
            if language is Language.CPP and not tc_state.can_cpp:
                return False, "Install Arduino CLI first"
            if language is Language.MICROPYTHON and not tc_state.can_micropython:
                return False, "Install mpremote first"

        if self._board is None:
            return False, "Plug in your board first."
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

    def _cancel_button_qss(self) -> str:
        """Red, attention-grabbing style for the in-flight Cancel state."""
        return (
            "QPushButton { "
            f"background: {tc.get('accent_danger')}; "
            "color: #fff; border: none; border-radius: 10px; "
            f"font-size: {tc.FONT_XL}px; font-weight: 700; "
            "}"
            "QPushButton:hover { background: #d43f3f; }"
            "QPushButton:pressed { padding-top: 2px; }"
        )

    def _on_upload_button_clicked(self) -> None:
        """Dispatch button click — start upload or cancel one in flight."""
        if self._busy:
            self._on_cancel_clicked()
            return
        self._on_upload_clicked()

    # ── Erase user code ─────────────────────────────────────────────

    def _on_wipe_clicked(self) -> None:
        """Confirm with the user, then dispatch to the right wipe path.

        The confirmation dialog spells out exactly what's going to
        disappear (the file path + language) so a kid can't blow
        away their work by misclicking. ``Yes`` runs the wipe,
        anything else cancels.
        """
        if self._busy:
            self._append_status(
                "Wait for the current operation to finish first.",
                kind="hint",
            )
            return

        # Figure out *what* we'd be erasing so the dialog can quote
        # it back. None of the wipe paths needs a project loaded —
        # the user might want to wipe a buggy upload from yesterday
        # without re-loading its project — but they all need a
        # board / drive.
        language = self._language
        target_desc, target_extra = self._describe_wipe_target()
        if target_desc is None:
            self._append_status(target_extra or "Plug in a board first.", kind="fail")
            return

        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("Erase user code?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"This will erase <b>{target_desc}</b>.")
        box.setInformativeText(
            f"{target_extra}\n\n"
            "Firmware on your board stays intact — you can upload again "
            "any time. But your current code will be gone."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        # Style the destructive button red so a thumbed-through
        # confirm doesn't quietly land on Yes by muscle memory.
        yes_btn = box.button(QMessageBox.StandardButton.Yes)
        if yes_btn is not None:
            yes_btn.setText("Erase")
            yes_btn.setStyleSheet(
                f"QPushButton {{ background: {tc.get('accent_danger')}; "
                "color: #fff; border: none; border-radius: 4px; "
                "padding: 4px 14px; font-weight: 600; }}"
            )

        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        # Release the serial port — the wipe shells out to the same
        # tools that need exclusive port access, exactly like the
        # upload path. Auto-reconnect doesn't fire here (there'd be
        # nothing to read from a blank board) but the user can
        # press Start monitor manually if they want to verify.
        if self._serial_monitor.is_connected:
            self._serial_monitor.disconnect()

        self._busy = True
        self._service.last_error_detail = ""
        self._refresh_ai_button()
        self._refresh_upload_button()
        self._append_status("— Erasing —")
        try:
            asyncio.ensure_future(self._run_wipe(language))
        except RuntimeError:
            self._busy = False
            self._refresh_upload_button()
            self._append_status(
                "Couldn't start the wipe — no event loop. Try restarting the app.",
                kind="fail",
            )

    def _describe_wipe_target(self) -> tuple[str | None, str]:
        """Return (file/target description, longer explanation).

        Returns ``(None, reason)`` when the wipe can't be performed
        — e.g. no board plugged in. The first half is a short
        bold-able label for the dialog title; the second is a
        sentence's worth of context for the body.
        """
        language = self._language
        if language is Language.CIRCUITPYTHON:
            if self._cp_drive is None:
                return None, (
                    "No CIRCUITPY drive detected. Plug in your board and "
                    "wait for it to mount, then try again."
                )
            return (
                f"code.py on {self._cp_drive.name}",
                f"Your program at {self._cp_drive / 'code.py'} will be deleted.",
            )
        if not self._port:
            return None, "No serial port detected. Plug in your board and try again."
        if language is Language.MICROPYTHON:
            return (
                "main.py on your board",
                f"Your MicroPython program (main.py) at port {self._port} will be deleted.",
            )
        # Default / C++ branch — also covers the case where we
        # have a board but no project loaded yet, since wiping is
        # a board-level operation.
        if self._board is None:
            return None, "No board detected. Plug in your board and try again."
        return (
            "your sketch",
            f"An empty sketch will be flashed to {self._board.display_name} on "
            f"{self._port}, replacing whatever's currently running.",
        )

    async def _run_wipe(self, language) -> None:
        """Drive the right wipe coroutine for the active language."""
        try:
            if language is Language.CIRCUITPYTHON and self._cp_drive is not None:
                async for u in self._service.wipe_circuitpython(self._cp_drive):
                    self._step_received.emit(u)
            elif language is Language.MICROPYTHON and self._port:
                async for u in self._service.wipe_micropython(self._port):
                    self._step_received.emit(u)
            else:
                # Default to the C++ path. Needs both a port and a
                # board for the empty-sketch flash. If the user is
                # in the "no project loaded yet" state, we still
                # have access to the detected board, but we need
                # *some* sketch dir for arduino-cli's staging
                # call. Use a temp dir under the project root or
                # the user's home as a sane default.
                if self._board is None or not self._port:
                    self._step_received.emit(
                        StepUpdate("No board or port — can't wipe.", kind="fail")
                    )
                    return
                base = (
                    self._project.project_dir.parent if self._project is not None else Path.home()
                )
                async for u in self._service.wipe_cpp(base, self._board, self._port):
                    self._step_received.emit(u)
        except Exception:
            logger.exception("arduino: wipe failed")
            self._step_received.emit(StepUpdate("Something went wrong. Try again?", kind="fail"))
        finally:
            self._busy = False
            QTimer.singleShot(0, self._refresh_upload_button)

    def _on_cancel_clicked(self) -> None:
        """Terminate the in-flight compile/upload subprocess.

        ``cancel_current_upload`` SIGTERMs the running subprocess.
        ``_run`` translates that into a ``rc=-1`` "cancelled"
        signal, the corresponding async generator stops yielding,
        and ``_run_upload``'s finally block resets ``_busy = False``
        so ``_refresh_upload_button`` flips the button back to
        the green Upload state.
        """
        cancelled = self._service.cancel_current_upload()
        if cancelled:
            self._append_status("Cancelling — waiting for the process to exit…", kind="hint")
        else:
            # Race: between the button flip and the click,
            # ``_run`` finished naturally. Nothing to do.
            self._append_status("Already finished — nothing to cancel.", kind="hint")

    def _on_upload_clicked(self) -> None:
        if self._busy:
            return
        ready, why = self._upload_readiness()
        if not ready:
            self._append_status(why, kind="fail")
            return
        # Release the serial port before re-flashing — arduino-cli
        # needs exclusive access to the port to drive the bootloader,
        # and a second Serial open would either fail or compete for
        # bytes mid-upload. The monitor auto-reconnects in
        # ``_after_success`` once the new code is on the board.
        if self._serial_monitor.is_connected:
            self._serial_monitor.disconnect()
        self._busy = True
        # Reset AI button label — clearing ``last_error_detail`` and
        # then refreshing flips "Explain this error" back to the
        # neutral "Ask AI for help" so a returning user with a fresh
        # session doesn't see a stale error label.
        self._service.last_error_detail = ""
        self._refresh_ai_button()
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
            # Empty ``last_error_detail`` after a non-exception run
            # means every yield from the service was a success or
            # in-progress; flip into the celebrate state.
            succeeded = not self._service.last_error_detail
            QTimer.singleShot(0, self._refresh_upload_button)
            if succeeded:
                # Queued *after* the refresh so this runs second and
                # wins the "what does the button say" race — if we
                # didn't, the refresh would set "Upload to Arduino"
                # and we'd never get the celebrated label.
                QTimer.singleShot(0, self._after_success)

    def _after_success(self) -> None:
        """Acknowledge a successful upload and invite a re-upload.

        Three cues — together they take the panel from "and then
        it just goes silent" to "your code is on the board, here's
        what to try next":

        1. Re-style the upload button label to ``Upload again ↻``
           so a returning user knows the button is still alive and
           targets the same project + board.
        2. Append a short hint line to the status feed pointing at
           the iteration loop — most beginners' next move is to
           tweak a delay or a pin and re-flash.
        3. Auto-start the serial monitor on the same port the
           upload used. Closes the *biggest* feedback gap in the
           wizard — the user uploaded code that prints something,
           the panel was about to go quiet, now they see output
           appearing in step 4 within a second.
        """
        self._upload_button.setText("Upload again ↻")
        self._append_status(
            "Edit your code and press Upload again to flash the new version.",
            kind="hint",
        )

        # Starter-specific post-upload coaching. Tells a kid what
        # to *look for* now that their code is on the board ("Look
        # at your board's tiny LED — it should blink once a
        # second"). Only fires for projects scaffolded from a
        # starter — blanks and opened-existing projects don't have
        # a meaningful canned hint.
        if self._active_starter_slug:
            try:
                from polyglot_ai.core.arduino.starters import list_starters

                starter = next(
                    (s for s in list_starters() if s.slug == self._active_starter_slug),
                    None,
                )
                if starter is not None and starter.post_upload_hint:
                    self._append_status(starter.post_upload_hint, kind="hint")
            except Exception:
                # Failure to load starter metadata is purely
                # cosmetic — silently skip the hint.
                logger.debug("arduino: post_upload_hint lookup failed", exc_info=True)

        # Auto-connect the monitor only when:
        #   - we have a serial port (CircuitPython upload-via-USB-
        #     drive doesn't, and there's nothing for ``pyserial``
        #     to talk to in that case)
        #   - the user isn't already monitoring (a second connect
        #     call would be a no-op anyway, but skipping the call
        #     also skips the "Connected" status line)
        if self._port and not self._serial_monitor.is_connected:
            baud = int(self._baud_combo.currentData() or 115200)
            # Tiny grace period so arduino-cli's port reset has
            # finished before we try to open it ourselves. Without
            # this, the open often races against the bootloader's
            # final port-relinquish and fails with "device busy."
            QTimer.singleShot(800, lambda: self._serial_monitor.connect_to(self._port, baud))

    # ── Serial monitor handlers ────────────────────────────────────

    def _on_serial_toggle(self) -> None:
        """Connect or disconnect the monitor based on current state."""
        if self._serial_monitor.is_connected:
            self._serial_monitor.disconnect()
            return
        if not self._port:
            self._append_status(
                "Plug in a board first — the monitor needs a serial port to read.",
                kind="fail",
            )
            return
        baud = int(self._baud_combo.currentData() or 115200)
        self._serial_monitor.connect_to(self._port, baud)

    def _on_serial_connected(self, port: str, baud: int) -> None:
        self._serial_connect_btn.setText("⏹  Stop monitor")
        # Keep the same primary-button QSS — just the label flips.
        # Append a marker line so the user can tell where one
        # session ends and the next begins.
        self._serial_view.appendPlainText(f"── connected to {port} @ {baud} ──")

    def _on_serial_disconnected(self) -> None:
        self._serial_connect_btn.setText("▶  Start monitor")
        self._serial_view.appendPlainText("── disconnected ──")

    def _on_serial_error(self, message: str) -> None:
        self._append_status(f"Serial monitor: {message}", kind="fail")

    def _on_serial_line(self, line: str) -> None:
        """Append one line of board output to the monitor view."""
        self._serial_view.appendPlainText(line)

    def _on_serial_clear(self) -> None:
        self._serial_view.clear()

    # ── Status & AI handoff ────────────────────────────────────────

    def _on_step_update(self, update: StepUpdate) -> None:
        self._append_status(update.message, kind=update.kind)
        if update.kind == "fail" and self._service.last_error_detail:
            # Button stays visible the whole session now (see
            # _build_step3_upload). Just flip the label so the user
            # sees an action verb that matches the new state.
            self._refresh_ai_button()
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
