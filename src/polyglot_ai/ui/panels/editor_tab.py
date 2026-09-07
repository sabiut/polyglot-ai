"""Single code editor tab wrapping QScintilla."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from polyglot_ai.core.coverage import FileCoverage

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
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

from polyglot_ai.ui import theme_colors as tc

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

        self._find_bar = self._build_find_bar()
        self._find_bar.setVisible(False)
        layout.addWidget(self._find_bar)

        self._editor = QsciScintilla()
        layout.addWidget(self._editor)

        self._provider_manager = None
        self._settings = None
        self._completion_task: asyncio.Task | None = None
        self._completion_annotation_line: int | None = None
        self._completion_text: str | None = None

        # Debounce timer for completions
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(400)
        self._completion_timer.timeout.connect(self._request_completion)

        # QScintilla consumes Tab itself (indentation) before it would
        # ever bubble up to keyPressEvent on this widget, so accepting
        # a completion with Tab requires intercepting the key on the
        # editor via an event filter (see eventFilter below).
        self._editor.installEventFilter(self)

        self._setup_editor()
        if file_path:
            self._setup_lexer(file_path.suffix.lower())

        self._editor.modificationChanged.connect(self._on_modification_changed)
        self._editor.textChanged.connect(self._on_text_changed)

    # ── Find / Replace bar ────────────────────────────────────────

    def _build_find_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        bar.setStyleSheet(
            f"background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )

        input_css = (
            f"QLineEdit {{ background: {tc.get('bg_input')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; border-radius: 3px; "
            f"padding: 3px 6px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        btn_css = (
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; border-radius: 3px; "
            f"padding: 3px 10px; font-size: {tc.FONT_SM}px; }}"
            f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }}"
            f"QPushButton:checked {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border-color: {tc.get('accent_primary')}; }}"
        )

        self._find_input = QLineEdit()
        self._find_input.setPlaceholderText("Find")
        self._find_input.setStyleSheet(input_css)
        self._find_input.returnPressed.connect(self.find_next)
        row.addWidget(self._find_input, 2)

        self._replace_input = QLineEdit()
        self._replace_input.setPlaceholderText("Replace with")
        self._replace_input.setStyleSheet(input_css)
        self._replace_input.returnPressed.connect(self._replace_one)
        row.addWidget(self._replace_input, 2)

        self._case_btn = QPushButton("Aa")
        self._case_btn.setCheckable(True)
        self._case_btn.setToolTip("Match case")
        self._case_btn.setStyleSheet(btn_css)
        row.addWidget(self._case_btn)

        self._regex_btn = QPushButton(".*")
        self._regex_btn.setCheckable(True)
        self._regex_btn.setToolTip("Regular expression")
        self._regex_btn.setStyleSheet(btn_css)
        row.addWidget(self._regex_btn)

        prev_btn = QPushButton("Prev")
        prev_btn.setToolTip("Find previous (Shift+Enter)")
        prev_btn.setStyleSheet(btn_css)
        prev_btn.clicked.connect(self.find_prev)
        row.addWidget(prev_btn)

        next_btn = QPushButton("Next")
        next_btn.setToolTip("Find next (Enter)")
        next_btn.setStyleSheet(btn_css)
        next_btn.clicked.connect(self.find_next)
        row.addWidget(next_btn)

        self._replace_btn = QPushButton("Replace")
        self._replace_btn.setStyleSheet(btn_css)
        self._replace_btn.clicked.connect(self._replace_one)
        row.addWidget(self._replace_btn)

        self._replace_all_btn = QPushButton("Replace All")
        self._replace_all_btn.setStyleSheet(btn_css)
        self._replace_all_btn.clicked.connect(self._replace_all)
        row.addWidget(self._replace_all_btn)

        self._find_status = QLabel("")
        self._find_status.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        row.addWidget(self._find_status)
        row.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.setStyleSheet(btn_css)
        close_btn.clicked.connect(self.hide_find_bar)
        row.addWidget(close_btn)

        # Esc anywhere in the bar closes it and returns focus.
        QShortcut(QKeySequence(Qt.Key.Key_Escape), bar, self.hide_find_bar)
        # Shift+Enter in the find field searches backwards.
        QShortcut(QKeySequence("Shift+Return"), self._find_input, self.find_prev)
        return bar

    def show_find_bar(self, *, replace: bool = False) -> None:
        """Reveal the bar; prefill from the current selection."""
        self._find_bar.setVisible(True)
        for w in (self._replace_input, self._replace_btn, self._replace_all_btn):
            w.setVisible(replace)
        selected = self._editor.selectedText()
        if selected and "\n" not in selected:
            self._find_input.setText(selected)
        self._find_input.setFocus()
        self._find_input.selectAll()

    def hide_find_bar(self) -> None:
        if self._find_bar.isVisible():
            self._find_bar.setVisible(False)
            self._find_status.setText("")
            self._editor.setFocus()

    def _do_find(self, *, forward: bool) -> bool:
        needle = self._find_input.text()
        if not needle:
            return False
        # When searching backwards, start from the selection anchor so
        # repeated Prev presses don't keep re-matching the same hit.
        if not forward:
            line, index, _, _ = self._editor.getSelection()
            if line >= 0:
                self._editor.setCursorPosition(line, index)
        found = self._editor.findFirst(
            needle,
            self._regex_btn.isChecked(),
            self._case_btn.isChecked(),
            False,  # whole word
            True,  # wrap
            forward,
        )
        self._find_status.setText("" if found else "No matches")
        return found

    def find_next(self) -> bool:
        return self._do_find(forward=True)

    def find_prev(self) -> bool:
        return self._do_find(forward=False)

    def _replace_one(self) -> None:
        # findFirst selects the match; replace() acts on that selection.
        if self._editor.hasSelectedText() or self.find_next():
            self._editor.replace(self._replace_input.text())
            self.find_next()

    def _replace_all(self) -> None:
        needle = self._find_input.text()
        if not needle:
            return
        count = 0
        self._editor.beginUndoAction()
        try:
            self._editor.setCursorPosition(0, 0)
            # wrap=False so the loop terminates at end of document.
            while self._editor.findFirst(
                needle,
                self._regex_btn.isChecked(),
                self._case_btn.isChecked(),
                False,
                False,
                True,
            ):
                self._editor.replace(self._replace_input.text())
                count += 1
        finally:
            self._editor.endUndoAction()
        self._find_status.setText(f"Replaced {count}")

    def _editor_font(self) -> QFont:
        """The user's editor font (Settings → Editor), or the Monospace 11 default."""
        family, size = "Monospace", 11
        if self._settings is not None:
            family = str(self._settings.get("editor.font_family") or family)
            try:
                size = int(self._settings.get("editor.font_size") or size)
            except (TypeError, ValueError):
                pass
        font = QFont(family, size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def _tab_width(self) -> int:
        if self._settings is not None:
            try:
                return max(1, int(self._settings.get("editor.tab_size") or 4))
            except (TypeError, ValueError):
                pass
        return 4

    def _setup_editor(self) -> None:
        editor = self._editor

        # Font
        editor.setFont(self._editor_font())

        # Line numbers (margin 0)
        editor.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        editor.setMarginWidth(0, "00000")
        editor.setMarginsForegroundColor(QColor(tc.get("text_tertiary")))
        editor.setMarginsBackgroundColor(QColor(tc.get("bg_surface")))

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
            QColor(tc.get("bg_surface")),
            QColor(tc.get("bg_surface")),
        )

        # Indentation
        editor.setAutoIndent(True)
        editor.setIndentationsUseTabs(False)
        editor.setTabWidth(self._tab_width())
        editor.setIndentationGuides(True)
        editor.setTabIndents(True)
        editor.setBackspaceUnindents(True)

        # Brace matching
        editor.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
        editor.setMatchedBraceForegroundColor(QColor(tc.get("text_primary")))
        editor.setMatchedBraceBackgroundColor(QColor(tc.get("bg_feedback_pos")))
        editor.setUnmatchedBraceForegroundColor(QColor(tc.get("text_primary")))
        editor.setUnmatchedBraceBackgroundColor(QColor(tc.get("bg_feedback_neg")))

        # Current line highlight
        editor.setCaretForegroundColor(QColor(tc.get("text_secondary")))
        editor.setCaretLineVisible(True)
        editor.setCaretLineBackgroundColor(QColor(tc.get("bg_hover_subtle")))

        # Selection
        editor.setSelectionBackgroundColor(QColor(tc.get("bg_active")))

        # Edge column at 120
        editor.setEdgeMode(QsciScintilla.EdgeMode.EdgeLine)
        editor.setEdgeColumn(120)
        editor.setEdgeColor(QColor(tc.get("border_primary")))

        # Background
        editor.setPaper(QColor(tc.get("bg_base")))
        editor.setColor(QColor(tc.get("text_primary")))

        # Wrap
        editor.setWrapMode(QsciScintilla.WrapMode.WrapNone)

        # EOL
        editor.setEolMode(QsciScintilla.EolMode.EolUnix)
        editor.setEolVisibility(False)

        # Auto-complete (basic word completion)
        editor.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsDocument)
        editor.setAutoCompletionThreshold(3)

        # User-configurable bits (word wrap, line numbers). Settings are
        # injected after construction, so this first pass applies the
        # built-in defaults; ``set_ai_services`` re-applies from the
        # real SettingsManager once it lands.
        self._apply_editor_settings()

    def _apply_editor_settings(self) -> None:
        """Apply user-configurable editor settings to the QScintilla widget.

        Reads the ``editor.*`` keys from the injected SettingsManager,
        falling back to the app defaults when ``self._settings`` hasn't
        been injected yet (it arrives via ``set_ai_services`` after
        ``__init__``). Also re-run on open tabs after the Settings
        dialog is saved (see ``EditorPanel.apply_settings``).
        """
        if self._settings is not None:
            word_wrap = bool(self._settings.get("editor.word_wrap"))
            show_line_numbers = bool(self._settings.get("editor.show_line_numbers"))
        else:
            word_wrap = False
            show_line_numbers = True

        font = self._editor_font()
        self._editor.setFont(font)
        lexer = self._editor.lexer()
        if lexer is not None:
            lexer.setDefaultFont(font)
            lexer.setFont(font)
        self._editor.setTabWidth(self._tab_width())

        self._editor.setWrapMode(
            QsciScintilla.WrapMode.WrapWord if word_wrap else QsciScintilla.WrapMode.WrapNone
        )
        if show_line_numbers:
            self._editor.setMarginWidth(0, "00000")
        else:
            self._editor.setMarginWidth(0, 0)

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

        # Apply the editor font to the lexer
        font = self._editor_font()
        lexer.setDefaultFont(font)
        lexer.setFont(font)
        lexer.setDefaultPaper(QColor(tc.get("bg_base")))
        lexer.setDefaultColor(QColor(tc.get("text_primary")))

        # Apply readable dark-theme colors to every token style
        bg = QColor(tc.get("bg_base"))
        for style_id in range(128):
            lexer.setPaper(bg, style_id)
            lexer.setFont(font, style_id)

        self._apply_token_colors(lexer, suffix)
        self._editor.setLexer(lexer)

    def _apply_token_colors(self, lexer, suffix: str) -> None:
        """Apply VS Code-inspired token colors to lexer styles."""
        # Color palette — readable on the editor background
        colors = {
            "keyword": tc.get("syn_keyword"),  # blue (softer than default)
            "keyword2": tc.get("syn_keyword2"),  # purple/magenta
            "string": tc.get("syn_string"),  # warm orange/salmon
            "string2": tc.get("syn_string"),
            "number": tc.get("syn_number"),  # light green
            "comment": tc.get("syn_comment"),  # green
            "decorator": tc.get("syn_decorator"),  # yellow
            "function": tc.get("syn_decorator"),  # yellow
            "class_name": tc.get("syn_builtin"),  # teal
            "operator": tc.get("text_primary"),  # white
            "identifier": tc.get("syn_identifier"),  # light blue
            "default": tc.get("text_primary"),  # white
            "builtin": tc.get("syn_builtin"),  # teal
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
        self._editor.setMarginsBackgroundColor(QColor(tc.get("bg_surface")))
        self._editor.setMarginsForegroundColor(QColor(tc.get("text_tertiary")))

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
            self.last_save_error = None
            logger.info("Saved file: %s", self._file_path)
            return True
        except OSError as exc:
            # Kept for the panel to surface in an error dialog — a
            # bare False loses the reason (read-only file? disk full?).
            self.last_save_error = str(exc)
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
            (self._MARKER_HIT, tc.get("accent_success")),  # green — line was executed
            (self._MARKER_MISS, tc.get("accent_error")),  # red — line was not executed
            (self._MARKER_PARTIAL, tc.get("accent_warning")),  # amber — branch only partly covered
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
        # Settings arrive after __init__ ran _setup_editor with defaults;
        # re-apply the user's word-wrap / line-number preferences now.
        self._apply_editor_settings()

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
        self._completion_text = text  # raw suggestion, inserted on Tab
        self._editor.annotate(
            line,
            f"  {preview}  (Tab to accept)",
            self._editor.SendScintilla(self._editor.SCI_GETSTYLEAT, 0),
        )

    def _clear_completion_annotation(self) -> None:
        self._completion_text = None
        if self._completion_annotation_line is not None:
            self._editor.clearAnnotations(self._completion_annotation_line)
            self._completion_annotation_line = None

    def _can_accept_completion(self) -> bool:
        """A suggestion is acceptable when its annotation is visible and
        the cursor is still on the annotated line."""
        return (
            self._completion_text is not None
            and self._completion_annotation_line is not None
            and self._editor.getCursorPosition()[0] == self._completion_annotation_line
        )

    def _accept_completion(self) -> None:
        """Insert the pending suggestion at the cursor as one undo action.

        The suggestion is inserted verbatim via ``insertAt`` — QScintilla
        only auto-indents on *typed* newlines, so multi-line suggestions
        keep exactly the indentation the model produced.
        """
        text = self._completion_text
        if not text:
            return
        self._clear_completion_annotation()
        line, col = self._editor.getCursorPosition()
        self._editor.beginUndoAction()
        try:
            self._editor.insertAt(text, line, col)
        finally:
            self._editor.endUndoAction()
        # Move the cursor to the end of the inserted text.
        inserted_lines = text.split("\n")
        if len(inserted_lines) == 1:
            self._editor.setCursorPosition(line, col + len(text))
        else:
            self._editor.setCursorPosition(line + len(inserted_lines) - 1, len(inserted_lines[-1]))

    def eventFilter(self, obj, event) -> bool:
        """Accept (Tab) or dismiss (Esc) a pending completion.

        Installed on the QScintilla editor because it consumes Tab for
        indentation before the key would ever reach ``keyPressEvent``
        on this widget.
        """
        from PyQt6.QtCore import QEvent, Qt

        if obj is self._editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab and self._can_accept_completion():
                # Insert the suggestion, clear the annotation, and
                # swallow the key so no literal tab/indent happens.
                self._accept_completion()
                return True
            if event.key() == Qt.Key.Key_Escape and self._completion_annotation_line is not None:
                self._clear_completion_annotation()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        """Override to handle Tab for accepting completions."""
        from PyQt6.QtCore import Qt

        if event.key() == Qt.Key.Key_Tab and self._can_accept_completion():
            # Insert the stored suggestion at the cursor (one undo
            # action) and clear the annotation instead of tabbing.
            # Normally the editor's event filter handles this first;
            # this is a fallback for when the tab itself has focus.
            self._accept_completion()
            return
        super().keyPressEvent(event)
