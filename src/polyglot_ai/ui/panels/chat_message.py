"""Single chat message widget — clean, modern chat design."""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class AvatarWidget(QWidget):
    """Small colored circle with an initial letter."""

    def __init__(self, letter: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letter = letter
        self._color = QColor(color)
        self.setFixedSize(28, 28)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 28, 28, 6, 6)
        painter.setPen(QColor("#ffffff"))
        font = QFont("sans-serif", 12, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._letter)
        painter.end()


class ChatMessage(QWidget):
    """A single message row in the chat — avatar + content."""

    def __init__(
        self,
        role: str,
        content: str = "",
        model: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._role = role
        self._content = content
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setStyleSheet("background: transparent;")

        _icon_btn_style = """
            QPushButton {
                background-color: transparent; border: none;
                border-radius: 5px; padding: 3px;
            }
            QPushButton:hover { background-color: rgba(255,255,255,0.1); }
        """

        is_user = role == "user"

        # Outer layout — minimal margins so text uses full width
        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(0)

        if is_user:
            # ── User message: right-aligned bubble ──
            outer.addStretch()

            # For short messages use a compact bubble, for long ones go wider
            is_long = len(content) > 200 or content.count("\n") > 3

            bubble = QWidget()
            if is_long:
                # Long/pasted content: wider card with subtle left border
                bubble.setStyleSheet(
                    "QWidget { background-color: #2a2a2c; border-radius: 12px; "
                    "border-left: 3px solid #0078d4; }"
                )
            else:
                bubble.setStyleSheet("QWidget { background-color: #303030; border-radius: 18px; }")
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(14, 10, 14, 10)
            bubble_layout.setSpacing(0)

            self._content_label = QTextBrowser()
            self._content_label.setReadOnly(True)
            self._content_label.setOpenExternalLinks(False)
            self._content_label.setFrameShape(QTextBrowser.Shape.NoFrame)
            self._content_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._content_label.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
            self._content_label.setMaximumWidth(560 if is_long else 480)
            self._content_label.setStyleSheet(
                "QTextBrowser { color: #e8e8e8; font-size: 13px; background: transparent; "
                "border: none; padding: 0px; font-family: -apple-system, 'Segoe UI', sans-serif; "
                "line-height: 145%; }"
            )
            self._content_label.anchorClicked.connect(self._on_link_clicked)
            self._content_label.document().contentsChanged.connect(self._resize_content)
            if content:
                self._set_content(content)
            bubble_layout.addWidget(self._content_label)
            outer.addWidget(bubble)

        else:
            # ── AI / System / Tool message: clean left-aligned ──
            content_col = QVBoxLayout()
            content_col.setSpacing(2)
            content_col.setContentsMargins(2, 0, 0, 0)

            # Content
            self._content_label = QTextBrowser()
            self._content_label.setReadOnly(True)
            self._content_label.setOpenExternalLinks(False)
            self._content_label.setFrameShape(QTextBrowser.Shape.NoFrame)
            self._content_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._content_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._content_label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
            self._content_label.setStyleSheet(
                "QTextBrowser { color: #d1d5db; font-size: 14px; background: transparent; "
                "border: none; padding: 0px; font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', sans-serif; "
                "line-height: 150%; }"
                "QTextBrowser a { color: #7cacf8; text-decoration: none; }"
            )
            self._content_label.anchorClicked.connect(self._on_link_clicked)
            self._content_label.document().contentsChanged.connect(self._resize_content)
            if content:
                self._set_content(content)
            content_col.addWidget(self._content_label)

            # Bottom action icons — show on hover only
            if role == "assistant":
                action_widget = QWidget()
                action_widget.setStyleSheet("background: transparent;")
                self._action_bar = QHBoxLayout(action_widget)
                self._action_bar.setContentsMargins(0, 2, 0, 0)
                self._action_bar.setSpacing(1)
                self._action_bar.addWidget(
                    self._make_icon_btn("copy", "Copy", self._copy_to_clipboard)
                )
                self._action_bar.addWidget(
                    self._make_icon_btn("thumbs_up", "Good response", self._on_thumbs_up)
                )
                self._action_bar.addWidget(
                    self._make_icon_btn("thumbs_down", "Bad response", self._on_thumbs_down)
                )
                self._action_bar.addWidget(
                    self._make_icon_btn("regenerate", "Regenerate", self._on_regenerate)
                )
                self._action_bar.addStretch()
                self._action_widget = action_widget
                action_widget.setVisible(False)
                content_col.addWidget(action_widget)

            outer.addLayout(content_col, stretch=1)

        # Callbacks set by the chat panel
        self.on_apply_code = None
        self.on_run_command = None
        self.on_regenerate = None
        self.on_edit = None  # Called with (message_widget, content) for user edit & resend
        self.on_fork = None  # Called with (message_widget) to fork conversation from this point
        self.message_db_id: int | None = None  # DB id set by chat panel for forking

    def _on_link_clicked(self, url) -> None:
        """Prompt user before opening external links from AI output."""
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtGui import QDesktopServices

        url_str = url.toString()
        # Only allow http/https — block file://, javascript:, data:, etc.
        if not url_str.startswith(("http://", "https://")):
            return
        reply = QMessageBox.question(
            self,
            "Open Link",
            f"Open this link in your browser?\n\n{url_str[:200]}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

    def _role_display_name(self) -> str:
        names = {
            "user": "You",
            "assistant": "AI Assistant",
            "tool": "Tool",
            "system": "System",
        }
        return names.get(self._role, self._role.capitalize())

    def _set_content(self, content: str) -> None:
        html = self._markdown_to_html(content)
        self._content_label.setHtml(html)
        self._resize_content()
        self._update_action_buttons(content)

    def _resize_content(self) -> None:
        """Resize the text browser to exactly fit its content — no scrollbar."""
        doc = self._content_label.document()
        # Use the widget width, not viewport (which can be 0 or too small)
        available = self._content_label.width()
        if available < 100:
            # Before first layout — use parent width as best guess
            p = self._content_label.parentWidget()
            while p and p.width() < 100:
                p = p.parentWidget()
            available = (p.width() - 40) if p else 800
        # Account for margins/padding
        text_width = max(available - 8, 300)
        doc.setTextWidth(text_width)
        doc_height = int(doc.size().height()) + 4
        self._content_label.setMinimumHeight(doc_height)
        self._content_label.setMaximumHeight(doc_height)

    def _update_action_buttons(self, content: str) -> None:
        """No per-message buttons — approval is handled by the chat panel."""
        pass

    def _make_icon_btn(self, icon_type: str, tooltip: str, callback) -> QWidget:
        """Create a small icon button for the action bar."""
        from PyQt6.QtWidgets import QPushButton

        btn = QPushButton()
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setIcon(self._draw_action_icon(icon_type))
        btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; border-radius: 5px; padding: 4px;
            }
            QPushButton:hover { background-color: #3e3e40; }
        """)
        btn.clicked.connect(callback)
        return btn

    @staticmethod
    def _draw_action_icon(icon_type: str):
        """Draw small action icons matching ChatGPT style."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#777777"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if icon_type == "copy":
            painter.drawRoundedRect(4, 1, 9, 11, 1.5, 1.5)
            painter.drawRoundedRect(2, 4, 9, 11, 1.5, 1.5)
        elif icon_type == "thumbs_up":
            # Thumb up
            painter.drawLine(4, 14, 4, 9)
            painter.drawLine(4, 9, 6, 5)
            painter.drawLine(6, 5, 8, 3)
            painter.drawLine(8, 3, 9, 5)
            painter.drawLine(9, 5, 13, 5)
            painter.drawLine(13, 5, 13, 10)
            painter.drawLine(13, 10, 7, 10)
            painter.drawLine(7, 10, 4, 14)
            painter.drawLine(1, 9, 1, 14)
            painter.drawLine(1, 14, 4, 14)
            painter.drawLine(1, 9, 4, 9)
        elif icon_type == "thumbs_down":
            # Thumb down (flipped)
            painter.drawLine(4, 2, 4, 7)
            painter.drawLine(4, 7, 6, 11)
            painter.drawLine(6, 11, 8, 13)
            painter.drawLine(8, 13, 9, 11)
            painter.drawLine(9, 11, 13, 11)
            painter.drawLine(13, 11, 13, 6)
            painter.drawLine(13, 6, 7, 6)
            painter.drawLine(7, 6, 4, 2)
            painter.drawLine(1, 2, 1, 7)
            painter.drawLine(1, 7, 4, 7)
            painter.drawLine(1, 2, 4, 2)
        elif icon_type == "regenerate":
            # Circular arrow
            from PyQt6.QtCore import QRectF

            painter.drawArc(QRectF(3, 3, 10, 10), 30 * 16, 300 * 16)
            # Arrow head
            painter.drawLine(11, 2, 13, 5)
            painter.drawLine(13, 5, 10, 5)

        painter.end()
        return QIcon(pixmap)

    def _on_thumbs_up(self) -> None:
        """User liked this response."""
        if hasattr(self, "_action_bar"):
            # Brief feedback
            for i in range(self._action_bar.count()):
                w = self._action_bar.itemAt(i).widget()
                if w and w.toolTip() == "Good response":
                    w.setStyleSheet(
                        "QPushButton { background: transparent; border: none; "
                        "border-radius: 5px; padding: 4px; } "
                        "QPushButton { background-color: #1a3a2a; }"
                    )
                    break

    def _on_thumbs_down(self) -> None:
        """User disliked this response."""
        if hasattr(self, "_action_bar"):
            for i in range(self._action_bar.count()):
                w = self._action_bar.itemAt(i).widget()
                if w and w.toolTip() == "Bad response":
                    w.setStyleSheet(
                        "QPushButton { background: transparent; border: none; "
                        "border-radius: 5px; padding: 4px; } "
                        "QPushButton { background-color: #3a1a1a; }"
                    )
                    break

    def _on_regenerate(self) -> None:
        """Request regeneration."""
        if self.on_regenerate:
            self.on_regenerate()

    def _on_edit(self) -> None:
        """Request edit & resend for this user message."""
        if self.on_edit:
            self.on_edit(self, self._content)

    @staticmethod
    def _create_edit_icon():
        """Draw a pencil/edit icon."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#888888"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # Pencil body (diagonal line)
        painter.drawLine(3, 13, 12, 4)
        # Pencil tip
        painter.drawLine(12, 4, 13, 3)
        painter.drawLine(13, 3, 14, 4)
        painter.drawLine(14, 4, 12, 4)
        # Pencil base
        painter.drawLine(3, 13, 2, 14)
        painter.drawLine(2, 14, 3, 13)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_copy_icon():
        """Draw a clipboard/copy icon."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#888888"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Back rectangle (slightly offset)
        painter.drawRoundedRect(4, 1, 9, 11, 1.5, 1.5)
        # Front rectangle
        painter.drawRoundedRect(2, 4, 9, 11, 1.5, 1.5)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_check_icon():
        """Draw a checkmark icon for 'copied' feedback."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#4ec9b0"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # Checkmark
        painter.drawLine(3, 8, 6, 12)
        painter.drawLine(6, 12, 13, 4)
        painter.end()
        return QIcon(pixmap)

    def _copy_to_clipboard(self) -> None:
        """Copy the raw message text to clipboard."""
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._content)
            if hasattr(self, "_copy_btn"):
                self._copy_btn.setIcon(self._create_check_icon())
                self._copy_btn.setToolTip("Copied!")
                from PyQt6.QtCore import QTimer

                QTimer.singleShot(1500, self._restore_copy_icon)

    def _restore_copy_icon(self) -> None:
        if hasattr(self, "_copy_btn"):
            self._copy_btn.setIcon(self._create_copy_icon())
            self._copy_btn.setToolTip("Copy to clipboard")

    def contextMenuEvent(self, event) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2d2d30; border: 1px solid #454545; color: #ccc; font-size: 12px; padding: 4px 0; }
            QMenu::item { padding: 4px 20px; }
            QMenu::item:selected { background: #094771; }
        """)
        copy_act = menu.addAction("Copy")
        copy_act.triggered.connect(self._copy_to_clipboard)
        if self.on_fork and self.message_db_id:
            fork_act = menu.addAction("Fork from here")
            fork_act.triggered.connect(lambda: self.on_fork(self))
        menu.exec(event.globalPos())

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if hasattr(self, "_action_widget"):
            self._action_widget.setVisible(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if hasattr(self, "_action_widget"):
            self._action_widget.setVisible(False)

    def resizeEvent(self, event) -> None:
        """Recalculate content height when widget width changes."""
        super().resizeEvent(event)
        self._resize_content()

    def append_content(self, text: str) -> None:
        self._content += text
        self._set_content(self._content)

    def set_final_content(self, content: str) -> None:
        self._content = content
        self._set_content(content)

    # Language display names
    _LANG_NAMES = {
        "py": "Python",
        "python": "Python",
        "js": "JavaScript",
        "javascript": "JavaScript",
        "ts": "TypeScript",
        "typescript": "TypeScript",
        "jsx": "JSX",
        "tsx": "TSX",
        "html": "HTML",
        "css": "CSS",
        "scss": "SCSS",
        "json": "JSON",
        "yaml": "YAML",
        "yml": "YAML",
        "toml": "TOML",
        "xml": "XML",
        "sql": "SQL",
        "sh": "Bash",
        "bash": "Bash",
        "shell": "Shell",
        "zsh": "Zsh",
        "fish": "Fish",
        "rust": "Rust",
        "rs": "Rust",
        "go": "Go",
        "golang": "Go",
        "java": "Java",
        "c": "C",
        "cpp": "C++",
        "cs": "C#",
        "csharp": "C#",
        "rb": "Ruby",
        "ruby": "Ruby",
        "php": "PHP",
        "swift": "Swift",
        "kotlin": "Kotlin",
        "kt": "Kotlin",
        "dart": "Dart",
        "lua": "Lua",
        "r": "R",
        "perl": "Perl",
        "scala": "Scala",
        "groovy": "Groovy",
        "dockerfile": "Dockerfile",
        "docker": "Dockerfile",
        "makefile": "Makefile",
        "cmake": "CMake",
        "diff": "Diff",
        "patch": "Patch",
        "md": "Markdown",
        "markdown": "Markdown",
        "ini": "INI",
        "cfg": "Config",
        "env": "Env",
        "txt": "Text",
        "plaintext": "Text",
        "graphql": "GraphQL",
        "proto": "Protobuf",
    }

    # Syntax keywords for basic highlighting per language family
    _KW_PATTERNS = {
        "python": {
            "keywords": (
                r"\b(def|class|import|from|return|if|elif|else|for|while|"
                r"try|except|finally|with|as|yield|async|await|raise|pass|"
                r"break|continue|lambda|and|or|not|in|is|True|False|None|"
                r"self|print|len|range|list|dict|set|tuple|str|int|float|"
                r"bool|open|super|isinstance|type|assert)\b"
            ),
            "strings": r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
            "comments": r"(#.*$)",
            "decorators": r"(@\w+)",
            "numbers": r"\b(\d+\.?\d*)\b",
        },
        "javascript": {
            "keywords": (
                r"\b(function|const|let|var|return|if|else|for|while|do|"
                r"switch|case|break|continue|new|this|class|extends|"
                r"import|export|from|default|async|await|try|catch|finally|"
                r"throw|typeof|instanceof|null|undefined|true|false|"
                r"console|require|module|process)\b"
            ),
            "strings": r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`)',
            "comments": r"(//.*$)",
            "numbers": r"\b(\d+\.?\d*)\b",
        },
        "bash": {
            "keywords": (
                r"\b(if|then|else|elif|fi|for|while|do|done|case|esac|"
                r"function|return|exit|echo|export|source|alias|sudo|"
                r"cd|ls|grep|find|cat|rm|cp|mv|mkdir|chmod|chown|curl|wget|"
                r"git|npm|pip|python|node|docker|make)\b"
            ),
            "strings": r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
            "comments": r"(#.*$)",
            "flags": r"(\s--?[\w-]+)",
        },
    }

    # Map language aliases to highlighting family
    _LANG_FAMILY = {
        "py": "python",
        "python": "python",
        "js": "javascript",
        "javascript": "javascript",
        "ts": "javascript",
        "typescript": "javascript",
        "jsx": "javascript",
        "tsx": "javascript",
        "sh": "bash",
        "bash": "bash",
        "shell": "bash",
        "zsh": "bash",
    }

    @classmethod
    def _highlight_code(cls, code: str, lang: str) -> str:
        """Apply basic syntax highlighting to code."""
        family = cls._LANG_FAMILY.get(lang.lower(), "")
        patterns = cls._KW_PATTERNS.get(family)
        if not patterns:
            return code

        # Apply highlighting in order: comments, strings, decorators, keywords, numbers, flags
        # Use placeholders to avoid double-highlighting
        replacements: list[tuple[str, str]] = []

        def stash(match, color: str) -> str:
            idx = len(replacements)
            replacements.append(
                (f"\x01H{idx}\x01", f'<span style="color:{color};">{match.group(0)}</span>')
            )
            return f"\x01H{idx}\x01"

        # Process multiline — handle line by line for comment patterns
        lines = code.split("\n")
        result_lines = []

        for line in lines:
            # Comments first (so they don't get inner-highlighted)
            if "comments" in patterns:
                line = re.sub(patterns["comments"], lambda m: stash(m, "#6a9955"), line)

            # Strings
            if "strings" in patterns:
                line = re.sub(patterns["strings"], lambda m: stash(m, "#ce9178"), line)

            # Decorators
            if "decorators" in patterns:
                line = re.sub(patterns["decorators"], lambda m: stash(m, "#dcdcaa"), line)

            # Flags (bash)
            if "flags" in patterns:
                line = re.sub(patterns["flags"], lambda m: stash(m, "#9cdcfe"), line)

            # Keywords
            if "keywords" in patterns:
                line = re.sub(patterns["keywords"], lambda m: stash(m, "#569cd6"), line)

            # Numbers
            if "numbers" in patterns:
                line = re.sub(patterns["numbers"], lambda m: stash(m, "#b5cea8"), line)

            result_lines.append(line)

        result = "\n".join(result_lines)

        # Restore stashed highlights
        for placeholder, html in replacements:
            result = result.replace(placeholder, html)

        return result

    @classmethod
    def _markdown_to_html(cls, text: str) -> str:
        """Convert markdown to ChatGPT-style HTML with code block headers."""
        # Preserve code blocks before escaping
        code_blocks: list[str] = []

        def stash_code_block(match):
            lang = match.group(1) or ""
            code = match.group(2).strip()
            # Escape inside code block
            code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            # Apply syntax highlighting
            highlighted = cls._highlight_code(code, lang)

            # Language display name
            display_lang = cls._LANG_NAMES.get(lang.lower(), lang.capitalize() if lang else "Code")

            # Build ChatGPT-style code block with header bar
            html = (
                # Outer container with rounded corners
                f'<div style="border-radius:8px; overflow:hidden; margin:8px 0; '
                f'border:1px solid #374151;">'
                # Header bar — dark with language label and copy icon
                f'<div style="background:#2f2f2f; padding:6px 12px; '
                f"display:flex; font-size:12px; color:#b4b4b4; "
                f'border-bottom:1px solid #374151;">'
                f'<span style="font-family:sans-serif; font-size:12px; color:#b4b4b4;">'
                f"{display_lang}</span>"
                f"</div>"
                # Code content
                f'<div style="background:#1e1e1e; padding:12px 16px; '
                f"font-family:'Consolas','Monaco','Courier New',monospace; "
                f"font-size:13px; color:#d4d4d4; line-height:155%; "
                f'white-space:pre-wrap; overflow-x:auto;">'
                f"{highlighted}</div>"
                f"</div>"
            )
            code_blocks.append(html)
            return f"\x00CODE{len(code_blocks) - 1}\x00"

        text = re.sub(r"```(\w*)\n(.*?)```", stash_code_block, text, flags=re.DOTALL)

        # Escape HTML in remaining text
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Inline code — dark pill, warm accent like Claude
        text = re.sub(
            r"`([^`]+)`",
            r'<code style="background:rgba(255,255,255,0.06); padding:2px 7px; '
            r"border-radius:6px; font-family:'Consolas','Monaco','SF Mono',monospace; "
            r'font-size:13px; color:#e06c75; border:1px solid rgba(255,255,255,0.08);">'
            r"\1</code>",
            text,
        )

        # Bold — bright white for emphasis
        text = re.sub(r"\*\*(.+?)\*\*", r'<b style="color:#f5f5f5; font-weight:600;">\1</b>', text)

        # Italic
        text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r'<i style="color:#b8b8b8;">\1</i>', text)

        # Links — underline on hover feel
        text = re.sub(
            r"\[(.+?)\]\((.+?)\)",
            r'<a href="\2" style="color:#7cacf8; text-decoration:none; '
            r'border-bottom:1px solid rgba(124,172,248,0.3);">\1</a>',
            text,
        )

        # Process line by line
        lines = text.split("\n")
        html_lines: list[str] = []
        in_list = False
        prev_empty = False

        # Table-based list items give proper hanging indent in QTextBrowser.
        # The marker goes in a fixed-width left cell, content wraps in the right cell.
        def _bullet_item(content: str) -> str:
            return (
                '<table cellspacing="0" cellpadding="0" style="margin:3px 0;">'
                '<tr>'
                '<td style="width:8px;"></td>'
                '<td style="vertical-align:top; padding-right:8px; color:#888; width:12px;">•</td>'
                f'<td style="vertical-align:top;">{content}</td>'
                '</tr></table>'
            )

        def _numbered_item(num: str, content: str) -> str:
            return (
                '<table cellspacing="0" cellpadding="0" style="margin:3px 0;">'
                '<tr>'
                '<td style="width:6px;"></td>'
                f'<td style="vertical-align:top; padding-right:6px; color:#888; '
                f'font-weight:600; width:18px; text-align:right;">{num}.</td>'
                f'<td style="vertical-align:top;">{content}</td>'
                '</tr></table>'
            )

        for line in lines:
            stripped = line.strip()

            # Empty line = paragraph break
            if not stripped:
                if not prev_empty:
                    if in_list:
                        in_list = False
                    html_lines.append('<div style="height:6px;"></div>')
                prev_empty = True
                continue
            prev_empty = False

            # Code block placeholder
            if stripped.startswith("\x00CODE") and stripped.endswith("\x00"):
                idx = int(stripped.replace("\x00CODE", "").replace("\x00", ""))
                html_lines.append(code_blocks[idx])
                in_list = False
                continue

            # Headers
            if stripped.startswith("### "):
                in_list = False
                html_lines.append(
                    f'<div style="font-size:14px; font-weight:600; '
                    f'margin:10px 0 3px 0; color:#e8e8e8;">{stripped[4:]}</div>'
                )
                continue
            if stripped.startswith("## "):
                in_list = False
                html_lines.append(
                    f'<div style="font-size:15px; font-weight:700; '
                    f'margin:12px 0 4px 0; padding-bottom:3px; '
                    f'border-bottom:1px solid rgba(255,255,255,0.08); '
                    f'color:#f0f0f0;">{stripped[3:]}</div>'
                )
                continue
            if stripped.startswith("# "):
                in_list = False
                html_lines.append(
                    f'<div style="font-size:16px; font-weight:700; '
                    f'margin:14px 0 4px 0; padding-bottom:4px; '
                    f'border-bottom:1px solid rgba(255,255,255,0.1); '
                    f'color:#ffffff;">{stripped[2:]}</div>'
                )
                continue

            # Horizontal rule
            if re.match(r"^---+$", stripped):
                in_list = False
                html_lines.append(
                    '<hr style="border:none; border-top:1px solid rgba(255,255,255,0.08); '
                    'margin:8px 0;">'
                )
                continue

            # Bullet list items — table layout for proper hanging indent
            m = re.match(r"^[-*•]\s+(.+)$", stripped)
            if m:
                in_list = True
                html_lines.append(_bullet_item(m.group(1)))
                continue

            # Numbered list items — table layout
            m = re.match(r"^(\d+)[.)]\s+(.+)$", stripped)
            if m:
                in_list = True
                html_lines.append(_numbered_item(m.group(1), m.group(2)))
                continue

            # Blockquote
            if stripped.startswith("&gt; "):
                in_list = False
                html_lines.append(
                    f'<div style="margin:4px 0; padding:3px 12px; '
                    f'border-left:3px solid rgba(124,172,248,0.4); '
                    f'color:#aaa; font-style:italic;">{stripped[5:]}</div>'
                )
                continue

            # Regular text
            in_list = False
            html_lines.append(
                f'<div style="margin:2px 0;">{stripped}</div>'
            )

        return "\n".join(html_lines)

    @property
    def content(self) -> str:
        return self._content
