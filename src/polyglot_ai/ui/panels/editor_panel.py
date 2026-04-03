"""Editor panel — multi-tab code editor."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui.panels.document_tab import DocumentTab
from polyglot_ai.ui.panels.editor_tab import EditorTab
from polyglot_ai.ui.panels.preview_tab import PREVIEW_EXTENSIONS, PreviewTab

logger = logging.getLogger(__name__)

MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})

# Directory to store generated icons
_ICON_DIR = None


def _ensure_close_icons() -> str:
    """Generate close button PNG files and return the directory path."""
    global _ICON_DIR
    if _ICON_DIR:
        return _ICON_DIR

    import os
    import tempfile

    _ICON_DIR = tempfile.mkdtemp(prefix="codex-icons-")

    for name, color in [("close", "#cccccc"), ("close-hover", "#ffffff")]:
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        m = 4
        painter.drawLine(m, m, size - m, size - m)
        painter.drawLine(size - m, m, m, size - m)
        painter.end()
        pixmap.save(os.path.join(_ICON_DIR, f"{name}.png"))

    return _ICON_DIR


class EditorPanel(QTabWidget):
    """Multi-tab code editor container."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self.close_tab)

        # Generate close icons and apply via QSS
        icon_dir = _ensure_close_icons()
        self.tabBar().setStyleSheet(f"""
            QTabBar::close-button {{
                image: url({icon_dir}/close.png);
                subcontrol-position: right;
                margin: 0px 4px 0px 0px;
                padding: 3px;
                border-radius: 3px;
            }}
            QTabBar::close-button:hover {{
                image: url({icon_dir}/close-hover.png);
                background-color: #c42b1c;
            }}
        """)

        self._open_tabs: dict[str, int] = {}  # abs path -> tab index

        # Placeholder when no files are open
        self._placeholder = QWidget()
        layout = QVBoxLayout(self._placeholder)
        label = QLabel("Open a file to begin editing")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #6c6c6c; font-size: 16px;")
        layout.addWidget(label)
        self.addTab(self._placeholder, "Welcome")

    def _remove_placeholder(self) -> None:
        idx = self.indexOf(self._placeholder)
        if idx >= 0:
            self.removeTab(idx)

    def _show_placeholder(self) -> None:
        if self.count() == 0:
            self.addTab(self._placeholder, "Welcome")

    def open_file(self, path: Path | None = None) -> None:
        """Open a file in a new tab (or switch to existing tab)."""
        if path is None:
            file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "All Files (*)")
            if not file_path:
                return
            path = Path(file_path)

        abs_path = str(path.resolve())

        # Switch to existing tab if already open
        if abs_path in self._open_tabs:
            self.setCurrentIndex(self._open_tabs[abs_path])
            return

        self._remove_placeholder()

        suffix = path.suffix.lower()
        if suffix in MARKDOWN_EXTENSIONS:
            tab = DocumentTab()
            tab.load(path)
        elif suffix in PREVIEW_EXTENSIONS:
            tab = PreviewTab(path)
        else:
            tab = EditorTab()
            tab.load(path)
            if hasattr(self, "_ai_provider_manager"):
                tab.set_ai_services(self._ai_provider_manager, self._ai_settings)

        index = self.addTab(tab, path.name)
        self.setCurrentIndex(index)
        self._open_tabs[abs_path] = index

        # Track modification state for editable tabs
        if isinstance(tab, EditorTab):
            tab.editor.modificationChanged.connect(
                lambda _: self._update_tab_title(self.indexOf(tab))
            )
        elif isinstance(tab, DocumentTab):
            tab.source_editor.textChanged.connect(lambda: self._update_tab_title(self.indexOf(tab)))

        self._refresh_tab_map()

    def new_file(self) -> None:
        """Create a new untitled file tab."""
        self._remove_placeholder()
        tab = EditorTab()
        count = sum(1 for i in range(self.count()) if self.tabText(i).startswith("Untitled"))
        name = f"Untitled-{count + 1}"
        index = self.addTab(tab, name)
        self.setCurrentIndex(index)

    def save_current(self) -> bool:
        """Save the current tab's file."""
        tab = self.currentWidget()
        if not hasattr(tab, "save"):
            return False

        if hasattr(tab, "file_path") and tab.file_path is None:
            if isinstance(tab, EditorTab):
                return self._save_as(tab)
            return False

        if tab.save():
            self._update_tab_title(self.currentIndex())
            return True
        return False

    def save_all(self) -> None:
        """Save all modified tabs."""
        for i in range(self.count()):
            tab = self.widget(i)
            if hasattr(tab, "is_modified") and hasattr(tab, "save") and tab.is_modified:
                if tab.file_path is None:
                    self.setCurrentIndex(i)
                    self._save_as(tab)
                else:
                    tab.save()
                    self._update_tab_title(i)

    def _save_as(self, tab: EditorTab) -> bool:
        file_path, _ = QFileDialog.getSaveFileName(self, "Save File", "", "All Files (*)")
        if not file_path:
            return False
        path = Path(file_path)
        tab.file_path = path
        if tab.save():
            idx = self.indexOf(tab)
            self.setTabText(idx, path.name)
            self._open_tabs[str(path.resolve())] = idx
            return True
        return False

    def close_tab(self, index: int) -> None:
        """Close tab at index, prompting to save if modified."""
        widget = self.widget(index)
        if widget is self._placeholder:
            return

        if isinstance(widget, EditorTab) and widget.is_modified:
            name = self.tabText(index).rstrip(" *")
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Save changes to {name}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Save:
                self.setCurrentIndex(index)
                if not self.save_current():
                    return

        # Remove from tracking
        if isinstance(widget, EditorTab) and widget.file_path:
            self._open_tabs.pop(str(widget.file_path.resolve()), None)

        self.removeTab(index)
        widget.deleteLater()
        self._refresh_tab_map()
        self._show_placeholder()

    def _update_tab_title(self, index: int) -> None:
        tab = self.widget(index)
        if hasattr(tab, "file_path"):
            name = tab.file_path.name if tab.file_path else "Untitled"
            if hasattr(tab, "is_modified") and tab.is_modified:
                name = f"● {name}"
            self.setTabText(index, name)

    def _refresh_tab_map(self) -> None:
        self._open_tabs.clear()
        for i in range(self.count()):
            tab = self.widget(i)
            if hasattr(tab, "file_path") and tab.file_path:
                self._open_tabs[str(tab.file_path.resolve())] = i

    def get_current_tab(self) -> QWidget | None:
        """Return current tab (EditorTab, DocumentTab, PreviewTab, or None)."""
        tab = self.currentWidget()
        if hasattr(tab, "file_path"):
            return tab
        return None

    def set_ai_services(self, provider_manager, settings) -> None:
        """Pass AI services to all current and future tabs for inline completions."""
        self._ai_provider_manager = provider_manager
        self._ai_settings = settings
        # Update existing tabs
        for i in range(self.count()):
            tab = self.widget(i)
            if isinstance(tab, EditorTab):
                tab.set_ai_services(provider_manager, settings)
