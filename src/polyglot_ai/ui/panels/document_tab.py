"""Document tab — markdown editor with live preview and formatting toolbar."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


class DocumentTab(QWidget):
    """Markdown editor with split view: source + live preview."""

    def __init__(self, file_path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._is_modified = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Formatting toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        toolbar.setStyleSheet(
            f"background: {tc.get('bg_surface')}; border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 0, 8, 0)
        tb_layout.setSpacing(2)

        btn_style = f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 3px;
                padding: 2px 8px; color: {tc.get('text_primary')};
                font-size: {tc.FONT_MD}px; min-width: 24px;
            }}
            QPushButton:hover {{ background: {tc.get('bg_hover')}; }}
            QPushButton:pressed {{ background: {tc.get('bg_active')}; }}
        """

        buttons = [
            ("B", "Bold (Ctrl+B)", lambda: self._wrap("**", "**")),
            ("I", "Italic (Ctrl+I)", lambda: self._wrap("*", "*")),
            ("|", None, None),  # Separator
            ("H1", "Heading 1", lambda: self._prefix("# ")),
            ("H2", "Heading 2", lambda: self._prefix("## ")),
            ("H3", "Heading 3", lambda: self._prefix("### ")),
            ("|", None, None),
            ("•", "Bullet list", lambda: self._prefix("- ")),
            ("1.", "Numbered list", lambda: self._prefix("1. ")),
            ("|", None, None),
            ("🔗", "Link", lambda: self._wrap("[", "](url)")),
            ("`", "Inline code", lambda: self._wrap("`", "`")),
            ("```", "Code block", lambda: self._wrap("```\n", "\n```")),
            (">", "Blockquote", lambda: self._prefix("> ")),
            ("—", "Horizontal rule", lambda: self._insert("\n---\n")),
        ]

        for text, tooltip, callback in buttons:
            if text == "|":
                sep = QWidget()
                sep.setFixedWidth(1)
                sep.setFixedHeight(20)
                sep.setStyleSheet(f"background: {tc.get('border_secondary')};")
                tb_layout.addWidget(sep)
                continue
            btn = QPushButton(text)
            btn.setToolTip(tooltip or text)
            btn.setStyleSheet(btn_style)
            if text == "B":
                btn.setStyleSheet(btn_style + "QPushButton { font-weight: bold; }")
            elif text == "I":
                btn.setStyleSheet(btn_style + "QPushButton { font-style: italic; }")
            btn.clicked.connect(callback)
            tb_layout.addWidget(btn)

        tb_layout.addStretch()

        # Export buttons
        export_pdf = QPushButton("PDF")
        export_pdf.setToolTip("Export as PDF")
        export_pdf.setStyleSheet(btn_style)
        export_pdf.clicked.connect(self._export_pdf)
        tb_layout.addWidget(export_pdf)

        export_html = QPushButton("HTML")
        export_html.setToolTip("Export as HTML")
        export_html.setStyleSheet(btn_style)
        export_html.clicked.connect(self._export_html)
        tb_layout.addWidget(export_html)

        layout.addWidget(toolbar)

        # Split view: source | preview
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Source editor
        self._source = QPlainTextEdit()
        self._source.setFont(QFont(tc.FONT_CODE.split(",")[0].strip('"'), tc.FONT_BASE))
        self._source.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {tc.get('bg_base')}; color: {tc.get('text_primary')};
                border: none; padding: 12px;
                selection-background-color: {tc.get('bg_active')};
            }}
        """)
        self._source.setTabStopDistance(32)
        self._source.textChanged.connect(self._on_text_changed)
        splitter.addWidget(self._source)

        # Preview
        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(True)
        self._preview.setStyleSheet(f"""
            QTextBrowser {{
                background: {tc.get('bg_base')}; color: {tc.get('text_primary')};
                border: none; border-left: 1px solid {tc.get('border_secondary')};
                padding: 16px; font-size: {tc.FONT_BASE}px;
            }}
        """)
        splitter.addWidget(self._preview)
        splitter.setSizes([500, 500])

        layout.addWidget(splitter)

        # Debounced preview update
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._update_preview)

        if file_path:
            self.load(file_path)

    @property
    def source_editor(self) -> QPlainTextEdit:
        return self._source

    def _on_text_changed(self) -> None:
        self._is_modified = True
        self._preview_timer.start()

    def _update_preview(self) -> None:
        text = self._source.toPlainText()
        # Try Qt's built-in markdown support (Qt 6.1+)
        if hasattr(self._preview, "setMarkdown"):
            self._preview.setMarkdown(text)
        else:
            # Fallback: basic regex markdown-to-HTML
            self._preview.setHtml(self._markdown_to_html(text))

    def _markdown_to_html(self, text: str) -> str:
        """Basic markdown to HTML converter (fallback)."""
        html = text
        # Escape HTML
        html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Code blocks
        html = re.sub(r"```(\w*)\n(.*?)```", r"<pre><code>\2</code></pre>", html, flags=re.DOTALL)
        # Inline code
        html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
        # Headers
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
        # Bold and italic
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
        html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
        # Links
        html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
        # Lists
        html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
        # Horizontal rule
        html = re.sub(r"^---$", "<hr>", html, flags=re.MULTILINE)
        # Blockquotes
        html = re.sub(r"^> (.+)$", r"<blockquote>\1</blockquote>", html, flags=re.MULTILINE)
        # Paragraphs
        html = re.sub(r"\n\n", "</p><p>", html)
        html = f"<p>{html}</p>"
        return html

    # Formatting helpers

    def _wrap(self, before: str, after: str) -> None:
        cursor = self._source.textCursor()
        selected = cursor.selectedText()
        if selected:
            cursor.insertText(f"{before}{selected}{after}")
        else:
            pos = cursor.position()
            cursor.insertText(f"{before}{after}")
            cursor.setPosition(pos + len(before))
            self._source.setTextCursor(cursor)

    def _prefix(self, prefix: str) -> None:
        cursor = self._source.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        cursor.insertText(prefix)

    def _insert(self, text: str) -> None:
        self._source.textCursor().insertText(text)

    # Export

    def _export_pdf(self) -> None:
        if not self._file_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export as PDF", str(self._file_path.with_suffix(".pdf")),
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        try:
            from PyQt6.QtPrintSupport import QPrinter
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            self._preview.document().print_(printer)
            logger.info("Exported PDF: %s", path)
        except Exception as e:
            logger.exception("PDF export failed")

    def _export_html(self) -> None:
        if not self._file_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export as HTML", str(self._file_path.with_suffix(".html")),
            "HTML Files (*.html)",
        )
        if not path:
            return
        try:
            html = self._preview.toHtml()
            Path(path).write_text(html, encoding="utf-8")
            logger.info("Exported HTML: %s", path)
        except Exception as e:
            logger.exception("HTML export failed")

    # File operations (same interface as EditorTab)

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @file_path.setter
    def file_path(self, path: Path) -> None:
        self._file_path = path

    @property
    def is_modified(self) -> bool:
        return self._is_modified

    def load(self, path: Path) -> None:
        self._file_path = path
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        self._source.setPlainText(text)
        self._is_modified = False
        self._update_preview()
        logger.info("Loaded document: %s", path)

    def save(self) -> bool:
        if self._file_path is None:
            return False
        try:
            self._file_path.write_text(self._source.toPlainText(), encoding="utf-8")
            self._is_modified = False
            logger.info("Saved document: %s", self._file_path)
            return True
        except OSError:
            logger.exception("Failed to save document: %s", self._file_path)
            return False
