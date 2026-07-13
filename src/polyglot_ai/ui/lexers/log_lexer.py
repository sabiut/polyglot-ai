"""Log file lexer — severity-based syntax highlighting for .log files."""

from __future__ import annotations

import re

from PyQt6.QtGui import QColor, QFont
from PyQt6.Qsci import QsciLexerCustom, QsciScintilla

from polyglot_ai.ui import theme_colors as tc


class LogLexer(QsciLexerCustom):
    """Highlights log lines by severity level.

    Styles:
        0 — Default (plain text)
        1 — ERROR / FATAL / CRITICAL (red)
        2 — WARN / WARNING (yellow)
        3 — INFO / NOTICE (default/cyan)
        4 — DEBUG / TRACE (dim)
        5 — Timestamp (grey)
    """

    # Patterns that identify severity at the start or within a log line
    _ERROR_RE = re.compile(r"\b(ERROR|FATAL|CRITICAL|EXCEPTION|FAIL(ED)?)\b", re.IGNORECASE)
    _WARN_RE = re.compile(r"\b(WARN(ING)?)\b", re.IGNORECASE)
    _INFO_RE = re.compile(r"\b(INFO|NOTICE)\b", re.IGNORECASE)
    _DEBUG_RE = re.compile(r"\b(DEBUG|TRACE)\b", re.IGNORECASE)

    def __init__(self, parent: QsciScintilla | None = None) -> None:
        super().__init__(parent)
        self._setup_styles()

    def _setup_styles(self) -> None:
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        paper = QColor(tc.get("bg_base"))

        # 0: Default
        self.setColor(QColor(tc.get("text_primary")), 0)
        self.setFont(mono, 0)
        self.setPaper(paper, 0)

        # 1: Error — red
        self.setColor(QColor(tc.get("severity_critical")), 1)
        self.setFont(mono, 1)
        self.setPaper(paper, 1)

        # 2: Warning — yellow
        self.setColor(QColor(tc.get("severity_high")), 2)
        self.setFont(mono, 2)
        self.setPaper(paper, 2)

        # 3: Info — cyan
        self.setColor(QColor(tc.get("severity_low")), 3)
        self.setFont(mono, 3)
        self.setPaper(paper, 3)

        # 4: Debug — dim grey
        self.setColor(QColor(tc.get("text_disabled")), 4)
        self.setFont(mono, 4)
        self.setPaper(paper, 4)

    def language(self) -> str:
        return "Log"

    def description(self, style: int) -> str:
        styles = {
            0: "Default",
            1: "Error",
            2: "Warning",
            3: "Info",
            4: "Debug",
        }
        return styles.get(style, "")

    def styleText(self, start: int, end: int) -> None:
        editor = self.editor()
        if editor is None:
            return

        text = editor.text()[start:end]
        self.startStyling(start)

        for line in text.splitlines(True):
            style = self._classify_line(line)
            self.setStyling(len(line.encode("utf-8")), style)

    def _classify_line(self, line: str) -> int:
        if self._ERROR_RE.search(line):
            return 1
        if self._WARN_RE.search(line):
            return 2
        if self._INFO_RE.search(line):
            return 3
        if self._DEBUG_RE.search(line):
            return 4
        return 0
