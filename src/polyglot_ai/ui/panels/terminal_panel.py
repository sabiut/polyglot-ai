"""Terminal panel — custom-painted terminal emulator using pyte + pty."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QTimer, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PyQt6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from polyglot_ai.constants import EVT_TERMINAL_EXITED, EVT_TERMINAL_OUTPUT
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.terminal.emulator import TerminalEmulator
from polyglot_ai.core.terminal.pty_process import PtyProcess
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

# Font zoom bounds. Below 7pt glyphs become unreadable; above 24pt the
# grid gets unwieldy on typical screens.
_MIN_FONT_SIZE = 7
_MAX_FONT_SIZE = 24
_DEFAULT_FONT_SIZE = 11

# Interior padding around the character grid so glyphs don't kiss the
# widget edge. 6px matches the default gnome-terminal / kitty padding
# closely enough that output doesn't feel cramped.
_TERM_PADDING_X = 6
_TERM_PADDING_Y = 4

# Width of the scrollback indicator gutter on the right edge. It's
# drawn over the padding so it doesn't steal columns from the grid.
_SCROLLBAR_WIDTH = 4

# URL detection for Ctrl+click. Kept deliberately conservative so we
# don't match accidental matches inside log output.
_URL_RE = re.compile(r"(https?|file)://[^\s<>\"'`]+")

# Key mappings for terminal escape sequences
KEY_MAP = {
    Qt.Key.Key_Return: b"\r",
    Qt.Key.Key_Backspace: b"\x7f",
    Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Escape: b"\x1b",
    Qt.Key.Key_Up: b"\x1b[A",
    Qt.Key.Key_Down: b"\x1b[B",
    Qt.Key.Key_Right: b"\x1b[C",
    Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Home: b"\x1b[H",
    Qt.Key.Key_End: b"\x1b[F",
    Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~",
    Qt.Key.Key_Insert: b"\x1b[2~",
    Qt.Key.Key_F1: b"\x1bOP",
    Qt.Key.Key_F2: b"\x1bOQ",
    Qt.Key.Key_F3: b"\x1bOR",
    Qt.Key.Key_F4: b"\x1bOS",
    # F5–F12 use CSI numeric-sequence encoding (different from F1–F4's
    # SS3 encoding). Values match xterm/VT220 conventions so TUIs like
    # mc, htop, nano pick them up out of the box.
    Qt.Key.Key_F5: b"\x1b[15~",
    Qt.Key.Key_F6: b"\x1b[17~",
    Qt.Key.Key_F7: b"\x1b[18~",
    Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~",
    Qt.Key.Key_F10: b"\x1b[21~",
    Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}

def _default_fg() -> str:
    return tc.get("text_primary")


def _default_bg() -> str:
    return tc.get("bg_terminal")


class TerminalWidget(QWidget):
    """Custom widget that paints a character grid for terminal output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setStyleSheet(f"background-color: {_default_bg()};")
        # Accept file drops so users can drag a path from the file
        # explorer into the terminal — pastes the absolute path so a
        # "cd " or "cat " prefix becomes a working command.
        self.setAcceptDrops(True)

        self._font_size = _DEFAULT_FONT_SIZE
        self._font = QFont("Monospace", self._font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        fm = QFontMetrics(self._font)
        self._char_width = fm.horizontalAdvance("M")
        self._char_height = fm.height()

        self._emulator: TerminalEmulator | None = None
        self._pty: PtyProcess | None = None
        self._lines: list = []
        self._cursor_row = 0
        self._cursor_col = 0
        self._cursor_visible = True

        # Mouse text selection state
        self._selecting = False
        self._sel_start: tuple[int, int] | None = None  # (row, col)
        self._sel_end: tuple[int, int] | None = None  # (row, col)

        # Triple-click tracking — Qt emits doubleClick twice for a
        # triple; we time consecutive clicks ourselves. 400ms is
        # generous enough to catch users on trackpads.
        self._last_click_time_ms = 0
        self._consecutive_clicks = 0

        # Cursor blink timer
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._toggle_cursor)
        self._blink_timer.start(500)

        # Repaint timer — 30fps is plenty for terminal output and halves
        # idle CPU compared to the previous 60fps poll. A future pass
        # could move to a signal-driven model; for now this is the
        # zero-risk improvement.
        self._paint_timer = QTimer()
        self._paint_timer.timeout.connect(self._check_dirty)
        self._paint_timer.start(33)

        # Visual-bell state — tracks the last emulator bell count so
        # we only flash on new rings. Flashes the background briefly
        # when the count advances.
        self._last_bell_count = 0
        self._bell_flash_until_ms = 0

    def set_emulator(self, emulator: TerminalEmulator) -> None:
        self._emulator = emulator

    def set_pty(self, pty_proc: PtyProcess) -> None:
        self._pty = pty_proc

    # ── Font zoom ───────────────────────────────────────────────────

    def _set_font_size(self, size: int) -> None:
        """Change the monospace font size and propagate to the emulator.

        The PTY cares about rows/cols, not pixels — changing the font
        changes the cell size, so we recalculate rows/cols from the
        current widget dimensions and resize both emulator and PTY to
        match. Without this, programs running inside the shell would
        see the old dimensions until the next real resize event.
        """
        size = max(_MIN_FONT_SIZE, min(_MAX_FONT_SIZE, size))
        if size == self._font_size:
            return
        self._font_size = size
        self._font = QFont("Monospace", self._font_size)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        fm = QFontMetrics(self._font)
        self._char_width = fm.horizontalAdvance("M")
        self._char_height = fm.height()
        rows, cols = self.get_terminal_size()
        if self._emulator:
            self._emulator.resize(rows, cols)
        if self._pty and self._pty.is_running:
            self._pty.resize(rows, cols)
        self.update()

    def _zoom_in(self) -> None:
        self._set_font_size(self._font_size + 1)

    def _zoom_out(self) -> None:
        self._set_font_size(self._font_size - 1)

    def _zoom_reset(self) -> None:
        self._set_font_size(_DEFAULT_FONT_SIZE)

    def update_screen(self) -> None:
        if self._emulator:
            self._lines = self._emulator.get_lines()
            self._cursor_row, self._cursor_col = self._emulator.get_cursor()
            self.update()

    def _check_dirty(self) -> None:
        if not self._emulator:
            return
        # Trigger visual bell if the emulator's bell counter advanced
        # since we last looked. Flash for 120ms — short enough to be a
        # confirmation, long enough to be noticeable.
        import time as _time

        bc = self._emulator.bell_count
        if bc != self._last_bell_count:
            self._last_bell_count = bc
            self._bell_flash_until_ms = int(_time.monotonic() * 1000) + 120
            self.update()
        elif self._bell_flash_until_ms and int(_time.monotonic() * 1000) >= self._bell_flash_until_ms:
            self._bell_flash_until_ms = 0
            self.update()

        if self._emulator.dirty:
            self.update_screen()

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.update()

    def get_terminal_size(self) -> tuple[int, int]:
        """Calculate rows/cols from widget size, minus the interior padding."""
        if self._char_width == 0 or self._char_height == 0:
            return 24, 80
        usable_w = max(0, self.width() - 2 * _TERM_PADDING_X - _SCROLLBAR_WIDTH)
        usable_h = max(0, self.height() - 2 * _TERM_PADDING_Y)
        cols = max(1, usable_w // self._char_width)
        rows = max(1, usable_h // self._char_height)
        return rows, cols

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rows, cols = self.get_terminal_size()
        if self._emulator:
            self._emulator.resize(rows, cols)
        if self._pty and self._pty.is_running:
            self._pty.resize(rows, cols)

    def _is_cell_selected(self, row: int, col: int) -> bool:
        """Check if a cell is within the current selection."""
        sel = self._sel_ordered()
        if not sel:
            return False
        (r1, c1), (r2, c2) = sel
        if r1 == r2:
            return row == r1 and c1 <= col < c2
        if row == r1:
            return col >= c1
        if row == r2:
            return col < c2
        return r1 < row < r2

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self._font)

        default_bg = _default_bg()
        default_fg = _default_fg()

        # Clear background. When a visual bell is active, use a
        # lightened shade so the whole widget flashes briefly.
        if self._bell_flash_until_ms:
            painter.fillRect(self.rect(), QColor(80, 30, 30))
        else:
            painter.fillRect(self.rect(), QColor(default_bg))

        sel_bg = QColor(51, 153, 255, 100)  # Blue selection highlight

        ox, oy = _TERM_PADDING_X, _TERM_PADDING_Y
        for row_idx, line in enumerate(self._lines):
            for col_idx, cell in enumerate(line):
                x = ox + col_idx * self._char_width
                y = oy + row_idx * self._char_height

                selected = self._is_cell_selected(row_idx, col_idx)

                # Background
                bg = cell.bg if cell.bg else default_bg
                fg = cell.fg if cell.fg else default_fg

                if cell.reverse:
                    bg, fg = fg, bg

                if selected:
                    painter.fillRect(x, y, self._char_width, self._char_height, sel_bg)
                elif bg != default_bg:
                    painter.fillRect(
                        x,
                        y,
                        self._char_width,
                        self._char_height,
                        QColor(bg),
                    )

                # Foreground
                font = self._font
                if cell.bold:
                    font = QFont(font)
                    font.setBold(True)
                if cell.italics:
                    font = QFont(font) if font is self._font else font
                    font.setItalic(True)
                painter.setFont(font)
                painter.setPen(QColor(fg))
                painter.drawText(x, y + self._char_height - 3, cell.char)

                # Underscore
                if cell.underscore:
                    painter.drawLine(
                        x,
                        y + self._char_height - 1,
                        x + self._char_width,
                        y + self._char_height - 1,
                    )

        # Draw cursor. When the widget has focus, blink per the timer.
        # When unfocused, draw a hollow outline so the user can still
        # see where the cursor is without the distraction of blinking
        # in a widget they're not typing into.
        cx = ox + self._cursor_col * self._char_width
        cy = oy + self._cursor_row * self._char_height
        if self.hasFocus():
            if self._cursor_visible:
                painter.fillRect(
                    cx,
                    cy,
                    self._char_width,
                    self._char_height,
                    QColor(200, 200, 200, 128),
                )
        else:
            painter.setPen(QColor(200, 200, 200, 128))
            painter.drawRect(cx, cy, self._char_width - 1, self._char_height - 1)

        # Scrollback indicator — a slim right-side gutter that fills
        # proportionally based on how far we've scrolled up. Only shown
        # when the history buffer has accumulated something to scroll
        # back into, so a fresh terminal doesn't show a pointless bar.
        if self._emulator and self._emulator.history_length:
            bar_x = self.width() - _SCROLLBAR_WIDTH
            bar_h = self.height()
            painter.fillRect(bar_x, 0, _SCROLLBAR_WIDTH, bar_h, QColor(255, 255, 255, 18))
            total = self._emulator.history_length + self._emulator.rows
            # offset_from_top represents the first-visible line's
            # position within the full history+screen span. When we're
            # at the latest output (scroll_offset=0), offset = history
            # length; when fully scrolled back, offset = 0.
            scroll_off = getattr(self._emulator, "_scroll_offset", 0)
            offset_from_top = max(0, self._emulator.history_length - scroll_off)
            thumb_top = int(bar_h * offset_from_top / max(1, total))
            thumb_h = max(16, int(bar_h * self._emulator.rows / max(1, total)))
            painter.fillRect(
                bar_x, thumb_top, _SCROLLBAR_WIDTH, thumb_h, QColor(255, 255, 255, 80)
            )

        painter.end()

    # ── Drag-and-drop paths ─────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Drop → paste paths (or text) into the terminal.

        File URLs are shell-quoted so filenames with spaces don't
        break the command being assembled. Multiple files are joined
        by spaces so ``cat file1 file2`` works after one drag.
        """
        import shlex

        mime = event.mimeData()
        paths: list[str] = []
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
        if paths:
            self._paste_text(" ".join(shlex.quote(p) for p in paths) + " ")
            event.acceptProposedAction()
            return
        if mime.hasText():
            self._paste_text(mime.text())
            event.acceptProposedAction()

    # Cursor-blink timer is paused on focus-out and resumed on focus-in.
    def focusInEvent(self, event) -> None:
        if not self._blink_timer.isActive():
            self._blink_timer.start(500)
        self._cursor_visible = True
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._blink_timer.stop()
        self.update()
        super().focusOutEvent(event)

    # ── Mouse selection ────────────────────────────────────────────

    def _pos_to_cell(self, pos: QPoint) -> tuple[int, int]:
        """Convert pixel position to (row, col) in the character grid.

        Accounts for the interior padding so a click in the padding
        gutter resolves to the nearest edge cell, not a negative index.
        """
        px = max(0, pos.x() - _TERM_PADDING_X)
        py = max(0, pos.y() - _TERM_PADDING_Y)
        col = px // self._char_width if self._char_width else 0
        row = py // self._char_height if self._char_height else 0
        # Clamp to grid bounds
        max_row = len(self._lines) - 1 if self._lines else 0
        row = min(row, max_row)
        if self._lines and row < len(self._lines):
            max_col = len(self._lines[row])
            col = min(col, max_col)
        return row, col

    def _sel_ordered(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """Return selection start/end in order (top-left first)."""
        if self._sel_start is None or self._sel_end is None:
            return None
        a, b = self._sel_start, self._sel_end
        if (a[0], a[1]) > (b[0], b[1]):
            a, b = b, a
        return a, b

    def _has_selection(self) -> bool:
        """Return True if there is a non-empty selection."""
        sel = self._sel_ordered()
        if not sel:
            return False
        return sel[0] != sel[1]

    def _get_selected_text(self) -> str:
        """Extract the text within the current selection."""
        sel = self._sel_ordered()
        if not sel or not self._lines:
            return ""
        (r1, c1), (r2, c2) = sel
        if r1 == r2:
            # Single line selection
            line = self._lines[r1] if r1 < len(self._lines) else []
            chars = "".join(line[c].char for c in range(c1, min(c2, len(line))))
            return chars
        # Multi-line selection
        result = []
        for r in range(r1, r2 + 1):
            if r >= len(self._lines):
                break
            line = self._lines[r]
            if r == r1:
                row_text = "".join(cell.char for cell in line[c1:]).rstrip()
            elif r == r2:
                row_text = "".join(cell.char for cell in line[:c2])
            else:
                row_text = "".join(cell.char for cell in line).rstrip()
            result.append(row_text)
        return "\n".join(result)

    def _clear_selection(self) -> None:
        """Clear the current selection."""
        if self._sel_start is not None:
            self._sel_start = None
            self._sel_end = None
            self._selecting = False
            self.update()

    def _url_at(self, row: int, col: int) -> str | None:
        """Return the URL under (row, col) in the current visible lines, or None.

        Scans the row's character cells for any ``_URL_RE`` match and
        returns the first one whose span contains ``col``. Confined to
        the visible view — scrollback URLs aren't clickable today.
        """
        if not self._lines or row >= len(self._lines):
            return None
        line_text = "".join(c.char for c in self._lines[row])
        for m in _URL_RE.finditer(line_text):
            if m.start() <= col < m.end():
                return m.group(0)
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Ctrl+click a URL → open in the user's browser. Bypasses
            # the selection flow so the user doesn't end up with a
            # one-character selection after following a link.
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                row, col = self._pos_to_cell(event.pos())
                url = self._url_at(row, col)
                if url:
                    from PyQt6.QtCore import QUrl
                    from PyQt6.QtGui import QDesktopServices

                    QDesktopServices.openUrl(QUrl(url))
                    return

            # Track consecutive clicks for triple-click line select
            import time as _time

            now_ms = int(_time.monotonic() * 1000)
            if now_ms - self._last_click_time_ms < 400:
                self._consecutive_clicks += 1
            else:
                self._consecutive_clicks = 1
            self._last_click_time_ms = now_ms

            if self._consecutive_clicks >= 3:
                # Triple click — select the whole current row
                row, _ = self._pos_to_cell(event.pos())
                if self._lines and row < len(self._lines):
                    self._sel_start = (row, 0)
                    self._sel_end = (row, len(self._lines[row]))
                    self._selecting = False
                    self.update()
                    if self._has_selection():
                        self._copy_selection()
                return

            self._sel_start = self._pos_to_cell(event.pos())
            self._sel_end = self._sel_start
            self._selecting = True
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            # X11 primary-selection convention: middle-click pastes the
            # current X selection (not the Ctrl+C clipboard). Qt exposes
            # this via QClipboard.Selection mode where available; on
            # Wayland / other platforms it falls back to the regular
            # clipboard which is a reasonable degraded behaviour.
            self._paste_primary_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._selecting:
            self._sel_end = self._pos_to_cell(event.pos())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._sel_end = self._pos_to_cell(event.pos())
            self._selecting = False
            self.update()
            # Auto-copy on release — matches gnome-terminal / kitty /
            # alacritty behaviour. Users expect the clipboard to have
            # the selection without an explicit Ctrl+Shift+C.
            if self._has_selection():
                self._copy_selection()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-click selects a word; the triple-click case is handled
        in :meth:`mousePressEvent` via a short timer, not here — Qt
        emits doubleClick twice for a triple click, so we distinguish
        based on click count tracked separately.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            row, col = self._pos_to_cell(event.pos())
            if row < len(self._lines):
                line = self._lines[row]
                # Find word boundaries
                start = col
                end = col
                while start > 0 and start < len(line) and line[start - 1].char.strip():
                    start -= 1
                while end < len(line) and line[end].char.strip():
                    end += 1
                self._sel_start = (row, start)
                self._sel_end = (row, end)
                self._selecting = False
                self.update()
                if self._has_selection():
                    self._copy_selection()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Right-click context menu with Copy / Paste / Select All / Copy All."""
        menu = QMenu(self)
        copy_action = QAction("Copy", self)
        copy_action.setShortcut("Ctrl+Shift+C")
        copy_action.setEnabled(self._has_selection())
        copy_action.triggered.connect(self._copy_selection)
        menu.addAction(copy_action)

        paste_action = QAction("Paste", self)
        paste_action.setShortcut("Ctrl+Shift+V")
        paste_action.triggered.connect(self._paste_clipboard)
        menu.addAction(paste_action)

        menu.addSeparator()

        select_all_action = QAction("Select All (visible)", self)
        select_all_action.triggered.connect(self._select_all)
        menu.addAction(select_all_action)

        # Selection model can't represent history rows, so offer a
        # dedicated "copy everything including scrollback" action that
        # goes straight to the clipboard without any visible highlight.
        copy_buffer_action = QAction("Copy All (with Scrollback)", self)
        copy_buffer_action.triggered.connect(self._copy_all_with_scrollback)
        menu.addAction(copy_buffer_action)

        menu.addSeparator()

        send_to_ai_action = QAction("Send selection to AI...", self)
        send_to_ai_action.setEnabled(self._has_selection())
        send_to_ai_action.triggered.connect(self._send_selection_to_ai)
        menu.addAction(send_to_ai_action)

        menu.addSeparator()

        zoom_in_action = QAction("Zoom In", self)
        zoom_in_action.setShortcut("Ctrl+=")
        zoom_in_action.triggered.connect(self._zoom_in)
        menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom Out", self)
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(self._zoom_out)
        menu.addAction(zoom_out_action)

        zoom_reset_action = QAction("Reset Zoom", self)
        zoom_reset_action.setShortcut("Ctrl+0")
        zoom_reset_action.triggered.connect(self._zoom_reset)
        menu.addAction(zoom_reset_action)

        menu.exec(event.globalPos())

    def _send_selection_to_ai(self) -> None:
        """Send the current selection to the chat panel as context.

        Looks up the main window to find the chat panel. Fails silently
        (logs) if the chat panel isn't reachable — the terminal shouldn't
        crash on a missing panel.
        """
        text = self._get_selected_text()
        if not text:
            return
        window = self.window()
        chat = getattr(window, "chat_panel", None)
        if chat is None or not hasattr(chat, "prefill_input"):
            logger.debug("Send to AI: chat panel unavailable")
            return
        framed = (
            "Help me understand this terminal output. Explain what it "
            "means, whether there's an error, and what I should do next:\n\n"
            f"```\n{text}\n```"
        )
        chat.prefill_input(framed)

    def _copy_all_with_scrollback(self) -> None:
        """Copy the full buffer (scrollback + visible) to the clipboard.

        No selection highlight — the visible-cell selection model
        can't address history rows. If nothing is in the buffer yet,
        this is a no-op.
        """
        if not self._emulator:
            return
        text = self._emulator.get_all_text()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)

    def _select_all(self) -> None:
        """Select all visible terminal text."""
        if not self._lines:
            return
        self._sel_start = (0, 0)
        last_row = len(self._lines) - 1
        last_col = len(self._lines[last_row]) if self._lines else 0
        self._sel_end = (last_row, last_col)
        self.update()

    def event(self, event) -> bool:
        """Override event() to intercept Tab before Qt uses it for focus navigation."""
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Scroll through terminal history with mouse wheel."""
        if not self._emulator:
            return
        delta = event.angleDelta().y()
        if delta > 0:
            # Scroll up (into history)
            self._emulator.scroll_up(3)
        elif delta < 0:
            # Scroll down (toward current)
            self._emulator.scroll_down(3)
        self.update_screen()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._pty or not self._pty.is_running:
            return

        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        # Ctrl+Shift+C — copy selected text (terminal convention)
        if ctrl and shift and key == Qt.Key.Key_C:
            self._copy_selection()
            return

        # Ctrl+Shift+V — paste from clipboard (terminal convention)
        if ctrl and shift and key == Qt.Key.Key_V:
            self._paste_clipboard()
            return

        # Font zoom — Ctrl+=, Ctrl+-, Ctrl+0. Qt reports Shift+= as
        # Key_Plus on some layouts and Key_Equal on others, so accept
        # both. These must be checked BEFORE the generic Ctrl block
        # below so they don't fall through as control characters.
        if ctrl and not shift:
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self._zoom_in()
                return
            if key == Qt.Key.Key_Minus:
                self._zoom_out()
                return
            if key == Qt.Key.Key_0:
                self._zoom_reset()
                return

        # Shift+PageUp / Shift+PageDown scroll the scrollback view
        # without forwarding a key to the shell. The page-height jump
        # matches gnome-terminal's convention.
        if shift and self._emulator:
            if key == Qt.Key.Key_PageUp:
                self._emulator.scroll_up(self._emulator.rows)
                self.update()
                return
            if key == Qt.Key.Key_PageDown:
                self._emulator.scroll_down(self._emulator.rows)
                self.update()
                return

        # Clear selection on any other keypress
        self._clear_selection()

        # Any keypress (except copy) snaps back to current output
        if self._emulator and self._emulator.is_scrolled_back:
            self._emulator.scroll_to_bottom()

        # Ctrl+C/D/Z/L — terminal control characters
        if ctrl and not shift:
            if key == Qt.Key.Key_C:
                self._pty.write(b"\x03")
                return
            elif key == Qt.Key.Key_D:
                self._pty.write(b"\x04")
                return
            elif key == Qt.Key.Key_Z:
                self._pty.write(b"\x1a")
                return
            elif key == Qt.Key.Key_L:
                self._pty.write(b"\x0c")
                return

        # Special keys
        if key in KEY_MAP:
            self._pty.write(KEY_MAP[key])
            return

        # Regular text
        text = event.text()
        if text:
            self._pty.write(text.encode("utf-8"))

    def _copy_selection(self) -> None:
        """Copy selected text to clipboard, or entire screen if no selection."""
        if self._has_selection():
            text = self._get_selected_text()
        elif self._emulator:
            # Fallback: copy entire visible screen
            lines = self._emulator.get_lines()
            text_lines = []
            for line in lines:
                row_text = "".join(cell.char for cell in line).rstrip()
                text_lines.append(row_text)
            while text_lines and not text_lines[-1]:
                text_lines.pop()
            text = "\n".join(text_lines)
        else:
            return
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)

    def _paste_clipboard(self) -> None:
        """Paste clipboard content into the terminal."""
        self._paste_text(QApplication.clipboard().text() if QApplication.clipboard() else "")

    def _paste_primary_selection(self) -> None:
        """Paste the X11 primary selection (middle-click paste).

        On Wayland / other platforms the primary-selection mode may be
        empty; falling back to the regular clipboard matches what most
        terminals do so the user gesture still does something useful.
        """
        clip = QApplication.clipboard()
        if not clip:
            return
        try:
            text = clip.text(mode=clip.Mode.Selection)
        except (TypeError, AttributeError):
            text = ""
        if not text:
            text = clip.text()
        self._paste_text(text)

    def _paste_text(self, text: str) -> None:
        """Send ``text`` to the PTY wrapped in bracket-paste markers.

        The wrapping tells the shell this is pasted input, so a pasted
        newline won't fire ``Enter`` in shells that honour bracket-paste
        mode (bash since 4.4, zsh, fish).
        """
        if not text or not self._pty or not self._pty.is_running:
            return
        if self._emulator and self._emulator.is_scrolled_back:
            self._emulator.scroll_to_bottom()
        self._pty.write(b"\x1b[200~")
        self._pty.write(text.encode("utf-8"))
        self._pty.write(b"\x1b[201~")
        self.update_screen()


class TerminalPanel(QWidget):
    """Embedded terminal emulator panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._terminal_widget = TerminalWidget()
        layout.addWidget(self._terminal_widget)

        self._event_bus: EventBus | None = None
        self._pty: PtyProcess | None = None
        self._emulator: TerminalEmulator | None = None

    def start_terminal(
        self,
        event_bus: EventBus,
        shell: str = "/bin/bash",
        cwd: Path | None = None,
    ) -> None:
        """Initialize and start the terminal."""
        self._event_bus = event_bus

        rows, cols = self._terminal_widget.get_terminal_size()
        rows = max(rows, 24)
        cols = max(cols, 80)

        self._emulator = TerminalEmulator(rows, cols)
        self._pty = PtyProcess(event_bus)

        self._terminal_widget.set_emulator(self._emulator)
        self._terminal_widget.set_pty(self._pty)

        # Subscribe to terminal events (unsubscribe first to avoid duplicates on restart)
        event_bus.unsubscribe(EVT_TERMINAL_OUTPUT, self._on_output)
        event_bus.unsubscribe(EVT_TERMINAL_EXITED, self._on_exited)
        event_bus.subscribe(EVT_TERMINAL_OUTPUT, self._on_output)
        event_bus.subscribe(EVT_TERMINAL_EXITED, self._on_exited)

        self._pty.start(shell=shell, cwd=cwd, rows=rows, cols=cols)
        logger.info("Terminal started: %dx%d", cols, rows)

    def _on_output(self, data: bytes = b"", **kwargs) -> None:
        if self._emulator:
            self._emulator.feed(data)

    def _on_exited(self, **kwargs) -> None:
        logger.info("Terminal process exited")

    def stop_terminal(self) -> None:
        if self._pty:
            self._pty.terminate()

    def restart_terminal(self, shell: str = "/bin/bash", cwd: Path | None = None) -> None:
        self.stop_terminal()
        if self._event_bus:
            self.start_terminal(self._event_bus, shell, cwd)

    def cd_to(self, path: Path | str) -> None:
        """Send a ``cd <path>`` to the running shell without restarting.

        Preferred over ``restart_terminal`` when a project opens mid-
        session: preserves scrollback, running processes, and shell
        history. Uses shlex.quote so paths with spaces don't break
        the command.
        """
        if not self._pty or not self._pty.is_running:
            return
        import shlex

        cmd = f" cd {shlex.quote(str(path))}\n"
        # Leading space keeps the command out of shell history (HISTIGNORE
        # or HISTCONTROL=ignorespace catches it in bash/zsh) — users
        # don't need to see the auto-cd polluting their recall.
        self._pty.write(cmd.encode("utf-8"))
