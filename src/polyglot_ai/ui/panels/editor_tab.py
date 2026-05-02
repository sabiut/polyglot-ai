"""Single code editor tab wrapping QScintilla."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from polyglot_ai.core.coverage import FileCoverage

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from PyQt6.Qsci import (
    QsciLexerBash,
    QsciLexerCPP,
    QsciLexerCSS,
    QsciLexerHTML,
    QsciLexerJavaScript,
    QsciLexerJSON,
    QsciLexerMarkdown,
    QsciLexerPython,
    QsciLexerSQL,
    QsciLexerXML,
    QsciLexerYAML,
    QsciScintilla,
)

logger = logging.getLogger(__name__)

# Map file extensions to QScintilla lexer classes
LEXER_MAP: dict[str, type] = {
    ".py": QsciLexerPython,
    ".pyw": QsciLexerPython,
    ".js": QsciLexerJavaScript,
    ".mjs": QsciLexerJavaScript,
    ".ts": QsciLexerJavaScript,
    ".tsx": QsciLexerJavaScript,
    ".jsx": QsciLexerJavaScript,
    ".json": QsciLexerJSON,
    ".html": QsciLexerHTML,
    ".htm": QsciLexerHTML,
    ".css": QsciLexerCSS,
    ".scss": QsciLexerCSS,
    ".xml": QsciLexerXML,
    ".svg": QsciLexerXML,
    ".md": QsciLexerMarkdown,
    ".markdown": QsciLexerMarkdown,
    ".sh": QsciLexerBash,
    ".bash": QsciLexerBash,
    ".zsh": QsciLexerBash,
    ".sql": QsciLexerSQL,
    ".yaml": QsciLexerYAML,
    ".yml": QsciLexerYAML,
    ".c": QsciLexerCPP,
    ".cpp": QsciLexerCPP,
    ".cxx": QsciLexerCPP,
    ".cc": QsciLexerCPP,
    ".h": QsciLexerCPP,
    ".hpp": QsciLexerCPP,
    ".hh": QsciLexerCPP,
    ".hxx": QsciLexerCPP,
    # Arduino sketches are C++ with auto-generated headers; the
    # CPP lexer's keyword set covers ``setup()``, ``loop()``,
    # ``Serial``, etc. close enough for syntax-colouring purposes.
    ".ino": QsciLexerCPP,
    ".pde": QsciLexerCPP,  # legacy Arduino / Processing
    ".java": QsciLexerCPP,
    ".go": QsciLexerCPP,
    ".rs": QsciLexerCPP,
    ".toml": QsciLexerYAML,
    # DevOps / IaC (use YAML lexer for HCL/Terraform — close enough)
    ".tf": QsciLexerYAML,
    ".tfvars": QsciLexerYAML,
    ".hcl": QsciLexerYAML,
    ".j2": QsciLexerHTML,
    ".jinja2": QsciLexerHTML,
}

# Log files use a custom lexer (added separately in _setup_lexer)
_LOG_EXTENSIONS = frozenset({".log"})

# Dark theme colors for the editor
DARK_COLORS = {
    "background": "#1e1e1e",
    "foreground": "#d4d4d4",
    "caret": "#aeafad",
    "selection_bg": "#264f78",
    "current_line": "#2a2d2e",
    "margin_bg": "#252526",
    "margin_fg": "#858585",
    "fold_margin_bg": "#252526",
    "matched_brace_bg": "#0d5640",
    "matched_brace_fg": "#d4d4d4",
    "unmatched_brace_bg": "#5a1d1d",
    "edge_color": "#3c3c3c",
}


class EditorTab(QWidget):
    """A single code editor tab with QScintilla."""

    def __init__(
        self,
        file_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._is_modified = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QsciScintilla()
        layout.addWidget(self._editor)

        self._provider_manager = None
        self._settings = None
        self._completion_task: asyncio.Task | None = None
        self._completion_annotation_line: int | None = None

        # Debounce timer for completions
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(400)
        self._completion_timer.timeout.connect(self._request_completion)

        self._setup_editor()
        if file_path:
            self._setup_lexer(file_path.suffix.lower())

        self._editor.modificationChanged.connect(self._on_modification_changed)
        self._editor.textChanged.connect(self._on_text_changed)

    def _setup_editor(self) -> None:
        editor = self._editor

        # Font
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor.setFont(font)

        # Line numbers (margin 0)
        editor.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        editor.setMarginWidth(0, "00000")
        editor.setMarginsForegroundColor(QColor(DARK_COLORS["margin_fg"]))
        editor.setMarginsBackgroundColor(QColor(DARK_COLORS["margin_bg"]))

        # Coverage gutter (margin 1) — shows test-coverage hit/miss bars
        # when a coverage report has been applied via ``set_coverage``.
        # Stays at 0 width until coverage data lands so the editor
        # looks identical to before for users who never run with --cov.
        # Margin type is "symbol" so we can attach markers per line;
        # the actual marker glyphs are configured in
        # :py:meth:`_setup_coverage_markers` once we know which marker
        # IDs are free (QScintilla allocates a small fixed pool).
        editor.setMarginType(1, QsciScintilla.MarginType.SymbolMargin)
        editor.setMarginWidth(1, 0)
        editor.setMarginSensitivity(1, False)
        self._setup_coverage_markers()

        # Code folding (margin 2)
        editor.setFolding(QsciScintilla.FoldStyle.BoxedTreeFoldStyle)
        editor.setFoldMarginColors(
            QColor(DARK_COLORS["fold_margin_bg"]),
            QColor(DARK_COLORS["fold_margin_bg"]),
        )

        # Indentation
        editor.setAutoIndent(True)
        editor.setIndentationsUseTabs(False)
        editor.setTabWidth(4)
        editor.setIndentationGuides(True)
        editor.setTabIndents(True)
        editor.setBackspaceUnindents(True)

        # Brace matching
        editor.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
        editor.setMatchedBraceForegroundColor(QColor(DARK_COLORS["matched_brace_fg"]))
        editor.setMatchedBraceBackgroundColor(QColor(DARK_COLORS["matched_brace_bg"]))
        editor.setUnmatchedBraceForegroundColor(QColor("#d4d4d4"))
        editor.setUnmatchedBraceBackgroundColor(QColor(DARK_COLORS["unmatched_brace_bg"]))

        # Current line highlight
        editor.setCaretForegroundColor(QColor(DARK_COLORS["caret"]))
        editor.setCaretLineVisible(True)
        editor.setCaretLineBackgroundColor(QColor(DARK_COLORS["current_line"]))

        # Selection
        editor.setSelectionBackgroundColor(QColor(DARK_COLORS["selection_bg"]))

        # Edge column at 120
        editor.setEdgeMode(QsciScintilla.EdgeMode.EdgeLine)
        editor.setEdgeColumn(120)
        editor.setEdgeColor(QColor(DARK_COLORS["edge_color"]))

        # Background
        editor.setPaper(QColor(DARK_COLORS["background"]))
        editor.setColor(QColor(DARK_COLORS["foreground"]))

        # Wrap
        editor.setWrapMode(QsciScintilla.WrapMode.WrapNone)

        # EOL
        editor.setEolMode(QsciScintilla.EolMode.EolUnix)
        editor.setEolVisibility(False)

        # Auto-complete (basic word completion)
        editor.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsDocument)
        editor.setAutoCompletionThreshold(3)

    def _setup_lexer(self, suffix: str) -> None:
        # Log files use a custom lexer with its own styling
        if suffix in _LOG_EXTENSIONS:
            from polyglot_ai.ui.lexers.log_lexer import LogLexer

            lexer = LogLexer(self._editor)
            self._editor.setLexer(lexer)
            return

        lexer_cls = LEXER_MAP.get(suffix)
        if lexer_cls is None:
            return

        lexer = lexer_cls(self._editor)

        # Apply dark theme font to lexer
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        lexer.setDefaultFont(font)
        lexer.setDefaultPaper(QColor(DARK_COLORS["background"]))
        lexer.setDefaultColor(QColor(DARK_COLORS["foreground"]))

        # Apply readable dark-theme colors to every token style
        bg = QColor(DARK_COLORS["background"])
        for style_id in range(128):
            lexer.setPaper(bg, style_id)
            lexer.setFont(font, style_id)

        self._apply_token_colors(lexer, suffix)
        self._editor.setLexer(lexer)

    def _apply_token_colors(self, lexer, suffix: str) -> None:
        """Apply VS Code-inspired token colors to lexer styles."""
        # Color palette — readable on #1e1e1e background
        colors = {
            "keyword": "#569cd6",  # blue (softer than default)
            "keyword2": "#c586c0",  # purple/magenta
            "string": "#ce9178",  # warm orange/salmon
            "string2": "#ce9178",
            "number": "#b5cea8",  # light green
            "comment": "#6a9955",  # green
            "decorator": "#dcdcaa",  # yellow
            "function": "#dcdcaa",  # yellow
            "class_name": "#4ec9b0",  # teal
            "operator": "#d4d4d4",  # white
            "identifier": "#9cdcfe",  # light blue
            "default": "#d4d4d4",  # white
            "builtin": "#4ec9b0",  # teal
        }

        if isinstance(lexer, QsciLexerPython):
            color_map = {
                0: colors["default"],  # Default
                1: colors["comment"],  # Comment
                2: colors["number"],  # Number
                3: colors["string"],  # DoubleQuotedString
                4: colors["string"],  # SingleQuotedString
                5: colors["keyword"],  # Keyword
                6: colors["string"],  # TripleSingleQuotedString
                7: colors["string"],  # TripleDoubleQuotedString
                8: colors["function"],  # ClassName
                9: colors["function"],  # FunctionMethodName
                10: colors["operator"],  # Operator
                11: colors["identifier"],  # Identifier
                12: colors["comment"],  # CommentBlock
                13: colors["string"],  # UnclosedString
                14: colors["decorator"],  # HighlightedIdentifier
                15: colors["decorator"],  # Decorator
            }
        elif isinstance(lexer, QsciLexerJavaScript):
            color_map = {
                0: colors["default"],
                1: colors["comment"],  # Comment
                2: colors["comment"],  # CommentLine
                3: colors["comment"],  # CommentDoc
                4: colors["number"],  # Number
                5: colors["keyword"],  # Keyword
                6: colors["string"],  # DoubleQuotedString
                7: colors["string"],  # SingleQuotedString
                10: colors["operator"],  # Operator
                11: colors["identifier"],  # Identifier
                15: colors["comment"],  # CommentLineDoc
            }
        elif isinstance(lexer, QsciLexerCSS):
            color_map = {
                0: colors["default"],
                1: colors["comment"],
                2: colors["keyword"],  # Tag
                4: colors["identifier"],  # Class selector
                6: colors["string"],  # Value
                8: colors["number"],  # Number
                13: colors["keyword2"],  # Property
            }
        elif isinstance(lexer, QsciLexerHTML):
            color_map = {
                0: colors["default"],
                1: colors["keyword"],  # Tag
                2: colors["identifier"],  # UnknownTag
                3: colors["class_name"],  # Attribute
                6: colors["string"],  # HTMLDoubleQuotedString
                7: colors["string"],  # HTMLSingleQuotedString
                9: colors["comment"],  # HTMLComment
            }
        elif isinstance(lexer, QsciLexerBash):
            color_map = {
                0: colors["default"],
                1: colors["comment"],
                2: colors["number"],
                3: colors["keyword"],
                4: colors["string"],  # DoubleQuotedString
                5: colors["string"],  # SingleQuotedString
                6: colors["operator"],
                7: colors["identifier"],  # Identifier
                8: colors["identifier"],  # Scalar
            }
        elif isinstance(lexer, QsciLexerSQL):
            color_map = {
                0: colors["default"],
                1: colors["comment"],
                2: colors["comment"],  # CommentLine
                5: colors["keyword"],
                6: colors["string"],
                7: colors["string"],
                8: colors["number"],
                11: colors["operator"],
            }
        else:
            # Generic fallback: apply default + comment + string + keyword
            color_map = {
                0: colors["default"],
                1: colors["comment"],
                2: colors["comment"],
                3: colors["string"],
                4: colors["string"],
                5: colors["keyword"],
                6: colors["string"],
                7: colors["string"],
                8: colors["number"],
                10: colors["operator"],
                11: colors["identifier"],
            }

        for style_id, color_hex in color_map.items():
            lexer.setColor(QColor(color_hex), style_id)

        # Reapply margin colors after lexer set (lexer can override them)
        self._editor.setMarginsBackgroundColor(QColor(DARK_COLORS["margin_bg"]))
        self._editor.setMarginsForegroundColor(QColor(DARK_COLORS["margin_fg"]))

    def _on_modification_changed(self, modified: bool) -> None:
        self._is_modified = modified

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @file_path.setter
    def file_path(self, path: Path) -> None:
        self._file_path = path
        self._setup_lexer(path.suffix.lower())

    @property
    def is_modified(self) -> bool:
        return self._is_modified

    @property
    def editor(self) -> QsciScintilla:
        return self._editor

    def load(self, path: Path) -> None:
        self._file_path = path
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        self._editor.setText(text)
        self._editor.setModified(False)
        self._is_modified = False
        self._setup_lexer(path.suffix.lower())
        logger.info("Loaded file: %s", path)

    def save(self) -> bool:
        if self._file_path is None:
            return False
        try:
            self._file_path.write_text(self._editor.text(), encoding="utf-8")
            self._editor.setModified(False)
            self._is_modified = False
            logger.info("Saved file: %s", self._file_path)
            return True
        except OSError:
            logger.exception("Failed to save file: %s", self._file_path)
            return False

    def get_cursor_position(self) -> tuple[int, int]:
        line, col = self._editor.getCursorPosition()
        return line + 1, col + 1  # 1-indexed for display

    # ── Test coverage gutter ──────────────────────────────────────
    #
    # Public surface:
    #   - set_coverage(file_coverage):  paint hit/miss/partial bars in
    #     margin 1 and widen the margin to make them visible
    #   - clear_coverage():             remove all coverage markers and
    #     collapse the margin back to zero width
    #
    # Implementation notes:
    #   - We use three marker IDs: HIT, MISS, PARTIAL. Marker IDs are
    #     a small pool (max 32) shared across all uses of the editor;
    #     reserving three at the top of the pool keeps them out of
    #     the way of any future debugger/breakpoint markers.
    #   - The marker shape is ``RoundRectangle`` rather than a thin
    #     vertical line because QScintilla's line-style markers don't
    #     honour foreground colour reliably across themes — the
    #     filled rect always renders.

    _MARKER_HIT = 29
    _MARKER_MISS = 30
    _MARKER_PARTIAL = 28
    _COVERAGE_MARGIN_WIDTH = 6  # pixels — wide enough to read, narrow enough not to crowd

    def _setup_coverage_markers(self) -> None:
        """Define the three coverage-gutter markers. Idempotent."""
        editor = self._editor
        # ``RoundRectangle`` is a built-in marker shape; we colour it
        # via setMarkerForegroundColor (used for the border) and
        # setMarkerBackgroundColor (used for the fill).
        for marker_id, fill in (
            (self._MARKER_HIT, "#4caf50"),  # green — line was executed
            (self._MARKER_MISS, "#d9534f"),  # red — line was not executed
            (self._MARKER_PARTIAL, "#e0a23a"),  # amber — branch only partly covered
        ):
            editor.markerDefine(QsciScintilla.MarkerSymbol.FullRectangle, marker_id)
            editor.setMarkerForegroundColor(QColor(fill), marker_id)
            editor.setMarkerBackgroundColor(QColor(fill), marker_id)

    def set_coverage(self, file_coverage: "FileCoverage") -> None:
        """Apply hit/miss/partial markers from a parsed coverage report.

        Idempotent — calling this twice (e.g. after a re-run) clears
        old markers first so stale data never lingers. Lines outside
        the file's range are silently skipped; QScintilla rejects
        out-of-range markerAdd calls anyway, but doing the bound
        check ourselves keeps the warning log clean.
        """
        self._clear_coverage_markers()
        editor = self._editor
        last_line = max(0, editor.lines() - 1)

        # Partial first, hit second, miss last so a multi-marker line
        # paints in priority order. (QScintilla overlays markers in
        # the order they're added per line.)
        for lineno in file_coverage.partial_lines:
            zero_based = lineno - 1
            if 0 <= zero_based <= last_line:
                editor.markerAdd(zero_based, self._MARKER_PARTIAL)
        for lineno in file_coverage.hit_lines:
            if lineno in file_coverage.partial_lines:
                continue  # already painted as partial
            zero_based = lineno - 1
            if 0 <= zero_based <= last_line:
                editor.markerAdd(zero_based, self._MARKER_HIT)
        for lineno in file_coverage.miss_lines:
            zero_based = lineno - 1
            if 0 <= zero_based <= last_line:
                editor.markerAdd(zero_based, self._MARKER_MISS)

        editor.setMarginWidth(1, self._COVERAGE_MARGIN_WIDTH)

    def clear_coverage(self) -> None:
        """Remove every coverage marker and collapse the gutter."""
        self._clear_coverage_markers()
        self._editor.setMarginWidth(1, 0)

    def _clear_coverage_markers(self) -> None:
        editor = self._editor
        # ``markerDeleteAll`` accepts a marker ID — clear each of our
        # three. Passing -1 would also work but would nuke any
        # markers a future feature might add.
        editor.markerDeleteAll(self._MARKER_HIT)
        editor.markerDeleteAll(self._MARKER_MISS)
        editor.markerDeleteAll(self._MARKER_PARTIAL)

    # ── AI inline completions ─────────────────────────────────────

    def set_ai_services(self, provider_manager, settings) -> None:
        self._provider_manager = provider_manager
        self._settings = settings

    def _on_text_changed(self) -> None:
        """Restart completion timer on text change."""
        self._clear_completion_annotation()
        if self._completion_task and not self._completion_task.done():
            self._completion_task.cancel()
        if self._settings and self._settings.get("editor.ai_completions"):
            self._completion_timer.start()

    def _request_completion(self) -> None:
        if not self._provider_manager or not self._settings:
            return
        if not self._settings.get("editor.ai_completions"):
            return
        self._completion_task = asyncio.ensure_future(self._do_completion())

    async def _do_completion(self) -> None:
        from polyglot_ai.core.ai.completion import get_completion

        try:
            line, col = self._editor.getCursorPosition()
            text = self._editor.text()
            lines = text.split("\n")

            # Get prefix (up to cursor) and suffix (after cursor)
            prefix_lines = lines[max(0, line - 50) : line]
            if line < len(lines):
                prefix_lines.append(lines[line][:col])
            prefix = "\n".join(prefix_lines)

            suffix_lines = []
            if line < len(lines):
                suffix_lines.append(lines[line][col:])
            suffix_lines.extend(lines[line + 1 : line + 51])
            suffix = "\n".join(suffix_lines)

            # Determine language from file extension
            language = "text"
            if self._file_path:
                ext_map = {
                    ".py": "python",
                    ".js": "javascript",
                    ".ts": "typescript",
                    ".html": "html",
                    ".css": "css",
                    ".json": "json",
                    ".go": "go",
                    ".rs": "rust",
                    ".java": "java",
                    ".sh": "bash",
                    ".sql": "sql",
                    ".yaml": "yaml",
                    ".c": "c",
                    ".cpp": "cpp",
                }
                language = ext_map.get(self._file_path.suffix.lower(), "text")

            # Get the first available provider and a fast model
            provider = None
            model = None
            for p in self._provider_manager.get_all_providers():
                provider = p
                models = p.list_models()
                # Prefer fast/small models for completions
                for m in models:
                    if any(fast in m.lower() for fast in ("mini", "nano", "flash", "haiku")):
                        model = m
                        break
                if not model and models:
                    model = models[0]
                break

            if not provider or not model:
                return

            result = await get_completion(provider, model, prefix, suffix, language)
            if result and not self._is_modified:
                return  # User changed text while waiting

            if result:
                self._show_completion_annotation(line, result)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Completion error: %s", e)

    def _show_completion_annotation(self, line: int, text: str) -> None:
        """Show completion suggestion as an annotation below the current line."""
        self._clear_completion_annotation()
        # Show as a calltip-style annotation
        preview = text.split("\n")[0][:80]  # First line, max 80 chars
        if not preview:
            return
        self._completion_annotation_line = line
        self._editor.annotate(
            line,
            f"  💡 {preview}  (Tab to accept)",
            self._editor.SendScintilla(self._editor.SCI_GETSTYLEAT, 0),
        )

    def _clear_completion_annotation(self) -> None:
        if self._completion_annotation_line is not None:
            self._editor.clearAnnotations(self._completion_annotation_line)
            self._completion_annotation_line = None

    def keyPressEvent(self, event) -> None:
        """Override to handle Tab for accepting completions."""
        from PyQt6.QtCore import Qt

        if (
            event.key() == Qt.Key.Key_Tab
            and self._completion_annotation_line is not None
            and self._completion_task
            and self._completion_task.done()
        ):
            # Accept the completion (insert the text)
            self._clear_completion_annotation()
            # The actual insertion would need the full completion text
            # For now, clear the annotation on Tab
        super().keyPressEvent(event)
