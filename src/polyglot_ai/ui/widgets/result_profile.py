"""Data profiling sidebar for SQL query results.

Given a result set (columns + rows), computes per-column stats:

- row count
- null count and %
- distinct count
- min, max, mean (numeric columns)
- top 5 most common values (text columns)

The widget is purposely lightweight — all stats are computed in pure
Python from the in-memory result set, no SQL round trip required.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


@dataclass
class ColumnStats:
    """Per-column profiling stats."""

    name: str
    total: int
    nulls: int
    distinct: int
    is_numeric: bool
    min_val: Any = None
    max_val: Any = None
    mean: float | None = None
    top_values: list[tuple[Any, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.top_values is None:
            self.top_values = []

    @property
    def null_pct(self) -> float:
        return (self.nulls / self.total * 100.0) if self.total else 0.0


def _try_float(value: Any) -> float | None:
    """Best-effort numeric conversion. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def profile_column(name: str, values: list[Any]) -> ColumnStats:
    """Compute per-column stats from a Python list of cell values."""
    total = len(values)
    non_null = [v for v in values if v is not None]
    nulls = total - len(non_null)
    distinct = len({_hashable(v) for v in non_null})

    # Numeric pass — count how many cells parse as numbers. If at least
    # 80% of non-null values are numeric, treat the column as numeric.
    numeric_values: list[float] = []
    for v in non_null:
        f = _try_float(v)
        if f is not None:
            numeric_values.append(f)
    is_numeric = bool(non_null) and len(numeric_values) >= 0.8 * len(non_null)

    stats = ColumnStats(
        name=name,
        total=total,
        nulls=nulls,
        distinct=distinct,
        is_numeric=is_numeric,
    )

    if is_numeric and numeric_values:
        stats.min_val = min(numeric_values)
        stats.max_val = max(numeric_values)
        stats.mean = sum(numeric_values) / len(numeric_values)
    elif non_null:
        # Top categories for text columns
        counter = Counter(_hashable(v) for v in non_null)
        stats.top_values = counter.most_common(5)

    return stats


def _hashable(v: Any) -> Any:
    """Coerce ``v`` to a hashable representation for counting/distinct."""
    try:
        hash(v)
        return v
    except TypeError:
        return repr(v)


class ResultProfileWidget(QWidget):
    """Scrollable list of per-column stats cards."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #1e1e1e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QLabel("PROFILE")
        header.setFixedHeight(28)
        header.setStyleSheet(
            "color: #888; font-size: 11px; font-weight: 600; "
            "letter-spacing: 0.5px; padding: 6px 12px; "
            "background: #252526; border-bottom: 1px solid #333;"
        )
        layout.addWidget(header)

        self._summary = QLabel("No results yet")
        self._summary.setStyleSheet(
            "color: #aaa; font-size: 11px; padding: 8px 12px; background: transparent;"
        )
        layout.addWidget(self._summary)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #1e1e1e; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        self._content = QWidget()
        self._content.setStyleSheet("background: #1e1e1e;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 8, 8)
        self._content_layout.setSpacing(6)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        layout.addWidget(scroll, stretch=1)

    def set_results(self, columns: list[str], rows: list[tuple]) -> None:
        """Compute and render stats for a result set."""
        # Clear previous cards
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if not columns or not rows:
            self._summary.setText("No results yet")
            return

        self._summary.setText(f"{len(rows):,} rows × {len(columns)} columns")

        # Build per-column value lists
        for col_idx, col_name in enumerate(columns):
            try:
                values = [row[col_idx] for row in rows]
            except Exception as e:
                logger.warning("profile: could not extract column %s: %s", col_name, e)
                continue
            try:
                stats = profile_column(col_name, values)
            except Exception as e:
                logger.exception("profile: failed to compute stats for %s", col_name)
                stats = ColumnStats(
                    name=col_name, total=len(values), nulls=0, distinct=0, is_numeric=False
                )
                _ = e
            self._content_layout.addWidget(self._make_card(stats))

    def _make_card(self, stats: ColumnStats) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            "QWidget { background: #252526; border: 1px solid #333; border-radius: 4px; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)

        # Header: column name + numeric/text badge
        header_row = QHBoxLayout()
        name_lbl = QLabel(stats.name)
        name_lbl.setStyleSheet(
            "color: #e0e0e0; font-size: 12px; font-weight: 600; background: transparent;"
        )
        header_row.addWidget(name_lbl)
        header_row.addStretch()
        badge = QLabel("123" if stats.is_numeric else "abc")
        badge_colour = "#9cdcfe" if stats.is_numeric else "#dcdcaa"
        badge.setStyleSheet(
            f"color: {badge_colour}; font-size: 10px; font-weight: 600; "
            "background: #1e1e1e; border-radius: 3px; padding: 1px 6px;"
        )
        header_row.addWidget(badge)
        v.addLayout(header_row)

        # Top stats line
        top_line = (
            f"{stats.total:,} rows · {stats.distinct:,} distinct · "
            f"{stats.nulls:,} null ({stats.null_pct:.1f}%)"
        )
        top = QLabel(top_line)
        top.setStyleSheet("color: #888; font-size: 11px; background: transparent;")
        v.addWidget(top)

        # Numeric stats
        if stats.is_numeric and stats.min_val is not None:
            num_line = (
                f"min: {self._fmt_num(stats.min_val)} · "
                f"max: {self._fmt_num(stats.max_val)} · "
                f"mean: {self._fmt_num(stats.mean)}"
            )
            num = QLabel(num_line)
            num.setStyleSheet(
                "color: #9cdcfe; font-size: 11px; background: transparent; "
                "font-family: 'JetBrains Mono', monospace;"
            )
            v.addWidget(num)

        # Top 5 categories for text columns
        if not stats.is_numeric and stats.top_values:
            top_label = QLabel("top values")
            top_label.setStyleSheet(
                "color: #777; font-size: 10px; background: transparent; margin-top: 2px;"
            )
            v.addWidget(top_label)
            for value, count in stats.top_values:
                vstr = str(value)
                if len(vstr) > 40:
                    vstr = vstr[:37] + "…"
                pct = (count / stats.total * 100) if stats.total else 0
                row_lbl = QLabel(f"  {vstr}  —  {count:,} ({pct:.1f}%)")
                row_lbl.setStyleSheet("color: #c0c0c0; font-size: 11px; background: transparent;")
                v.addWidget(row_lbl)

        return card

    @staticmethod
    def _fmt_num(value: float | None) -> str:
        if value is None:
            return "—"
        if abs(value) >= 1e6 or (0 < abs(value) < 1e-3):
            return f"{value:.4g}"
        if value == int(value):
            return f"{int(value):,}"
        return f"{value:,.2f}"
