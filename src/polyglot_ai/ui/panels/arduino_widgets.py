"""The Arduino panel's own small widgets: starter tile, problem card, plot.

Kept apart from ``arduino_panel.py`` so each can be built and tested by
itself, and so the panel reads as the flow it is.
"""

from __future__ import annotations

import sys
from collections import deque

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.arduino import diagnose
from polyglot_ai.core.arduino.diagnose import Problem
from polyglot_ai.ui import theme_colors as tc

# ── A row that wraps ───────────────────────────────────────────────


class FlowLayout(QLayout):
    """Widgets side by side, continuing on the next line when out of room.

    The panel is shown in windows from half a small laptop screen
    upwards. Rows of buttons in a plain horizontal layout set a
    minimum width for the whole window, and past it were cut off.
    """

    def __init__(self, spacing: int = 8) -> None:
        super().__init__()
        self._items: list = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 — Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 — Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 — Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 — Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt override
        return self._arrange(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 — Qt override
        super().setGeometry(rect)
        self._arrange(rect, place=True)

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 — Qt override
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _visible(self) -> list:
        return [item for item in self._items if not item.isEmpty()]

    def _arrange(self, rect: QRect, *, place: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        line: list[tuple] = []

        def finish(line: list[tuple], height: int) -> None:
            # Centre each widget on its line, so a label sits level
            # with the taller button beside it.
            for item, left, top, size in line:
                item.setGeometry(QRect(QPoint(left, top + (height - size.height()) // 2), size))

        for item in self._visible():
            size = item.sizeHint()
            if line and x + size.width() > area.right() + 1:
                if place:
                    finish(line, line_height)
                x, y, line_height, line = area.x(), y + line_height + self._gap, 0, []
            line.append((item, x, y, size))
            x += size.width() + self._gap
            line_height = max(line_height, size.height())
        if place:
            finish(line, line_height)
        return y + line_height - rect.y() + margins.bottom()


# ── Starter tile ───────────────────────────────────────────────────


class StarterTile(QFrame):
    """A starter project to click: its name over one line about it.

    A frame rather than a button with labels laid over it: a button
    sizes itself from its (empty) text and takes no notice of a layout
    inside it, so a wrapped description only fitted when something
    else happened to stretch the row.
    """

    clicked = pyqtSignal()

    def __init__(self, name: str, blurb: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("starterTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Start with {name}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(
            f"#starterTile {{ background: {tc.get('bg_surface_raised')}; "
            f"border: 1px solid {tc.get('border_subtle')}; border-radius: 6px; }}"
            f"#starterTile:hover, #starterTile:focus {{ background: {tc.get('bg_hover')}; "
            f"border-color: {tc.get('accent_primary')}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        title = QLabel(name)
        title.setWordWrap(True)
        title.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_BASE}px; "
            "font-weight: 600; background: transparent; border: none;"
        )
        layout.addWidget(title)
        if blurb:
            about = QLabel(blurb)
            about.setWordWrap(True)
            about.setStyleSheet(
                f"color: {tc.get('text_secondary')}; font-size: {tc.FONT_XS}px; "
                "background: transparent; border: none;"
            )
            layout.addWidget(about)
        layout.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


# ── Problem card ───────────────────────────────────────────────────

#: A heading for each kind of failure. The sentence about it is already
#: in the feed; this names it at a glance.
_HEADINGS = {
    diagnose.CODE: "A mistake in your code",
    diagnose.PLATFORM: "Board support isn't installed",
    diagnose.LIBRARY: "A library is missing",
    diagnose.TIMEOUT: "It took too long",
    diagnose.SKETCH: "The folder isn't set up as a sketch",
    diagnose.PORT_BUSY: "The board's connection is in use",
    diagnose.PERMISSION: "No permission to use the board",
    diagnose.NO_ANSWER: "The board didn't answer",
    diagnose.UNPLUGGED: "The board isn't connected",
    diagnose.UNKNOWN: "Something went wrong",
}


class ProblemCard(QFrame):
    """What went wrong, in the tool's own words, and the way out of it.

    Shown under the feed after a failure. The buttons depend on the
    kind of problem: go to the line for a mistake in the code, install
    for a missing board package or library.
    """

    goto_requested = pyqtSignal()
    install_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("problemCard")
        self.setStyleSheet(
            f"#problemCard {{ background: {tc.get('bg_surface_raised')}; "
            f"border: 1px solid {tc.get('accent_danger')}; border-radius: 6px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._heading = QLabel()
        self._heading.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_BASE}px; "
            "font-weight: 700; background: transparent;"
        )
        layout.addWidget(self._heading)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._detail.setFont(mono)
        self._detail.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        layout.addWidget(self._detail)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.goto_button = QPushButton()
        self.goto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.goto_button.clicked.connect(self.goto_requested)
        row.addWidget(self.goto_button)
        self.install_button = QPushButton()
        self.install_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_button.clicked.connect(self.install_requested)
        row.addWidget(self.install_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.hide()

    def set_button_styles(self, primary: str, secondary: str) -> None:
        self.install_button.setStyleSheet(primary)
        self.goto_button.setStyleSheet(secondary)

    def show_problem(self, problem: Problem | None, *, board_name: str = "") -> None:
        if problem is None:
            self.hide()
            return
        self._heading.setText(_HEADINGS.get(problem.kind, _HEADINGS[diagnose.UNKNOWN]))
        said = problem.detail
        if problem.where and said:
            said = f"{problem.where}:  {said}"
        self._detail.setText(said or problem.where)
        self._detail.setVisible(bool(said or problem.where))

        self.goto_button.setVisible(bool(problem.file and problem.line))
        self.goto_button.setText(f"Go to line {problem.line}")

        if problem.kind == diagnose.PLATFORM and problem.platform:
            self.install_button.setText(
                f"Install support for {board_name}…" if board_name else "Install board support…"
            )
            self.install_button.setVisible(True)
        elif problem.kind == diagnose.LIBRARY and problem.header:
            self.install_button.setText(f"Install the library for {problem.header}…")
            self.install_button.setVisible(True)
        else:
            self.install_button.setVisible(False)
        self.show()


# ── Serial plot ────────────────────────────────────────────────────

#: Series colours, in the order series first appear. A series keeps its
#: colour for as long as it is on the plot: it goes with the name, not
#: with the position in the latest line. Checked for colour-blind
#: separation and contrast on both themes' plot surfaces.
_SERIES_DARK = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
)
_SERIES_LIGHT = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)
#: How many readings stay on the plot.
PLOT_WINDOW = 300


def _is_dark(colour: str) -> bool:
    return QColor(colour).lightness() < 128


def _tidy(value: float) -> str:
    """A number short enough for an axis: 21.5, 1024, 3.2e+06."""
    if abs(value) < 1e6 and value == int(value):
        return str(int(value))
    return f"{value:.3g}"


def _share(value: float, low: float, high: float) -> float:
    """Where ``value`` sits between ``low`` and ``high``, as 0…1.

    Halved before subtracting, like the scale itself, so the largest
    numbers a float can hold don't overflow on the way.
    """
    return (value / 2 - low / 2) / (high / 2 - low / 2)


class SerialPlot(QWidget):
    """The numbers a board prints, as lines over the last readings.

    One line per series, all on one scale. Point at the plot to read
    the values at that moment; otherwise the legend shows the latest.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        #: One dict per reading: series name → value.
        self._rows: deque[dict[str, float]] = deque(maxlen=PLOT_WINDOW)
        #: Names in the order they first appeared — which fixes the colour.
        self._names: list[str] = []
        self._hover_x: float | None = None

    # ── Data ──

    def add_sample(self, values: list[tuple[str, float]]) -> None:
        row: dict[str, float] = {}
        for position, (name, value) in enumerate(values):
            name = name or (f"value {position + 1}" if len(values) > 1 else "value")
            row[name] = value
            if name not in self._names:
                self._names.append(name)
        self._rows.append(row)
        self.update()

    def clear(self) -> None:
        self._rows.clear()
        self._names.clear()
        self._hover_x = None
        self.update()

    def series_names(self) -> list[str]:
        """The series on the plot, in colour order."""
        return [name for name in self._names if any(name in row for row in self._rows)]

    def colour_for(self, name: str) -> str:
        palette = _SERIES_DARK if _is_dark(tc.get("bg_base")) else _SERIES_LIGHT
        return palette[self._names.index(name) % len(palette)]

    def value_range(self) -> tuple[float, float]:
        """The scale's bottom and top: the data, with a little room."""
        seen = [value for row in self._rows for value in row.values()]
        if not seen:
            return 0.0, 1.0
        low, high = min(seen), max(seen)
        room = (high - low) * 0.06
        if room <= 0:
            # A flat line. One either side — or, for numbers so large
            # that adding one changes nothing (1e20 + 1 == 1e20), a
            # share of the number itself.
            room = max(1.0, abs(low) * 0.05)
        # Kept to numbers a float can hold: at the far ends of the
        # range the room itself overflows.
        limit = sys.float_info.max
        return max(low - room, -limit), min(high + room, limit)

    def height_share(self, value: float) -> float:
        """How far up the scale ``value`` is: 0 at the bottom, 1 at the top."""
        return _share(value, *self.value_range())

    def readout(self) -> tuple[int, dict[str, float]]:
        """``(readings ago, values)`` for what the legend shows.

        The reading under the pointer, or the latest one.
        """
        if not self._rows:
            return 0, {}
        index = len(self._rows) - 1
        plot = self._plot_rect()
        if self._hover_x is not None and plot.width() > 0 and len(self._rows) > 1:
            share = (self._hover_x - plot.left()) / plot.width()
            index = round(min(1.0, max(0.0, share)) * (len(self._rows) - 1))
        return len(self._rows) - 1 - index, dict(self._rows[index])

    # ── Pointer ──

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._hover_x = event.position().x()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._hover_x = None
        self.update()

    # ── Drawing ──

    _LEGEND_HEIGHT = 24
    _AXIS_WIDTH = 46

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self._AXIS_WIDTH,
            self._LEGEND_HEIGHT + 6,
            max(0, self.width() - self._AXIS_WIDTH - 10),
            max(0, self.height() - self._LEGEND_HEIGHT - 6 - 20),
        )

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        surface = QColor(tc.get("bg_base"))
        painter.fillRect(self.rect(), surface)
        painter.setPen(QPen(QColor(tc.get("border_secondary")), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        font = QFont(self.font())
        font.setPixelSize(tc.FONT_XS)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        muted = QColor(tc.get("text_muted"))
        secondary = QColor(tc.get("text_secondary"))

        names = self.series_names()
        if not names:
            painter.setPen(muted)
            painter.drawText(
                QRectF(self.rect()).adjusted(16, 8, -16, -8),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                "Numbers your board prints show up here as a graph.\n"
                "Print one per line, or several separated by commas "
                "— for example  temp:21.5,humidity:40",
            )
            return

        plot = self._plot_rect()
        low, high = self.value_range()

        def y_of(value: float) -> float:
            return plot.bottom() - _share(value, low, high) * plot.height()

        # Scale: three quiet lines with their values.
        grid = QColor(tc.get("border_subtle"))
        for value in (low, low / 2 + high / 2, high):
            y = y_of(value)
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(muted)
            painter.drawText(
                QRectF(0, y - 8, self._AXIS_WIDTH - 6, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _tidy(value),
            )
        painter.setPen(muted)
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 3, plot.width(), 16),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"last {len(self._rows)} readings →",
        )

        # The lines themselves.
        count = len(self._rows)
        step = plot.width() / max(1, count - 1)
        for name in names:
            points = QPolygonF()
            for index, row in enumerate(self._rows):
                if name in row:
                    points.append(QPointF(plot.left() + index * step, y_of(row[name])))
            pen = QPen(QColor(self.colour_for(name)), 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if points.size() == 1:
                painter.drawEllipse(points[0], 2.5, 2.5)
            else:
                painter.drawPolyline(points)

        # Pointer: a line at the reading being read, a dot on each series.
        ago, values = self.readout()
        if self._hover_x is not None and count > 1:
            x = plot.left() + (count - 1 - ago) * step
            painter.setPen(QPen(secondary, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            for name, value in values.items():
                painter.setPen(QPen(surface, 2))
                painter.setBrush(QColor(self.colour_for(name)))
                painter.drawEllipse(QPointF(x, y_of(value)), 4, 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # Legend: a swatch for identity, the name and value in text ink.
        x = 12.0
        for name in names:
            label = name + (f"  {_tidy(values[name])}" if name in values else "")
            width = metrics.horizontalAdvance(label)
            if x + 20 + width > self.width() - 8:
                break  # out of room: the monitor's text has every value
            painter.setPen(QPen(QColor(self.colour_for(name)), 3))
            painter.drawLine(QPointF(x, 14), QPointF(x + 12, 14))
            painter.setPen(secondary)
            painter.drawText(
                QRectF(x + 18, 4, width + 4, 20),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label,
            )
            x += 18 + width + 18
        if self._hover_x is not None and ago:
            note = f"{ago} reading{'s' if ago != 1 else ''} ago"
            if x + metrics.horizontalAdvance(note) < self.width() - 8:
                painter.setPen(muted)
                painter.drawText(
                    QRectF(x, 4, metrics.horizontalAdvance(note) + 4, 20),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    note,
                )
