"""Search sidebar panel — search across project files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.thread_bridge import run_in_thread


class SearchPanel(QWidget):
    """Search panel for finding text across project files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._search_request = 0
        self.on_file_selected = None  # callback(path, line_number)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(tc.HEADER_HEIGHT)
        header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(tc.SPACING_LG, 0, tc.SPACING_MD, 0)
        title = QLabel("SEARCH")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(header)

        # Search input
        input_area = QWidget()
        input_area.setStyleSheet(f"background: {tc.get('bg_base')};")
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(tc.SPACING_MD, tc.SPACING_MD, tc.SPACING_MD, tc.SPACING_MD)
        input_layout.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search files... (Enter)")
        self._search_input.setStyleSheet(
            f"QLineEdit {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
            f"border: 1px solid {tc.get('border_card')}; "
            f"border-radius: {tc.RADIUS_SM}px; padding: 6px 8px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {tc.get('border_focus')}; }}"
        )
        self._search_input.returnPressed.connect(self._do_search)
        input_layout.addWidget(self._search_input)

        self._result_count = QLabel("")
        self._result_count.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')}; background: transparent;"
        )
        input_layout.addWidget(self._result_count)

        layout.addWidget(input_area)

        # Results area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}"
            f"QScrollBar:vertical {{ width: 6px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ background: {tc.get('scrollbar_thumb')}; border-radius: 3px; }}"
        )

        self._results_widget = QWidget()
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(0)
        self._results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self._results_widget)
        layout.addWidget(scroll)

    def set_project_root(self, path: Path) -> None:
        self._project_root = path

    _GREP_INCLUDES = [
        f"--include=*.{ext}"
        for ext in ("py js ts html css json yaml yml toml md txt rs go java c cpp h rb sh".split())
    ]

    def _do_search(self) -> None:
        query = self._search_input.text().strip()
        if not query or not self._project_root:
            return

        self._clear_results()
        self._result_count.setText("Searching…")
        # Only the newest search may render — a slow grep on a big
        # tree must not overwrite the results of a later query.
        self._search_request += 1
        request = self._search_request
        project_root = self._project_root

        def work() -> list[str]:
            result = subprocess.run(
                ["grep", "-rn", "-F", *self._GREP_INCLUDES, "-l", query, "."],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return [f for f in result.stdout.strip().split("\n") if f]

        def done(files: list[str]) -> None:
            if request != self._search_request:
                return
            self._render_results(files, project_root)

        def failed(exc: Exception) -> None:
            if request != self._search_request:
                return
            self._result_count.setText(f"Error: {str(exc)[:50]}")

        run_in_thread(work, done, failed, owner=self, name="search-grep")

    def _clear_results(self) -> None:
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_results(self, files: list[str], project_root: Path) -> None:
        self._result_count.setText(
            f"{len(files)} file{'s' if len(files) != 1 else ''} found" if files else "No results"
        )

        for filepath in files[:50]:  # Max 50 results
            clean = filepath.lstrip("./")
            item = QWidget()
            item.setFixedHeight(26)
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.setStyleSheet(
                "QWidget { background: transparent; }"
                f"QWidget:hover {{ background: {tc.get('bg_hover_subtle')}; }}"
            )
            row = QHBoxLayout(item)
            row.setContentsMargins(tc.SPACING_LG, 0, tc.SPACING_MD, 0)
            row.setSpacing(6)

            icon = QLabel("•")
            icon.setFixedWidth(14)
            icon.setStyleSheet(f"font-size: {tc.FONT_XS}px; background: transparent;")
            row.addWidget(icon)

            label = QLabel(clean)
            label.setStyleSheet(
                f"font-size: {tc.FONT_MD}px; color: {tc.get('text_heading')}; "
                "background: transparent;"
            )
            row.addWidget(label, stretch=1)

            full_path = str(project_root / clean)
            item.mousePressEvent = lambda e, p=full_path: self._on_file_click(p)
            self._results_layout.addWidget(item)

    def _on_file_click(self, path: str) -> None:
        if self.on_file_selected:
            self.on_file_selected(path)
