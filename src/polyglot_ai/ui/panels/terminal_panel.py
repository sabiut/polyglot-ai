"""Terminal panel — custom-painted terminal emulator using pyte + pty."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPainter
from PyQt6.QtWidgets import QVBoxLayout, QWidget

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

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self._font)

        # Clear background
        painter.fillRect(self.rect(), QColor(DEFAULT_BG))

        for row_idx, line in enumerate(self._lines):
            for col_idx, cell in enumerate(line):
                x = col_idx * self._char_width
                y = row_idx * self._char_height

                # Background
                bg = cell.bg if cell.bg else DEFAULT_BG
                fg = cell.fg if cell.fg else DEFAULT_FG

                if cell.reverse:
                    bg, fg = fg, bg

                if bg != DEFAULT_BG:
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._pty or not self._pty.is_running:
            return

        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+C
        if modifiers & Qt.KeyboardModifier.ControlModifier:
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
