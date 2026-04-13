"""Terminal panel — custom-painted terminal emulator using pyte + pty."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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
}

DEFAULT_FG = "#d4d4d4"
DEFAULT_BG = "#0e0e0e"


class TerminalWidget(QWidget):
    """Custom widget that paints a character grid for terminal output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setStyleSheet(f"background-color: {DEFAULT_BG};")

        self._font = QFont("Monospace", 11)
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

        # Cursor blink timer
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._toggle_cursor)
        self._blink_timer.start(500)

        # Repaint timer (60fps)
        self._paint_timer = QTimer()
        self._paint_timer.timeout.connect(self._check_dirty)
        self._paint_timer.start(16)

    def set_emulator(self, emulator: TerminalEmulator) -> None:
        self._emulator = emulator

    def set_pty(self, pty_proc: PtyProcess) -> None:
        self._pty = pty_proc

    def update_screen(self) -> None:
        if self._emulator:
            self._lines = self._emulator.get_lines()
            self._cursor_row, self._cursor_col = self._emulator.get_cursor()
            self.update()

    def _check_dirty(self) -> None:
        if self._emulator and self._emulator.dirty:
            self.update_screen()

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.update()

    def get_terminal_size(self) -> tuple[int, int]:
        """Calculate rows/cols from widget size."""
        if self._char_width == 0 or self._char_height == 0:
            return 24, 80
        cols = max(1, self.width() // self._char_width)
        rows = max(1, self.height() // self._char_height)
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

        # Clear background
        painter.fillRect(self.rect(), QColor(DEFAULT_BG))

        sel_bg = QColor(51, 153, 255, 100)  # Blue selection highlight

        for row_idx, line in enumerate(self._lines):
            for col_idx, cell in enumerate(line):
                x = col_idx * self._char_width
                y = row_idx * self._char_height

                selected = self._is_cell_selected(row_idx, col_idx)

                # Background
                bg = cell.bg if cell.bg else DEFAULT_BG
                fg = cell.fg if cell.fg else DEFAULT_FG

                if cell.reverse:
                    bg, fg = fg, bg

                if selected:
                    painter.fillRect(x, y, self._char_width, self._char_height, sel_bg)
                elif bg != DEFAULT_BG:
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

        # Draw cursor
        if self._cursor_visible:
            cx = self._cursor_col * self._char_width
            cy = self._cursor_row * self._char_height
            painter.fillRect(
                cx,
                cy,
                self._char_width,
                self._char_height,
                QColor(200, 200, 200, 128),
            )

        painter.end()

    # ── Mouse selection ────────────────────────────────────────────

    def _pos_to_cell(self, pos: QPoint) -> tuple[int, int]:
        """Convert pixel position to (row, col) in the character grid."""
        col = max(0, pos.x() // self._char_width) if self._char_width else 0
        row = max(0, pos.y() // self._char_height) if self._char_height else 0
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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_start = self._pos_to_cell(event.pos())
            self._sel_end = self._sel_start
            self._selecting = True
            self.update()
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
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-click selects a word."""
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
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Right-click context menu with Copy and Paste."""
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

        select_all_action = QAction("Select All", self)
        select_all_action.triggered.connect(self._select_all)
        menu.addAction(select_all_action)

        menu.exec(event.globalPos())

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
        if not self._pty or not self._pty.is_running:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            if text:
                # Scroll to bottom before pasting
                if self._emulator and self._emulator.is_scrolled_back:
                    self._emulator.scroll_to_bottom()
                # Bracket paste mode: wrap in escape sequences so the shell
                # knows this is pasted text (prevents execution of newlines)
                self._pty.write(b"\x1b[200~")
                self._pty.write(text.encode("utf-8"))
                self._pty.write(b"\x1b[201~")
                # Force screen update after paste
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
