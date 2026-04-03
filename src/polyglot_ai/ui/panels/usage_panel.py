"""Token usage and cost tracking dashboard."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.constants import MODEL_COSTS
from polyglot_ai.ui import theme_colors as tc

if TYPE_CHECKING:
    from polyglot_ai.core.database import Database

logger = logging.getLogger(__name__)


class BarChartWidget(QWidget):
    """Simple bar chart showing daily token usage."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []  # [{date, tokens_in, tokens_out}]
        self.setMinimumHeight(120)

    def set_data(self, data: list[dict]) -> None:
        self._data = data[-14:]  # Last 14 days
        self.update()

    def paintEvent(self, event) -> None:
        if not self._data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        margin = 40
        chart_w = w - margin * 2
        chart_h = h - margin * 2

        if chart_w <= 0 or chart_h <= 0:
            painter.end()
            return

        max_val = max((d.get("tokens_in", 0) + d.get("tokens_out", 0)) for d in self._data) or 1

        bar_count = len(self._data)
        bar_width = max(8, chart_w // (bar_count * 2))
        spacing = max(4, (chart_w - bar_width * bar_count) // max(bar_count, 1))

        for i, entry in enumerate(self._data):
            x = margin + i * (bar_width + spacing)
            tokens_in = entry.get("tokens_in", 0)
            tokens_out = entry.get("tokens_out", 0)

            # Input bar (blue)
            h_in = int(chart_h * tokens_in / max_val) if max_val else 0
            painter.fillRect(
                QRectF(x, margin + chart_h - h_in, bar_width / 2, h_in),
                QColor(tc.get("accent_info")),
            )

            # Output bar (teal)
            h_out = int(chart_h * tokens_out / max_val) if max_val else 0
            painter.fillRect(
                QRectF(x + bar_width / 2, margin + chart_h - h_out, bar_width / 2, h_out),
                QColor(tc.get("accent_success_muted")),
            )

            # Date label
            date_str = entry.get("date", "")[-5:]  # MM-DD
            painter.setPen(QColor(tc.get("text_muted")))
            painter.setFont(QFont("sans-serif", 8))
            painter.drawText(
                QRectF(x - 4, h - margin + 4, bar_width + 8, 16),
                Qt.AlignmentFlag.AlignCenter,
                date_str,
            )

        # Legend
        painter.setPen(QColor(tc.get("text_tertiary")))
        painter.setFont(QFont("sans-serif", 9))
        painter.fillRect(QRectF(margin, 4, 10, 10), QColor(tc.get("accent_info")))
        painter.drawText(margin + 14, 13, "Input")
        painter.fillRect(QRectF(margin + 60, 4, 10, 10), QColor(tc.get("accent_success_muted")))
        painter.drawText(margin + 74, 13, "Output")

        painter.end()


class UsagePanel(QWidget):
    """Token usage dashboard with per-model table and daily chart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db: Database | None = None
        self._setup_ui()

    def set_database(self, db: Database) -> None:
        self._db = db

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Header
        header = QLabel("TOKEN USAGE & COST ESTIMATES")
        header.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; letter-spacing: 0.5px;"
        )
        layout.addWidget(header)

        # Summary cards
        cards = QWidget()
        cards_layout = QHBoxLayout(cards)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(12)

        self._total_in_card = self._make_card("Tokens In", "0")
        self._total_out_card = self._make_card("Tokens Out", "0")
        self._cost_card = self._make_card("Est. Cost", "$0.00")
        cards_layout.addWidget(self._total_in_card)
        cards_layout.addWidget(self._total_out_card)
        cards_layout.addWidget(self._cost_card)
        layout.addWidget(cards)

        # Per-model table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Model", "Tokens In", "Tokens Out", "Messages", "Est. Cost"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {tc.get("bg_base")}; color: {tc.get("text_primary")};
                border: 1px solid {tc.get("border_secondary")};
                font-size: {tc.FONT_MD}px; gridline-color: {tc.get("border_secondary")};
            }}
            QHeaderView::section {{
                background: {tc.get("bg_surface")}; color: {tc.get("text_tertiary")};
                border: 1px solid {tc.get("border_secondary")};
                padding: 4px; font-size: {tc.FONT_SM}px; font-weight: 600;
            }}
            QTableWidget::item {{ padding: 4px; }}
            QTableWidget::item:selected {{ background: {tc.get("bg_active")}; }}
        """)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        # Daily chart
        chart_label = QLabel("Daily Usage (Last 14 Days)")
        chart_label.setStyleSheet(f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')};")
        layout.addWidget(chart_label)

        self._chart = BarChartWidget()
        self._chart.setStyleSheet(
            f"background: {tc.get('bg_base')}; border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: {tc.RADIUS_SM}px;"
        )
        layout.addWidget(self._chart)

    def _make_card(self, title: str, value: str) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            f"background: {tc.get('bg_surface')}; border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: {tc.RADIUS_MD}px; padding: {tc.SPACING_MD}px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_tertiary')}; background: transparent; border: none;"
        )
        card_layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setObjectName("cardValue")
        value_label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {tc.get('text_heading')}; background: transparent; border: none;"
        )
        card_layout.addWidget(value_label)

        return card

    def refresh(self) -> None:
        """Refresh usage data from database."""
        if self._db:
            asyncio.ensure_future(self._do_refresh())

    async def _do_refresh(self) -> None:
        if not self._db:
            return

        # Per-model aggregates
        rows = await self._db.fetchall(
            """SELECT model, SUM(tokens_in) as total_in, SUM(tokens_out) as total_out,
                      COUNT(*) as msg_count
               FROM messages WHERE tokens_in IS NOT NULL
               GROUP BY model ORDER BY total_in DESC"""
        )

        self._table.setRowCount(len(rows))
        grand_in = 0
        grand_out = 0
        grand_cost = 0.0

        for i, row in enumerate(rows):
            model = row["model"] or "unknown"
            t_in = row["total_in"] or 0
            t_out = row["total_out"] or 0
            msg_count = row["msg_count"] or 0

            # Calculate cost
            costs = MODEL_COSTS.get(model, {"input": 0.002, "output": 0.006})
            cost = (t_in / 1000) * costs["input"] + (t_out / 1000) * costs["output"]

            self._table.setItem(i, 0, QTableWidgetItem(model))
            self._table.setItem(i, 1, QTableWidgetItem(f"{t_in:,}"))
            self._table.setItem(i, 2, QTableWidgetItem(f"{t_out:,}"))
            self._table.setItem(i, 3, QTableWidgetItem(str(msg_count)))
            self._table.setItem(i, 4, QTableWidgetItem(f"${cost:.4f}"))

            grand_in += t_in
            grand_out += t_out
            grand_cost += cost

        # Update summary cards
        self._update_card(self._total_in_card, f"{grand_in:,}")
        self._update_card(self._total_out_card, f"{grand_out:,}")
        self._update_card(self._cost_card, f"${grand_cost:.4f}")

        # Daily data for chart
        daily = await self._db.fetchall(
            """SELECT DATE(created_at) as date,
                      SUM(tokens_in) as tokens_in, SUM(tokens_out) as tokens_out
               FROM messages WHERE tokens_in IS NOT NULL
               GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 14"""
        )
        daily.reverse()  # Oldest first
        self._chart.set_data([dict(d) for d in daily])

    def _update_card(self, card: QWidget, value: str) -> None:
        label = card.findChild(QLabel, "cardValue")
        if label:
            label.setText(value)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
