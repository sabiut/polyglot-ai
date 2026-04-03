"""Theme management — generate and apply QSS from the centralized token system."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

# Singleton instance for signal access
_instance: ThemeManager | None = None


def instance() -> ThemeManager | None:
    """Return the global ThemeManager (set during app init)."""
    return _instance


class ThemeManager(QObject):
    """Generate QSS from color tokens and apply dark/light themes."""

    theme_changed = pyqtSignal()

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        global _instance
        _instance = self
        self._app = app
        self._current_theme = "dark"

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def apply_theme(self, theme: str = "dark") -> None:
        tc.set_theme(theme)
        stylesheet = _generate_qss()
        self._app.setStyleSheet(stylesheet)
        self._current_theme = theme
        self.theme_changed.emit()
        logger.info("Applied theme: %s", theme)

    def toggle_theme(self) -> str:
        new_theme = "light" if self._current_theme == "dark" else "dark"
        self.apply_theme(new_theme)
        return new_theme


def _generate_qss() -> str:
    """Build the global QSS stylesheet from current theme tokens."""
    g = tc.get
    return f"""
/* Polyglot AI — Generated Theme ({tc.current_theme()}) */

QMainWindow {{
    background-color: {g("bg_base")};
}}

QWidget {{
    background-color: {g("bg_base")};
    color: {g("text_primary")};
    font-family: {tc.FONT_UI};
    font-size: {tc.FONT_BASE}px;
}}

/* Menu bar */
QMenuBar {{
    background-color: {g("bg_surface_raised")};
    color: {g("text_primary")};
    border-bottom: 1px solid {g("border_primary")};
    padding: 2px;
}}
QMenuBar::item:selected {{
    background-color: {g("bg_active")};
}}

QMenu {{
    background-color: {g("bg_surface_raised")};
    border: 1px solid {g("border_menu")};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 30px 6px 20px;
}}
QMenu::item:selected {{
    background-color: {g("bg_active")};
}}
QMenu::separator {{
    height: 1px;
    background-color: {g("border_menu")};
    margin: 4px 10px;
}}

/* Toolbar */
QToolBar {{
    background-color: {g("bg_surface_raised")};
    border-bottom: 1px solid {g("border_primary")};
    spacing: 4px;
    padding: 2px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {tc.RADIUS_SM}px;
    padding: 4px 8px;
    color: {g("text_primary")};
}}
QToolButton:hover {{
    background-color: {g("bg_hover")};
    border-color: {g("border_input")};
}}
QToolButton:pressed {{
    background-color: {g("bg_active")};
}}

/* Status bar */
QStatusBar {{
    background-color: {g("status_bar_bg")};
    color: {g("status_bar_fg")};
    font-size: {tc.FONT_MD}px;
}}
QStatusBar::item {{
    border: none;
}}

/* Splitter */
QSplitter::handle {{
    background-color: {g("border_primary")};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}

/* Tab widget */
QTabWidget::pane {{
    border: 1px solid {g("border_primary")};
    background-color: {g("bg_base")};
}}
QTabBar::tab {{
    background-color: {g("bg_surface_raised")};
    color: {g("text_secondary")};
    border: 1px solid {g("border_primary")};
    border-bottom: none;
    padding: 6px 28px 6px 12px;
    margin-right: 1px;
    min-width: 60px;
}}
QTabBar::tab:selected {{
    background-color: {g("bg_base")};
    color: {g("text_on_accent")};
    border-bottom: 2px solid {g("accent_primary")};
}}
QTabBar::tab:hover:!selected {{
    background-color: {g("bg_hover")};
}}

/* Scroll bars */
QScrollBar:vertical {{
    background-color: {g("scrollbar_track")};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {g("scrollbar_thumb")};
    min-height: 20px;
    border-radius: 3px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {g("scrollbar_thumb_hover")};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: {g("scrollbar_track")};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {g("scrollbar_thumb")};
    min-width: 20px;
    border-radius: 3px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {g("scrollbar_thumb_hover")};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* Tree view */
QTreeView {{
    background-color: {g("bg_surface")};
    border: none;
    outline: none;
}}
QTreeView::item {{
    padding: 4px 2px;
}}
QTreeView::item:selected {{
    background-color: {g("bg_active")};
}}
QTreeView::item:hover:!selected {{
    background-color: {g("bg_hover_subtle")};
}}
QTreeView::branch {{
    background-color: {g("bg_surface")};
}}

/* Input fields */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {g("bg_input")};
    border: 1px solid {g("border_input")};
    border-radius: {tc.RADIUS_SM}px;
    padding: 4px 8px;
    color: {g("text_primary")};
    selection-background-color: {g("bg_active")};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {g("border_focus")};
}}

/* Buttons */
QPushButton {{
    background-color: {g("accent_primary")};
    color: {g("text_on_accent")};
    border: none;
    border-radius: {tc.RADIUS_SM}px;
    padding: 6px 16px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {g("accent_primary_hover")};
}}
QPushButton:pressed {{
    background-color: {g("accent_primary_pressed")};
}}
QPushButton:disabled {{
    background-color: {g("bg_hover")};
    color: {g("text_disabled")};
}}

/* Combo box */
QComboBox {{
    background-color: {g("bg_input")};
    border: 1px solid {g("border_input")};
    border-radius: {tc.RADIUS_SM}px;
    padding: 4px 8px;
    color: {g("text_primary")};
    min-width: 100px;
}}
QComboBox:hover {{
    border-color: {g("border_focus")};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {g("bg_surface_raised")};
    border: 1px solid {g("border_menu")};
    selection-background-color: {g("bg_active")};
    color: {g("text_primary")};
}}

/* Labels */
QLabel {{
    color: {g("text_primary")};
    background: transparent;
}}

/* Group box */
QGroupBox {{
    border: 1px solid {g("border_primary")};
    border-radius: {tc.RADIUS_SM}px;
    margin-top: 8px;
    padding-top: 12px;
    color: {g("text_primary")};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    padding: 0 6px;
}}

/* Dialogs */
QDialog {{
    background-color: {g("bg_surface")};
}}

/* Tooltip */
QToolTip {{
    background-color: {g("bg_surface_raised")};
    color: {g("text_primary")};
    border: 1px solid {g("border_menu")};
    padding: 4px;
}}
"""
