"""Chat input widget and @mention popup.

Extracted from ``chat_panel.py``. Both classes are self-contained —
they communicate back to the panel purely through Qt signals
(``submit_requested``, ``file_dropped``, ``image_pasted``), so the
panel does not need to reach into their internals.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QListWidget, QTextEdit, QVBoxLayout, QWidget


class FileMentionPopup(QWidget):
    """Popup for @file mention fuzzy search."""

    file_selected = None  # Set by ChatInput to its insert-mention callback

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("""
            QWidget { background: #2d2d2d; border: 1px solid #555; border-radius: 6px; }
            QListWidget { background: transparent; border: none; color: #d4d4d4;
                          font-size: 13px; font-family: monospace; }
            QListWidget::item { padding: 4px 8px; border-radius: 3px; }
            QListWidget::item:selected { background: #094771; }
            QListWidget::item:hover { background: #3e3e40; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._list = QListWidget()
        self._list.setMaximumHeight(200)
        self._list.itemActivated.connect(self._on_select)
        layout.addWidget(self._list)
        self._files: list[str] = []

    def set_files(self, files: list[str]) -> None:
        self._files = files

    def update_filter(self, query: str) -> None:
        self._list.clear()
        q = query.lower()
        matches = [f for f in self._files if q in f.lower()][:15]
        for m in matches:
            self._list.addItem(m)
        if matches:
            self._list.setCurrentRow(0)

    def _on_select(self, item) -> None:
        if self.file_selected:
            self.file_selected(item.text())
        self.hide()

    def select_current(self) -> None:
        item = self._list.currentItem()
        if item:
            self._on_select(item)

    def move_selection(self, delta: int) -> None:
        row = self._list.currentRow() + delta
        row = max(0, min(row, self._list.count() - 1))
        self._list.setCurrentRow(row)


class ChatInput(QTextEdit):
    """Text input with Enter-to-send, drag-drop files, clipboard paste, and @mention."""

    submit_requested = pyqtSignal()
    file_dropped = pyqtSignal(str)
    image_pasted = pyqtSignal(QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._mention_popup = FileMentionPopup(self)
        self._mention_popup.file_selected = self._insert_mention
        self._mention_start = -1  # cursor pos where @ was typed
        self._project_files: list[str] = []

    def set_project_files(self, files: list[str]) -> None:
        """Update available files for @mention."""
        self._project_files = files
        self._mention_popup.set_files(files)

    def keyPressEvent(self, event):
        # Handle mention popup navigation
        if self._mention_popup.isVisible():
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._mention_popup.select_current()
                return
            if event.key() == Qt.Key.Key_Escape:
                self._mention_popup.hide()
                return
            if event.key() == Qt.Key.Key_Down:
                self._mention_popup.move_selection(1)
                return
            if event.key() == Qt.Key.Key_Up:
                self._mention_popup.move_selection(-1)
                return
            if event.key() == Qt.Key.Key_Tab:
                self._mention_popup.select_current()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submit_requested.emit()
            return

        super().keyPressEvent(event)

        # Check for @ trigger
        text = self.toPlainText()
        cursor_pos = self.textCursor().position()
        if event.text() == "@" and self._project_files:
            self._mention_start = cursor_pos
            self._show_mention_popup("")
        elif self._mention_popup.isVisible() and self._mention_start >= 0:
            # Update filter as user types after @
            if cursor_pos > self._mention_start:
                query = text[self._mention_start : cursor_pos]
                self._show_mention_popup(query)
            else:
                self._mention_popup.hide()

    def _show_mention_popup(self, query: str) -> None:
        self._mention_popup.update_filter(query)
        # Position above the cursor
        cursor_rect = self.cursorRect()
        pos = self.mapToGlobal(cursor_rect.topLeft())
        self._mention_popup.setFixedWidth(min(400, self.width()))
        self._mention_popup.move(pos.x(), pos.y() - self._mention_popup.sizeHint().height() - 4)
        self._mention_popup.show()

    def _insert_mention(self, filepath: str) -> None:
        """Replace @query with @filepath."""
        cursor = self.textCursor()
        # Select from @ to current position
        cursor.setPosition(self._mention_start - 1)  # -1 for the @ char
        cursor.setPosition(
            cursor.position() + (self.textCursor().position() - self._mention_start + 1),
            cursor.MoveMode.KeepAnchor,
        )
        cursor.insertText(f"@{filepath} ")
        self.setTextCursor(cursor)
        self._mention_start = -1

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or source.hasText()

    def insertFromMimeData(self, source):
        """Handle paste — images from clipboard, files from drag."""
        if source.hasImage():
            image = source.imageData()
            if image:
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    self.image_pasted.emit(pixmap)
                    return

        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    self.file_dropped.emit(url.toLocalFile())
            return

        # Fall back to text
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    self.file_dropped.emit(url.toLocalFile())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
