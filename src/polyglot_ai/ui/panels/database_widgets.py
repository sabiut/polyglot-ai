"""Shared pieces of the Database panel and its SQL editor window.

The schema tree (the sidebar and the window show the same one), the
query-history list's two-line rows, and a label that shortens itself.
"""

from __future__ import annotations

import tempfile
import time

from PyQt6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from polyglot_ai.ui import theme_colors as tc

# ── Schema tree ─────────────────────────────────────────────────────

_ICON_DIR: str | None = None
_CHEVRONS: dict[tuple[str, str], str] = {}


def chevron_path(direction: str, colour: str) -> str:
    """A painted ``right`` or ``down`` chevron PNG, for tree branch arrows.

    The app's stylesheet styles ``QTreeView::branch``, and once it does Qt
    no longer draws its own expand arrows — they have to be images.
    """
    global _ICON_DIR
    key = (direction, colour)
    if key in _CHEVRONS:
        return _CHEVRONS[key]
    if _ICON_DIR is None:
        _ICON_DIR = tempfile.mkdtemp(prefix="polyglot_db_")
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if direction == "down":
        points = [QPointF(2.5, 4.5), QPointF(6, 8), QPointF(9.5, 4.5)]
    else:
        points = [QPointF(4.5, 2.5), QPointF(8, 6), QPointF(4.5, 9.5)]
    painter.drawPolyline(QPolygonF(points))
    painter.end()
    path = f"{_ICON_DIR}/chevron_{direction}_{colour.lstrip('#')}.png"
    pixmap.save(path, "PNG")
    _CHEVRONS[key] = path
    return path


def schema_tree_style(item_padding: int = 3) -> str:
    """Stylesheet for a schema tree, expand arrows included."""
    arrow = tc.get("text_secondary")
    return (
        f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
        f"border: none; font-size: {tc.FONT_SM}px; }}"
        f"QTreeWidget::item {{ padding: {item_padding}px; }}"
        f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
        f"QTreeWidget::branch {{ background: {tc.get('bg_base')}; }}"
        "QTreeWidget::branch:has-children:closed "
        f"{{ image: url({chevron_path('right', arrow)}); }}"
        "QTreeWidget::branch:has-children:open "
        f"{{ image: url({chevron_path('down', arrow)}); }}"
        f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
        f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
        f"padding: 4px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
    )


#: What a row of the schema tree is: "database", "table", "column" or "note".
KIND_ROLE = Qt.ItemDataRole.UserRole + 20
#: On a table row: the database it is in, "" for the connected one.
DATABASE_ROLE = Qt.ItemDataRole.UserRole + 21
_LOADED_ROLE = Qt.ItemDataRole.UserRole + 22


def database_icon(colour: str) -> QIcon:
    """A small cylinder."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.3)
    painter.setPen(pen)
    painter.drawEllipse(QRectF(3, 2.5, 10, 4))
    painter.drawLine(QPointF(3, 4.5), QPointF(3, 11.5))
    painter.drawLine(QPointF(13, 4.5), QPointF(13, 11.5))
    painter.drawArc(QRectF(3, 9.5, 10, 4), 180 * 16, 180 * 16)
    painter.drawArc(QRectF(3, 6, 10, 4), 180 * 16, 180 * 16)
    painter.end()
    return QIcon(pixmap)


class SchemaTree(QTreeWidget):
    """Databases, their tables and the tables' columns: name left, type right.

    Filled one of two ways. :meth:`set_databases` lists every database on
    the server, the connected one open with its tables; another is read
    the first time it is opened, which the tree asks for through
    :attr:`database_requested`. :meth:`set_tables` shows tables alone,
    for when the list of databases can't be had.

    Both columns stay in view however narrow the pane is. The name column
    used to have a fixed width wider than the pane, which left the types
    off-screen behind a scrollbar; now the type column takes what its
    text needs, up to :attr:`TYPE_SHARE` of the width, and the names get
    the rest. Whatever doesn't fit is shortened, with the whole of it in
    the tooltip.
    """

    TYPE_SHARE = 0.45

    #: A database was opened whose tables haven't been read yet.
    database_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._type_width = 0
        #: Goes up each time the tree is filled afresh, so a database
        #: still being read for the old contents isn't shown in the new.
        self.generation = 0
        self.setHeaderLabels(["Name", "Type"])
        self.setIndentation(16)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.itemExpanded.connect(self._on_expanded)

    # ── filling ──

    def set_tables(self, tables) -> None:
        """Tables at the top level, with no database above them."""
        self.clear()
        self.generation += 1
        self._add_tables(self.invisibleRootItem(), tables, "", expanded=True)
        self._measure()

    def set_databases(self, databases, tables) -> None:
        """Every database on the server; ``tables`` are the connected one's."""
        self.clear()
        self.generation += 1
        muted = QColor(tc.get("text_muted"))
        bold = QFont(self.font())
        bold.setBold(True)
        for database in databases:
            item = QTreeWidgetItem(self)
            item.setText(0, database.name)
            item.setData(0, KIND_ROLE, "database")
            item.setData(0, DATABASE_ROLE, "" if database.current else database.name)
            colour = "accent_primary" if database.current else "text_muted"
            item.setIcon(0, database_icon(tc.get(colour)))
            if database.current:
                item.setFont(0, bold)
                item.setToolTip(
                    0, f"{database.name} — the database this connection is signed in to"
                )
                self._fill_database(item, tables)
                item.setExpanded(True)
            else:
                kind = "system database" if database.system else "database"
                item.setToolTip(0, f"{database.name} — another {kind} on this server")
                if database.system:
                    item.setForeground(0, muted)
                    item.setText(1, "system")
                    item.setForeground(1, muted)
                self._add_note(item, "Loading…")  # gives the row its arrow
        self._measure()

    def set_database_tables(self, database: str, tables, error: str = "") -> None:
        """Fill in a database opened from the tree — or say why it can't be read."""
        item = self._database_item(database)
        if item is None:
            return
        if error:
            item.takeChildren()
            item.setData(0, _LOADED_ROLE, None)  # opening it again tries again
            note = self._add_note(item, f"Can't read this database: {error}")
            note.setForeground(0, QColor(tc.get("accent_error")))
            note.setToolTip(0, error)
        else:
            self._fill_database(item, tables, expanded=False)
        self._measure()

    def _fill_database(self, item: QTreeWidgetItem, tables, *, expanded: bool = True) -> None:
        item.takeChildren()
        item.setData(0, _LOADED_ROLE, True)
        count = len(tables)
        item.setText(1, f"{count} table{'' if count == 1 else 's'}")
        item.setForeground(1, QColor(tc.get("text_muted")))
        if not tables:
            self._add_note(item, "No tables")
        self._add_tables(item, tables, item.data(0, DATABASE_ROLE) or "", expanded=expanded)

    def _add_note(self, parent: QTreeWidgetItem, text: str) -> QTreeWidgetItem:
        note = QTreeWidgetItem(parent)
        note.setText(0, text)
        note.setData(0, KIND_ROLE, "note")
        note.setForeground(0, QColor(tc.get("text_muted")))
        note.setFirstColumnSpanned(True)
        return note

    def _add_tables(
        self, parent: QTreeWidgetItem, tables, database: str, *, expanded: bool
    ) -> None:
        muted = QColor(tc.get("text_muted"))
        key = QColor(tc.get("accent_warning"))
        for table in tables:
            table_item = QTreeWidgetItem(parent)
            table_item.setText(0, table.name)
            table_item.setData(0, KIND_ROLE, "table")
            table_item.setData(0, DATABASE_ROLE, database)
            count = len(table.columns)
            table_item.setText(1, f"{count} col{'' if count == 1 else 's'}")
            table_item.setForeground(1, muted)
            table_item.setToolTip(0, f"{database}.{table.name}" if database else table.name)
            for col in table.columns:
                col_item = QTreeWidgetItem(table_item)
                col_item.setText(0, col.name)
                col_item.setData(0, KIND_ROLE, "column")
                col_item.setText(1, f"{col.data_type} PK" if col.primary_key else col.data_type)
                col_item.setForeground(1, key if col.primary_key else muted)
                detail = [col.data_type]
                if col.primary_key:
                    detail.append("primary key")
                detail.append("nullable" if col.nullable else "not null")
                tip = f"{col.name} — {', '.join(d for d in detail if d)}"
                col_item.setToolTip(0, tip)
                col_item.setToolTip(1, tip)
            table_item.setExpanded(expanded)

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, KIND_ROLE) == "database" and not item.data(0, _LOADED_ROLE):
            item.setData(0, _LOADED_ROLE, True)  # asked for once, not on every open
            self.database_requested.emit(item.text(0))

    # ── reading ──

    def _database_items(self) -> list[QTreeWidgetItem]:
        items = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        return [item for item in items if item.data(0, KIND_ROLE) == "database"]

    def _database_item(self, database: str) -> QTreeWidgetItem | None:
        for item in self._database_items():
            if item.text(0) == database:
                return item
        return None

    def database_names(self) -> list[str]:
        return [item.text(0) for item in self._database_items()]

    def table_names(self, database: str = "") -> list[str]:
        """The tables shown for ``database`` — the connected one by default."""
        parent = self.invisibleRootItem()
        for item in self._database_items():
            if (database and item.text(0) == database) or (
                not database and not item.data(0, DATABASE_ROLE)
            ):
                parent = item
                break
        else:
            if database or self._database_items():
                return []
        children = [parent.child(i) for i in range(parent.childCount())]
        return [child.text(0) for child in children if child.data(0, KIND_ROLE) == "table"]

    def table_at(self, item: QTreeWidgetItem | None) -> tuple[str, str] | None:
        """``(database, table)`` when ``item`` is a table row; database "" = the connected one."""
        if item is None or item.data(0, KIND_ROLE) != "table":
            return None
        return item.data(0, DATABASE_ROLE) or "", item.text(0)

    # ── layout ──

    def _measure(self) -> None:
        self._type_width = self.sizeHintForColumn(1) + 12
        self._fit_columns()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        available = self.viewport().width()
        wanted = max(self._type_width, self.header().minimumSectionSize())
        self.header().resizeSection(1, min(wanted, int(available * self.TYPE_SHARE)))


def fill_schema_tree(tree: SchemaTree, tables, databases=()) -> None:
    """Databases with their tables when the server listed them; else the tables alone."""
    if databases:
        tree.set_databases(databases, tables)
    else:
        tree.set_tables(tables)


def load_database_into(tree: SchemaTree, connection, database: str) -> None:
    """Read ``database``'s tables in the background and show them under it."""
    from polyglot_ai.core.async_utils import safe_task
    from polyglot_ai.core.db_drivers import describe_error

    generation = tree.generation

    async def load() -> None:
        try:
            tables, error = await connection.get_schema(database), ""
        except Exception as exc:
            tables, error = [], describe_error(exc)
        try:
            if tree.generation == generation:
                tree.set_database_tables(database, tables, error)
        except RuntimeError:
            pass  # the window was closed meanwhile

    safe_task(load(), name="db_schema_database")


def other_database_problem(db_type: str, database: str) -> str:
    """Why a table in ``database`` can't be queried from here; "" when it can."""
    from polyglot_ai.core.db_catalog import can_query_across

    if not database or can_query_across(db_type):
        return ""
    return (
        f"A PostgreSQL connection reads one database. To query “{database}”, add a "
        "connection with that name in its Database field."
    )


def delete_explanation(connected: bool, has_notes: bool) -> str:
    """What deleting a saved connection does, for the question asked first."""
    parts = [
        "Its details and saved password are removed from this computer. "
        "The database itself isn't touched."
    ]
    if connected:
        parts.append("It is open now and will be closed.")
    if has_notes:
        parts.append(
            "Unless you tick the box, its query history and snippets are kept, "
            "and come back if you add a connection with the same name."
        )
    return "\n\n".join(parts)


def notes_label(history: int, snippets: int) -> str:
    """The tick box offering to delete a connection's history and snippets too."""
    kinds = []
    if history:
        kinds.append(f"query history ({history})")
    if snippets:
        kinds.append(f"saved snippets ({snippets})")
    return "Also delete its " + " and ".join(kinds)


def refresh_icon(colour: str) -> QIcon:
    """A circular arrow."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Three quarters of a circle, open at the top right, with an arrow head there.
    painter.drawArc(QRectF(3, 3, 10, 10), 60 * 16, 290 * 16)
    painter.drawPolyline(QPolygonF([QPointF(9.2, 1.6), QPointF(12.6, 3.6), QPointF(9.6, 6.0)]))
    painter.end()
    return QIcon(pixmap)


# ── Query history ───────────────────────────────────────────────────


def history_meta(entry, now: float | None = None) -> str:
    """The second line of a history row: when, and how it went.

    ``14:32 · 4 rows · 3 ms`` — or, for a query that failed, the reason,
    which the list used to keep to itself.
    """
    now = time.time() if now is None else now
    ran, today = time.localtime(entry.executed_at), time.localtime(now)
    same_day = (ran.tm_year, ran.tm_yday) == (today.tm_year, today.tm_yday)
    when = time.strftime("%H:%M" if same_day else "%d %b %H:%M", ran)
    if entry.error:
        reason = " ".join(str(entry.error).split())
        return f"{when} · Failed: {reason}"
    parts = [when]
    if entry.row_count >= 0:
        parts.append(f"{entry.row_count:,} row{'' if entry.row_count == 1 else 's'}")
    if entry.duration_ms > 0:
        parts.append(f"{entry.duration_ms:,} ms")
    return " · ".join(parts)


def history_preview(sql: str) -> str:
    """One line standing for a query: its first lines of SQL, not its header comment."""
    lines = [line.strip() for line in sql.strip().splitlines()]
    code = [line for line in lines if line and not line.startswith("--")]
    return " ".join(" ".join(code or lines).split())[:300]


def history_tooltip(entry, now: float | None = None) -> str:
    sql = entry.sql.strip()
    if len(sql) > 1500:
        sql = sql[:1500] + "…"
    return f"{sql}\n\n{history_meta(entry, now)}"


class HistoryDelegate(QStyledItemDelegate):
    """A history row: the SQL on one line, when/outcome beneath it.

    Both lines are shortened to the width of the list, so nothing is cut
    off behind a horizontal scrollbar.
    """

    META_ROLE = Qt.ItemDataRole.UserRole + 2
    FAILED_ROLE = Qt.ItemDataRole.UserRole + 3
    _PAD_X, _PAD_Y, _GAP = 8, 5, 2

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        # The app's code fonts; the style hint is what makes the fallback
        # a fixed-width font when none of the named ones is installed.
        sql_font = QFont(option.font)
        sql_font.setFamilies([f.strip(' "') for f in tc.FONT_CODE.split(",")])
        sql_font.setStyleHint(QFont.StyleHint.Monospace)
        sql_font.setPixelSize(tc.FONT_SM)
        meta_font = QFont(option.font)
        meta_font.setPixelSize(tc.FONT_XS)
        return sql_font, meta_font

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        sql_font, meta_font = self._fonts(option)
        height = (
            QFontMetrics(sql_font).height()
            + QFontMetrics(meta_font).height()
            + 2 * self._PAD_Y
            + self._GAP
        )
        return QSize(120, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        sql = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        # Background, hover and selection, as the stylesheet draws them.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        failed = bool(index.data(self.FAILED_ROLE))
        meta = index.data(self.META_ROLE) or ""
        sql_font, meta_font = self._fonts(opt)
        sql_metrics, meta_metrics = QFontMetrics(sql_font), QFontMetrics(meta_font)
        rect = opt.rect.adjusted(self._PAD_X, self._PAD_Y, -self._PAD_X, -self._PAD_Y)
        elide = Qt.TextElideMode.ElideRight

        painter.save()
        painter.setFont(sql_font)
        painter.setPen(QColor(tc.get("text_on_accent" if selected else "text_primary")))
        painter.drawText(
            rect.left(),
            rect.top() + sql_metrics.ascent(),
            sql_metrics.elidedText(sql, elide, rect.width()),
        )
        painter.setFont(meta_font)
        if selected:
            colour = "text_on_accent"  # red on the selection colour is hard to read
        else:
            colour = "accent_error" if failed else "text_muted"
        painter.setPen(QColor(tc.get(colour)))
        painter.drawText(
            rect.left(),
            rect.top() + sql_metrics.height() + self._GAP + meta_metrics.ascent(),
            meta_metrics.elidedText(meta, elide, rect.width()),
        )
        painter.restore()


# ── Header ──────────────────────────────────────────────────────────


class ElidedLabel(QLabel):
    """A one-line label that shortens its text in the middle to fit.

    A plain label insists on being as wide as its text, which for a long
    host name or file path would set the window's minimum width.
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._fit()

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        width = self.fontMetrics().horizontalAdvance(self._full) + 4
        return QSize(width, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        width = max(self.width() - 2, 0)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, width))
