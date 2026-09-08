"""Terminal panel — custom-painted terminal emulator using pyte + pty."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QTimer, Qt, pyqtSignal
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
from polyglot_ai.core.terminal.pty_process import PTY_AVAILABLE, PtyProcess
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

        # Cursor blink timer. Parented to ``self`` so Qt deletes the
        # underlying QTimer when this widget is destroyed, even if we
        # somehow miss the closeEvent path.
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_cursor)
        self._blink_timer.start(500)

        # Repaint timer — 30fps is plenty for terminal output and halves
        # idle CPU compared to the previous 60fps poll. A future pass
        # could move to a signal-driven model; for now this is the
        # zero-risk improvement.
        self._paint_timer = QTimer(self)
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
        elif (
            self._bell_flash_until_ms and int(_time.monotonic() * 1000) >= self._bell_flash_until_ms
        ):
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
            painter.fillRect(bar_x, thumb_top, _SCROLLBAR_WIDTH, thumb_h, QColor(255, 255, 255, 80))

        painter.end()

    # ── Drag-and-drop paths ─────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        # Only accept if the PTY is actually alive — accepting on a
        # dead shell would make the cursor briefly say "drop OK" then
        # silently swallow the input.
        if not self._pty or not self._pty.is_running:
            event.ignore()
            return
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
        # Restart the timer (not just check isActive) so the blink
        # phase resets to a fresh 500ms cycle from "now". Otherwise
        # an unfocus that happened mid-cycle leaves the cursor with
        # an asymmetric on/off rhythm on refocus.
        self._blink_timer.start(500)
        self._cursor_visible = True
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._blink_timer.stop()
        self.update()
        super().focusOutEvent(event)

    def closeEvent(self, event) -> None:
        """Stop timers explicitly on close.

        Parenting the timers to ``self`` already covers the normal
        teardown path, but stopping them up front avoids one final
        ``timeout`` after the widget is on its way out.
        """
        self._blink_timer.stop()
        self._paint_timer.stop()
        super().closeEvent(event)

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
            # Single line selection. Strip trailing space-fill from the
            # row so a triple-click that selects a whole 80-column row
            # doesn't paste 60 trailing spaces of nothing — pyte fills
            # unset cells with " " so a naive join yields the full
            # column width every time.
            line = self._lines[r1] if r1 < len(self._lines) else []
            chars = "".join(line[c].char for c in range(c1, min(c2, len(line))))
            return chars.rstrip() if c2 >= len(line) else chars
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
                # Triple click — select the whole current row.
                # Reset the counter immediately so a 4th rapid click
                # starts a fresh selection at that point instead of
                # re-firing the line-select path again.
                self._consecutive_clicks = 0
                self._last_click_time_ms = 0
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

        # "Restart" only matters when the shell has exited (or is hung);
        # when running normally the user can just type ``exit`` and the
        # terminal handles re-spawn elsewhere. Show it always for
        # discoverability — no harm in restarting a healthy shell.
        menu.addSeparator()
        restart_action = QAction("Restart Terminal", self)
        restart_action.triggered.connect(self._restart_terminal_from_menu)
        menu.addAction(restart_action)

        menu.exec(event.globalPos())

    def _main_window(self):
        """The application main window, even when this widget is popped out.

        ``self.window()`` is the pop-out ``_TerminalWindow`` while the
        terminal lives in its own window; that window is parented to the
        main window, so climb parents until something exposes the
        panels we need.
        """
        w = self.window()
        for _ in range(5):
            if w is None or hasattr(w, "chat_panel"):
                return w
            parent = w.parent()
            w = parent.window() if parent is not None else None
        return w

    def _restart_terminal_from_menu(self) -> None:
        """Walk up to the TerminalPanel and ask it to restart the shell.

        Falls back silently if the lookup fails so the menu action
        never errors visibly — the worst case is "menu does nothing".
        """
        window = self._main_window()
        panel = getattr(window, "terminal_panel", None)
        if panel is None or not hasattr(panel, "restart_terminal"):
            logger.debug("Restart Terminal: panel unavailable")
            return
        panel.restart_terminal()

    def _send_selection_to_ai(self) -> None:
        """Send the current selection to the chat panel as context.

        Looks up the main window to find the chat panel. Fails silently
        (logs) if the chat panel isn't reachable — the terminal shouldn't
        crash on a missing panel.
        """
        text = self._get_selected_text()
        if not text:
            return
        window = self._main_window()
        chat = getattr(window, "chat_panel", None)
        if chat is None or not hasattr(chat, "prefill_input"):
            logger.debug("Send to AI: chat panel unavailable")
            return
        if window is not self.window():
            # Sent from the popped-out terminal: bring the main window
            # forward so the prefilled chat is actually visible.
            window.raise_()
            window.activateWindow()
        framed = (
            "Help me understand this terminal output. Explain what it "
            "means, whether there's an error, and what I should do next:\n\n"
            f"```\n{text}\n```"
        )
        chat.prefill_input(framed)
        # Chat lives in the right-side tab widget — prefilling alone is
        # invisible unless that tab is active and the strip is shown, so
        # raise it (same pattern as test_panel's "Fix with AI").
        right_tabs = getattr(window, "_right_tabs", None)
        if right_tabs is None:
            return
        try:
            idx = right_tabs.indexOf(chat)
            if idx >= 0:
                right_tabs.setCurrentIndex(idx)
            if not right_tabs.isVisible():
                # Go through the View-menu toggle when we can so the
                # menu's checked state stays in sync with reality.
                toggle = getattr(window, "_action_toggle_chat", None)
                if toggle is not None:
                    toggle.setChecked(True)
                else:
                    right_tabs.setVisible(True)
        except Exception:
            logger.debug("Send to AI: couldn't raise chat tab", exc_info=True)

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
        """Intercept events that Qt would otherwise handle before us.

        Two collisions to deal with:

        * **Tab** — Qt uses Tab for focus traversal. We need it as a key
          to forward to the shell.
        * **Ctrl+C / Ctrl+D / Ctrl+Z / Ctrl+L** — the main window's
          edit menu registers ``QKeySequence.StandardKey.Copy`` (= Ctrl+C)
          as a window-wide ``QAction``. By default Qt routes those
          shortcuts to the action *before* keyPressEvent fires, so the
          terminal would never get to translate Ctrl+C into the SIGINT
          byte ``\\x03``. ``ShortcutOverride`` is Qt's hook for "this
          widget wants this combo" — accepting it tells Qt to skip the
          action and deliver the keypress to us instead.
        """
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self.keyPressEvent(event)
            return True

        if event.type() == QEvent.Type.ShortcutOverride:
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            # Plain Ctrl+ for terminal control characters that the menu
            # bar would otherwise eat. Ctrl+Shift+C/V are also claimed
            # so they reach our copy/paste handlers (the menu binds
            # plain Ctrl+C to Copy, but a stray Shift modifier still
            # falls into the same dispatch path on some Qt versions).
            if (
                ctrl
                and not shift
                and event.key()
                in (
                    Qt.Key.Key_C,
                    Qt.Key.Key_D,
                    Qt.Key.Key_Z,
                    Qt.Key.Key_L,
                )
            ):
                event.accept()
                return True
            if ctrl and shift and event.key() in (Qt.Key.Key_C, Qt.Key.Key_V):
                event.accept()
                return True

        return super().event(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Scroll through terminal history with mouse wheel.

        Selection is cleared on scroll because the selection model is
        keyed by visible-row index — leaving it in place would make
        the highlight follow whatever content scrolls into those row
        positions, which is misleading. Users can re-select after
        scrolling if they still want to copy.
        """
        if not self._emulator:
            return
        delta = event.angleDelta().y()
        if delta > 0:
            self._emulator.scroll_up(3)
        elif delta < 0:
            self._emulator.scroll_down(3)
        else:
            return
        self._clear_selection()
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
        # No update_screen() here — the shell's echo of the pasted
        # text will arrive via EVT_TERMINAL_OUTPUT and the paint timer
        # will pick it up. Calling update_screen() now would just
        # repaint the pre-paste state.


class _TerminalWindow(QWidget):
    """Top-level home for a popped-out TerminalWidget.

    Owned by the panel; closing the window hands the widget back
    rather than destroying it.
    """

    def __init__(self, panel: TerminalPanel) -> None:
        # Parent to the main window (with the Window flag) so it closes
        # with the app and stacks sensibly, but is not embedded.
        super().__init__(panel.window(), Qt.WindowType.Window)
        self._panel = panel
        self._docking = False
        self.setWindowTitle("Terminal — Polyglot AI")
        self.resize(900, 560)
        from polyglot_ai.ui import theme_colors as tc

        self.setStyleSheet(f"background-color: {tc.get('bg_terminal')};")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def take(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def release(self, widget: QWidget) -> None:
        self._docking = True
        self._layout.removeWidget(widget)
        widget.setParent(None)
        self.close()

    def closeEvent(self, event) -> None:
        if not self._docking:
            self._docking = True
            self._panel.dock_back()
            return
        super().closeEvent(event)


class TerminalPanel(QWidget):
    """Embedded terminal emulator panel."""

    # PtyProcess invokes its callbacks from a background reader thread.
    # We pass these signals' ``.emit`` as the callbacks: Qt's queued
    # auto-connection delivers them on the GUI thread, which is the
    # only thread allowed to touch the emulator and EventBus.
    _pty_output = pyqtSignal(bytes)
    _pty_exited = pyqtSignal()

    #: Header buttons the main window acts on (it owns the splitter
    #: and the show/hide action).
    expand_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._event_bus: EventBus | None = None
        self._pty: PtyProcess | None = None
        self._emulator: TerminalEmulator | None = None

        # GUI-thread slots for the cross-thread PTY signals. These
        # MUST be connected here: 0.18.6 shipped with them displaced
        # into dock_back(), so a freshly started shell's output never
        # reached the emulator and the terminal looked dead.
        self._pty_output.connect(self._on_output)
        self._pty_exited.connect(self._on_exited)

        # Let the AI's ``terminal_read`` tool see the buffer. A lazy
        # reader (not a pushed snapshot) because terminal output churns
        # on every PTY write; the bound method survives shell restarts
        # since it always reads whatever emulator is current.
        from polyglot_ai.core import panel_state

        panel_state.set_terminal_reader(self._read_buffer_for_ai)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

        layout.addWidget(self._build_header())

        self._terminal_widget = TerminalWidget()
        layout.addWidget(self._terminal_widget)

        # Shown in place of the terminal while it lives in its own window.
        self._popped_placeholder = self._build_popped_placeholder()
        self._popped_placeholder.hide()
        layout.addWidget(self._popped_placeholder)
        self._window: QWidget | None = None
        self._expanded = False

    # ── Header: title + pop-out / expand / close ────────────────────

    def _build_header(self) -> QWidget:
        from PyQt6.QtWidgets import QHBoxLayout, QLabel

        from polyglot_ai.ui import theme_colors as tc
        from polyglot_ai.ui.panels import shared_icons
        from polyglot_ai.ui.widgets.icon_button import make_icon_button

        header = QWidget()
        header.setFixedHeight(30)
        header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(2)
        title = QLabel("TERMINAL")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            "letter-spacing: 0.5px; background: transparent; border: none;"
        )
        row.addWidget(title)
        row.addStretch()

        self._popout_btn = make_icon_button(
            shared_icons.draw_popout_icon(), "Open terminal in a separate window"
        )
        self._popout_btn.clicked.connect(self.pop_out)
        row.addWidget(self._popout_btn)

        self._expand_btn = make_icon_button(
            shared_icons.draw_expand_icon(), "Expand terminal to fill the column"
        )
        self._expand_btn.clicked.connect(self.expand_requested.emit)
        row.addWidget(self._expand_btn)

        close_btn = make_icon_button(shared_icons.draw_close_icon(), "Hide terminal (Ctrl+`)")
        close_btn.clicked.connect(self.close_requested.emit)
        row.addWidget(close_btn)
        return header

    def _build_popped_placeholder(self) -> QWidget:
        from PyQt6.QtWidgets import QLabel, QPushButton

        from polyglot_ai.ui import theme_colors as tc

        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg = QLabel("The terminal is open in its own window.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_MD}px;")
        v.addWidget(msg)
        back = QPushButton("Bring it back here")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFixedWidth(160)
        back.clicked.connect(self.dock_back)
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignCenter)
        return box

    def set_expanded(self, expanded: bool) -> None:
        """Reflect the main window's expand state in the header button."""
        from polyglot_ai.ui.panels import shared_icons

        self._expanded = expanded
        if expanded:
            self._expand_btn.setIcon(shared_icons.draw_collapse_icon())
            self._expand_btn.setToolTip("Restore terminal to its normal height")
        else:
            self._expand_btn.setIcon(shared_icons.draw_expand_icon())
            self._expand_btn.setToolTip("Expand terminal to fill the column")

    @property
    def is_popped_out(self) -> bool:
        return self._window is not None

    def pop_out(self) -> None:
        """Move the live terminal into its own top-level window.

        The same TerminalWidget (and therefore the same shell, buffer
        and scrollback) is reparented — nothing restarts. Closing the
        window docks it back here.
        """
        if self._window is not None:
            self._window.raise_()
            self._window.activateWindow()
            return
        win = _TerminalWindow(self)
        self._layout.removeWidget(self._terminal_widget)
        win.take(self._terminal_widget)
        self._window = win
        self._popped_placeholder.show()
        self._popout_btn.setEnabled(False)
        self._expand_btn.setEnabled(False)
        win.show()
        self._terminal_widget.setFocus()

    def dock_back(self) -> None:
        """Return the terminal from its window to this panel."""
        win = self._window
        if win is None:
            return
        self._window = None
        win.release(self._terminal_widget)
        self._layout.insertWidget(1, self._terminal_widget)
        self._terminal_widget.show()
        self._popped_placeholder.hide()
        self._popout_btn.setEnabled(True)
        self._expand_btn.setEnabled(True)
        win.deleteLater()
        self._terminal_widget.setFocus()

    def _read_buffer_for_ai(self) -> str | None:
        """Return the full terminal buffer, or None when no shell is running."""
        if self._emulator is None:
            return None
        return self._emulator.get_all_text()

    def set_font_size(self, size) -> None:
        """Apply Settings → Terminal → Font size (Ctrl+= / Ctrl+- still zoom from here)."""
        try:
            self._terminal_widget._set_font_size(int(size))
        except (TypeError, ValueError):
            pass

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
        if not PTY_AVAILABLE:
            # Windows: no POSIX pty yet. Show why instead of a blank box.
            self._terminal_widget.set_emulator(self._emulator)
            self._on_output(
                b"The built-in terminal isn't available on this platform yet "
                b"(it needs a POSIX pty).\r\nUse your system terminal for now; "
                b"everything else in Polyglot AI works.\r\n"
            )
            logger.info("Terminal disabled: no pty on this platform")
            return
        # Pass bound signal emitters as thread-safe callbacks. The signal
        # is connected to GUI-thread slots above, so cross-thread
        # delivery is queued automatically by Qt.
        self._pty = PtyProcess(
            on_output=self._pty_output.emit,
            on_exited=self._pty_exited.emit,
        )

        self._terminal_widget.set_emulator(self._emulator)
        self._terminal_widget.set_pty(self._pty)

        self._pty.start(shell=shell, cwd=cwd, rows=rows, cols=cols)
        logger.info("Terminal started: %dx%d", cols, rows)

    def _on_output(self, data: bytes) -> None:
        if self._emulator:
            self._emulator.feed(data)
        # Re-broadcast on the bus on the GUI thread for any other
        # subscribers (currently none, but kept to preserve the public
        # event contract documented in constants.py).
        if self._event_bus is not None:
            self._event_bus.emit(EVT_TERMINAL_OUTPUT, data=data)

    def _on_exited(self) -> None:
        """Print a visible exit notice into the emulator and freeze input.

        Without this the user typed into a dead PTY in silence — the
        shell had exited but the terminal still painted, blinked, and
        accepted keystrokes that went nowhere. Feeding a coloured line
        into the emulator surfaces the exit and "Right-click → Restart"
        becomes the obvious next step.
        """
        logger.info("Terminal process exited")
        if self._emulator:
            self._emulator.feed(
                "\r\n\x1b[33m[process exited — right-click to restart]\x1b[0m\r\n".encode()
            )
        if self._event_bus is not None:
            self._event_bus.emit(EVT_TERMINAL_EXITED)

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
