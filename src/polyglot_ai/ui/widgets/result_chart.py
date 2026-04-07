"""Chart visualisation for SQL query results.

Renders a result set as a bar / line / scatter / histogram chart using
PyQt6's QtCharts module (no matplotlib dependency). The user picks
which column is X and which is Y from dropdowns; the widget figures
out a sensible default automatically.
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


CHART_KINDS = ("bar", "line", "scatter", "histogram")


def _try_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_numeric_column(values: list[Any]) -> bool:
    """True if at least 80% of non-null values parse as numbers."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return False
    numeric = sum(1 for v in non_null if _try_float(v) is not None)
    return numeric >= 0.8 * len(non_null)


class ResultChartWidget(QWidget):
    """Renders a result set as a chart with X/Y dropdowns and a kind selector."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns: list[str] = []
        self._rows: list[tuple] = []
        self._suspend_signals = False

        self.setStyleSheet("background: #1e1e1e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header / control bar
        bar = QWidget()
        bar.setFixedHeight(34)
        bar.setStyleSheet("background: #252526; border-bottom: 1px solid #333;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 0, 10, 0)
        bl.setSpacing(8)

        bl.addWidget(self._mk_label("Type"))
        self._kind = QComboBox()
        self._kind.addItems(["Bar", "Line", "Scatter", "Histogram"])
        self._kind.setStyleSheet(self._combo_style())
        self._kind.currentIndexChanged.connect(self._render)
        bl.addWidget(self._kind)

        bl.addWidget(self._mk_label("X"))
        self._x_combo = QComboBox()
        self._x_combo.setStyleSheet(self._combo_style())
        self._x_combo.currentIndexChanged.connect(self._render)
        bl.addWidget(self._x_combo, stretch=1)

        bl.addWidget(self._mk_label("Y"))
        self._y_combo = QComboBox()
        self._y_combo.setStyleSheet(self._combo_style())
        self._y_combo.currentIndexChanged.connect(self._render)
        bl.addWidget(self._y_combo, stretch=1)

        layout.addWidget(bar)

        # The chart view itself
        self._view = QChartView()
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setBackgroundBrush(QColor("#181818"))
        self._view.setStyleSheet("background: #181818; border: none;")
        layout.addWidget(self._view, stretch=1)

        # Empty placeholder
        self._placeholder = QLabel(
            "Run a query, then switch to chart view.\nPick the X and Y columns to visualise."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #777; font-size: 12px; padding: 30px; background: #181818;"
        )
        layout.addWidget(self._placeholder)
        self._view.hide()

    @staticmethod
    def _mk_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #888; font-size: 11px; font-weight: 600; background: transparent;"
        )
        return lbl

    @staticmethod
    def _combo_style() -> str:
        # Reuse the same chevron-arrow style as the rest of the database
        # panel so all dropdowns look identical.
        from polyglot_ai.ui.panels.database_panel import _combo_dropdown_style

        return _combo_dropdown_style()

    # ── Public API ──────────────────────────────────────────────────

    def set_results(self, columns: list[str], rows: list[tuple]) -> None:
        self._columns = list(columns)
        self._rows = list(rows)

        if not columns or not rows:
            self._placeholder.show()
            self._view.hide()
            return

        # Auto-pick sensible defaults: first column for X, first numeric
        # column for Y. Block signals to avoid re-rendering twice.
        self._suspend_signals = True
        try:
            self._x_combo.clear()
            self._y_combo.clear()
            self._x_combo.addItems(columns)
            self._y_combo.addItems(columns)
            # Pick the first numeric column for Y if any.
            for i, col in enumerate(columns):
                values = [r[i] for r in rows]
                if _is_numeric_column(values):
                    self._y_combo.setCurrentIndex(i)
                    break
        finally:
            self._suspend_signals = False

        self._placeholder.hide()
        self._view.show()
        self._render()

    # ── Rendering ───────────────────────────────────────────────────

    def _render(self) -> None:
        if self._suspend_signals or not self._columns or not self._rows:
            return
        kind = self._kind.currentText().lower()
        x_idx = self._x_combo.currentIndex()
        y_idx = self._y_combo.currentIndex()
        if x_idx < 0 or y_idx < 0:
            return

        try:
            chart = self._build_chart(kind, x_idx, y_idx)
        except Exception:
            logger.exception("result_chart: failed to render %s chart", kind)
            return
        self._view.setChart(chart)

    def _build_chart(self, kind: str, x_idx: int, y_idx: int) -> QChart:
        chart = QChart()
        chart.setBackgroundBrush(QColor("#181818"))
        chart.setBackgroundPen(QColor("#181818"))
        chart.setTitleBrush(QColor("#e0e0e0"))
        chart.legend().setLabelColor(QColor("#cccccc"))

        x_name = self._columns[x_idx]
        y_name = self._columns[y_idx]
        chart.setTitle(f"{y_name} by {x_name}")

        x_values = [r[x_idx] for r in self._rows]
        y_values = [r[y_idx] for r in self._rows]
        y_floats = [_try_float(v) for v in y_values]

        # Limit cardinality so we don't melt the chart with 100k bars.
        max_points = 200
        if len(self._rows) > max_points and kind in ("bar", "line", "scatter"):
            x_values = x_values[:max_points]
            y_floats = y_floats[:max_points]
            chart.setTitle(f"{y_name} by {x_name}  (first {max_points} rows)")

        if kind == "bar":
            self._build_bar(chart, x_values, y_floats, y_name)
        elif kind == "line":
            self._build_line(chart, x_values, y_floats, x_name, y_name)
        elif kind == "scatter":
            self._build_scatter(chart, x_values, y_floats, x_name, y_name)
        elif kind == "histogram":
            self._build_histogram(chart, y_floats, y_name)
        return chart

    def _build_bar(
        self,
        chart: QChart,
        x_values: list[Any],
        y_floats: list[float | None],
        y_name: str,
    ) -> None:
        bar_set = QBarSet(y_name)
        bar_set.setColor(QColor("#4ec9b0"))
        bar_set.setLabelColor(QColor("#e0e0e0"))
        for v in y_floats:
            bar_set.append(v if v is not None else 0.0)
        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([str(v) for v in x_values])
        axis_x.setLabelsColor(QColor("#aaa"))
        axis_x.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelsColor(QColor("#aaa"))
        axis_y.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)

    def _build_line(
        self,
        chart: QChart,
        x_values: list[Any],
        y_floats: list[float | None],
        x_name: str,
        y_name: str,
    ) -> None:
        series = QLineSeries()
        series.setName(y_name)
        series.setColor(QColor("#9cdcfe"))
        for i, y in enumerate(y_floats):
            if y is None:
                continue
            x = _try_float(x_values[i])
            series.append(x if x is not None else float(i), y)
        chart.addSeries(series)
        self._attach_xy_axes(chart, series, x_name, y_name)

    def _build_scatter(
        self,
        chart: QChart,
        x_values: list[Any],
        y_floats: list[float | None],
        x_name: str,
        y_name: str,
    ) -> None:
        series = QScatterSeries()
        series.setName(y_name)
        series.setColor(QColor("#e5a00d"))
        series.setMarkerSize(8.0)
        for i, y in enumerate(y_floats):
            if y is None:
                continue
            x = _try_float(x_values[i])
            series.append(x if x is not None else float(i), y)
        chart.addSeries(series)
        self._attach_xy_axes(chart, series, x_name, y_name)

    def _build_histogram(
        self,
        chart: QChart,
        y_floats: list[float | None],
        y_name: str,
    ) -> None:
        clean = [v for v in y_floats if v is not None]
        if not clean:
            chart.setTitle(f"{y_name}: no numeric values")
            return

        # Sturges' formula for bin count
        from math import ceil, log2

        bin_count = max(5, ceil(log2(len(clean)) + 1))
        lo, hi = min(clean), max(clean)
        if lo == hi:
            hi = lo + 1.0
        width = (hi - lo) / bin_count
        bins = [0] * bin_count
        for v in clean:
            idx = min(int((v - lo) / width), bin_count - 1)
            bins[idx] += 1

        bar_set = QBarSet(y_name)
        bar_set.setColor(QColor("#c586c0"))
        for c in bins:
            bar_set.append(c)
        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([f"{lo + i * width:.2g}" for i in range(bin_count)])
        axis_x.setLabelsColor(QColor("#aaa"))
        axis_x.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelsColor(QColor("#aaa"))
        axis_y.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)

    @staticmethod
    def _attach_xy_axes(chart: QChart, series: Any, x_name: str, y_name: str) -> None:
        axis_x = QValueAxis()
        axis_x.setTitleText(x_name)
        axis_x.setTitleBrush(QColor("#aaa"))
        axis_x.setLabelsColor(QColor("#aaa"))
        axis_x.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText(y_name)
        axis_y.setTitleBrush(QColor("#aaa"))
        axis_y.setLabelsColor(QColor("#aaa"))
        axis_y.setGridLineColor(QColor("#2a2a2a"))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)
