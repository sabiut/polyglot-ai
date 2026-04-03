"""Terminal emulator wrapping pyte for ANSI parsing."""

from __future__ import annotations

from dataclasses import dataclass

import pyte


@dataclass
class Cell:
    char: str
    fg: str
    bg: str
    bold: bool
    italics: bool
    underscore: bool
    reverse: bool


class TerminalEmulator:
    """Wraps pyte Screen/Stream for ANSI terminal emulation."""

    # Standard ANSI color names to hex
    COLOR_MAP = {
        "default": None,
        "black": "#000000",
        "red": "#cd3131",
        "green": "#0dbc79",
        "brown": "#e5e510",
        "blue": "#2472c8",
        "magenta": "#bc3fbc",
        "cyan": "#11a8cd",
        "white": "#e5e5e5",
        "brightblack": "#666666",
        "brightred": "#f14c4c",
        "brightgreen": "#23d18b",
        "brightyellow": "#f5f543",
        "brightblue": "#3b8eea",
        "brightmagenta": "#d670d6",
        "brightcyan": "#29b8db",
        "brightwhite": "#ffffff",
    }

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.Stream(self._screen)
        self._dirty = True

    def feed(self, data: bytes) -> None:
        """Feed raw bytes from the PTY into the terminal emulator."""
        text = data.decode("utf-8", errors="replace")
        self._stream.feed(text)
        self._dirty = True

    def get_lines(self) -> list[list[Cell]]:
        """Get the current screen state as a grid of Cells."""
        lines = []
        for row in range(self._screen.lines):
            line = []
            for col in range(self._screen.columns):
                char_data = self._screen.buffer[row][col]
                cell = Cell(
                    char=char_data.data or " ",
                    fg=self._resolve_color(char_data.fg, "default"),
                    bg=self._resolve_color(char_data.bg, "default"),
                    bold=char_data.bold,
                    italics=char_data.italics,
                    underscore=char_data.underscore,
                    reverse=char_data.reverse,
                )
                line.append(cell)
            lines.append(line)
        self._dirty = False
        return lines

    def get_cursor(self) -> tuple[int, int]:
        """Get cursor position (row, col)."""
        return self._screen.cursor.y, self._screen.cursor.x

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal screen."""
        self._screen.resize(rows, cols)
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def rows(self) -> int:
        return self._screen.lines

    @property
    def cols(self) -> int:
        return self._screen.columns

    def _resolve_color(self, color: str, fallback: str) -> str | None:
        """Convert a pyte color to a hex string."""
        if not color or color == "default":
            return None

        # Check named colors
        if color in self.COLOR_MAP:
            return self.COLOR_MAP[color]

        # Check if it's a 256-color index
        try:
            idx = int(color)
            return self._color_256(idx)
        except (ValueError, TypeError):
            pass

        # Check if it's already a hex color
        if len(color) == 6:
            try:
                int(color, 16)
                return f"#{color}"
            except ValueError:
                pass

        return None

    def _color_256(self, idx: int) -> str:
        """Convert a 256-color index to hex."""
        if idx < 16:
            # Standard colors
            standard = [
                "#000000",
                "#cd3131",
                "#0dbc79",
                "#e5e510",
                "#2472c8",
                "#bc3fbc",
                "#11a8cd",
                "#e5e5e5",
                "#666666",
                "#f14c4c",
                "#23d18b",
                "#f5f543",
                "#3b8eea",
                "#d670d6",
                "#29b8db",
                "#ffffff",
            ]
            return standard[idx]
        elif idx < 232:
            # 216-color cube
            idx -= 16
            r = (idx // 36) * 51
            g = ((idx % 36) // 6) * 51
            b = (idx % 6) * 51
            return f"#{r:02x}{g:02x}{b:02x}"
        else:
            # Grayscale
            v = 8 + (idx - 232) * 10
            return f"#{v:02x}{v:02x}{v:02x}"
