"""Preview tab — view images, CSV/TSV data, and PDFs inline."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
})
TABLE_EXTENSIONS = frozenset({".csv", ".tsv"})
PDF_EXTENSIONS = frozenset({".pdf"})
PREVIEW_EXTENSIONS = IMAGE_EXTENSIONS | TABLE_EXTENSIONS | PDF_EXTENSIONS


class PreviewTab(QWidget):
    """Read-only file preview for images, CSV/TSV, and PDFs."""

    def __init__(self, file_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._scale = 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Build viewers
        self._image_viewer = self._build_image_viewer()
        self._table_viewer = self._build_table_viewer()
        self._fallback_viewer = self._build_fallback_viewer()

        self._stack.addWidget(self._image_viewer)   # 0
        self._stack.addWidget(self._table_viewer)    # 1
        self._stack.addWidget(self._fallback_viewer) # 2

        # Load content
        self._load(file_path)

    def _build_image_viewer(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Zoom toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        toolbar.setStyleSheet(
            f"background: {tc.get('bg_surface')}; border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 0, 8, 0)
        tb_layout.setSpacing(4)

        btn_style = f"""
            QPushButton {{
                background: transparent; border: 1px solid {tc.get('border_card')};
                border-radius: 3px; padding: 2px 8px; color: {tc.get('text_primary')};
                font-size: {tc.FONT_MD}px;
            }}
            QPushButton:hover {{ background: {tc.get('bg_hover')}; }}
        """

        zoom_in = QPushButton("+")
        zoom_in.setObjectName("previewZoomIn")
        zoom_in.setFixedSize(28, 24)
        zoom_in.setStyleSheet(btn_style)
        zoom_in.clicked.connect(lambda: self._zoom(1.25))
        tb_layout.addWidget(zoom_in)

        zoom_out = QPushButton("−")
        zoom_out.setObjectName("previewZoomOut")
        zoom_out.setFixedSize(28, 24)
        zoom_out.setStyleSheet(btn_style)
        zoom_out.clicked.connect(lambda: self._zoom(0.8))
        tb_layout.addWidget(zoom_out)

        fit_btn = QPushButton("Fit")
        fit_btn.setObjectName("previewFit")
        fit_btn.setFixedSize(40, 24)
        fit_btn.setStyleSheet(btn_style)
        fit_btn.clicked.connect(self._fit_to_window)
        tb_layout.addWidget(fit_btn)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        tb_layout.addWidget(self._zoom_label)
        tb_layout.addStretch()

        self._size_label = QLabel("")
        self._size_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        tb_layout.addWidget(self._size_label)

        layout.addWidget(toolbar)

        # Image display
        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setStyleSheet(f"background: {tc.get('bg_base')}; border: none;")

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._image_label)
        self._scroll.setWidgetResizable(False)

        layout.addWidget(self._scroll)
        self._original_pixmap: QPixmap | None = None

        return widget

    def _build_table_viewer(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget()
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {tc.get('bg_base')}; color: {tc.get('text_primary')};
                border: none; font-size: {tc.FONT_MD}px;
                gridline-color: {tc.get('border_secondary')};
            }}
            QHeaderView::section {{
                background: {tc.get('bg_surface')}; color: {tc.get('text_heading')};
                border: 1px solid {tc.get('border_secondary')};
                padding: 4px; font-size: {tc.FONT_SM}px; font-weight: 600;
            }}
            QTableWidget::item {{ padding: 4px; }}
            QTableWidget::item:selected {{ background: {tc.get('bg_active')}; }}
        """)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        self._row_label = QLabel("")
        self._row_label.setFixedHeight(24)
        self._row_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px;"
        )
        layout.addWidget(self._row_label)

        return widget

    def _build_fallback_viewer(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        msg = QLabel("This file type cannot be previewed inline.")
        msg.setStyleSheet(
            f"color: {tc.get('text_secondary')}; font-size: {tc.FONT_BASE}px;"
        )
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)

        open_btn = QPushButton("Open with System Viewer")
        open_btn.setObjectName("previewOpenExternal")
        open_btn.setStyleSheet(f"""
            #previewOpenExternal {{
                background: {tc.get('accent_primary')}; color: {tc.get('text_on_accent')};
                border: none; border-radius: {tc.RADIUS_SM}px;
                padding: 8px 20px; font-size: {tc.FONT_BASE}px; font-weight: 600;
            }}
            #previewOpenExternal:hover {{ background: {tc.get('accent_primary_hover')}; }}
        """)
        open_btn.clicked.connect(self._open_externally)
        layout.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return widget

    def _load(self, path: Path) -> None:
        suffix = path.suffix.lower()

        if suffix in IMAGE_EXTENSIONS:
            self._load_image(path)
            self._stack.setCurrentIndex(0)
        elif suffix in TABLE_EXTENSIONS:
            self._load_table(path, delimiter="\t" if suffix == ".tsv" else ",")
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(2)

    def _load_image(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._stack.setCurrentIndex(2)
            return
        self._original_pixmap = pixmap
        self._size_label.setText(f"{pixmap.width()} × {pixmap.height()}")
        self._fit_to_window()

    def _load_table(self, path: Path, delimiter: str = ",") -> None:
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as f:
                # Try to sniff the delimiter
                try:
                    sample = f.read(8192)
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                    delimiter = dialect.delimiter
                    f.seek(0)
                except csv.Error:
                    f.seek(0)

                reader = csv.reader(f, delimiter=delimiter)
                rows = []
                for i, row in enumerate(reader):
                    rows.append(row)
                    if i >= 10_000:
                        break

            if not rows:
                return

            # First row as headers
            headers = rows[0]
            data = rows[1:]

            self._table.setColumnCount(len(headers))
            self._table.setHorizontalHeaderLabels(headers)
            self._table.setRowCount(len(data))
            self._table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )

            for r, row in enumerate(data):
                for c, val in enumerate(row):
                    self._table.setItem(r, c, QTableWidgetItem(val))

            total = len(data)
            truncated = " (showing first 10,000)" if total >= 10_000 else ""
            self._row_label.setText(f"  {total:,} rows × {len(headers)} columns{truncated}")

        except Exception as e:
            logger.exception("Failed to load table: %s", path)
            self._row_label.setText(f"  Error: {str(e)[:80]}")

    def _zoom(self, factor: float) -> None:
        self._scale *= factor
        self._scale = max(0.1, min(10.0, self._scale))
        self._apply_zoom()

    def _fit_to_window(self) -> None:
        if not self._original_pixmap:
            return
        scroll_size = self._scroll.size()
        pw = self._original_pixmap.width()
        ph = self._original_pixmap.height()
        if pw == 0 or ph == 0:
            return
        scale_w = (scroll_size.width() - 20) / pw
        scale_h = (scroll_size.height() - 20) / ph
        self._scale = min(scale_w, scale_h, 1.0)
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if not self._original_pixmap:
            return
        scaled = self._original_pixmap.scaled(
            int(self._original_pixmap.width() * self._scale),
            int(self._original_pixmap.height() * self._scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())
        self._zoom_label.setText(f"{int(self._scale * 100)}%")

    def _open_externally(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._file_path)))

    @property
    def file_path(self) -> Path:
        return self._file_path

    @property
    def is_modified(self) -> bool:
        return False
