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

from polyglot_ai.ui import theme_colors as tc

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
        self.setStyleSheet(f"background: {tc.get('bg_base')};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QLabel("PROFILE")
        header.setFixedHeight(28)
        header.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"letter-spacing: 0.5px; padding: 6px 12px; "
            f"background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        layout.addWidget(header)

        self._summary = QLabel("No results yet")
        self._summary.setStyleSheet(
            f"color: {tc.get('text_secondary')}; font-size: {tc.FONT_SM}px; "
            f"padding: 8px 12px; background: transparent;"
        )
        layout.addWidget(self._summary)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {tc.get('bg_base')}; }}"
            f"QScrollBar:vertical {{ width: 8px; background: transparent; }}"
            f"QScrollBar::handle:vertical {{ "
            f"background: {tc.get('scrollbar_thumb')}; border-radius: 4px; }}"
        )
        self._content = QWidget()
        self._content.setStyleSheet(f"background: {tc.get('bg_base')};")
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
            f"QWidget {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; border-radius: 4px; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)

        # Header: column name + numeric/text badge
        header_row = QHBoxLayout()
        name_lbl = QLabel(stats.name)
        name_lbl.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_MD}px; "
            f"font-weight: 600; background: transparent;"
        )
        header_row.addWidget(name_lbl)
        header_row.addStretch()
        badge = QLabel("123" if stats.is_numeric else "abc")
        badge_colour = tc.get("syn_identifier" if stats.is_numeric else "syn_decorator")
        badge.setStyleSheet(
            f"color: {badge_colour}; font-size: {tc.FONT_XS}px; font-weight: 600; "
            f"background: {tc.get('bg_base')}; border-radius: 3px; padding: 1px 6px;"
        )
        header_row.addWidget(badge)
        v.addLayout(header_row)

        # Top stats line
        top_line = (
            f"{stats.total:,} rows · {stats.distinct:,} distinct · "
            f"{stats.nulls:,} null ({stats.null_pct:.1f}%)"
        )
        top = QLabel(top_line)
        top.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
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
                f"color: {tc.get('syn_identifier')}; font-size: {tc.FONT_SM}px; "
                f"background: transparent; "
                f"font-family: 'JetBrains Mono', monospace;"
            )
            v.addWidget(num)

        # Top 5 categories for text columns
        if not stats.is_numeric and stats.top_values:
            top_label = QLabel("top values")
            top_label.setStyleSheet(
                f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; "
                f"background: transparent; margin-top: 2px;"
            )
            v.addWidget(top_label)
            for value, count in stats.top_values:
                vstr = str(value)
                if len(vstr) > 40:
                    vstr = vstr[:37] + "…"
                pct = (count / stats.total * 100) if stats.total else 0
                row_lbl = QLabel(f"  {vstr}  —  {count:,} ({pct:.1f}%)")
                row_lbl.setStyleSheet(
                    f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px; "
                    f"background: transparent;"
                )
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
