"""Pytest test explorer sidebar panel.

Discovers tests with ``pytest --collect-only``, displays them in a
file → class → test tree, and lets the user run any node (whole suite,
file, class, single test) with live output. Pass/fail status is shown
inline as a coloured dot. Failing tests can be jumped to in the editor
or sent to the chat panel for an AI fix.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import safe_task
from polyglot_ai.core.test_collector import (
    CollectResult,
    TestNode,
    collect_tests,
    run_tests,
)

logger = logging.getLogger(__name__)


# Tree column index — single-column tree, but UserRole carries the TestNode.
_NODE_ROLE = Qt.ItemDataRole.UserRole + 1


class TestPanel(QWidget):
    """Sidebar panel that lists pytest tests and runs them."""

    # Cross-thread signals so async work can update the tree on the GUI thread.
    _collect_done = pyqtSignal(object)  # CollectResult
    _result_received = pyqtSignal(str, str)  # node_id, status
    _output_line = pyqtSignal(str)
    _run_finished = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._event_bus = None
        self._items_by_node_id: dict[str, QTreeWidgetItem] = {}
        self._failed_node_ids: set[str] = set()
        self._running = False
        self._popout_dialog: QDialog | None = None
        self._popout_text: QTextEdit | None = None

        self._collect_done.connect(self._apply_collect)
        self._result_received.connect(self._apply_result)
        self._output_line.connect(self._append_output)
        self._run_finished.connect(self._on_run_finished)

        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #333;")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 0, 6, 0)
        h.setSpacing(2)

        title = QLabel("TESTS")
        title.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #888; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        h.addWidget(title)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "font-size: 10px; color: #4ec9b0; background: transparent; margin-left: 6px;"
        )
        h.addWidget(self._summary_label)
        h.addStretch()

        run_btn = self._icon_btn(self._draw_play_icon(), "Run all tests")
        run_btn.clicked.connect(self._on_run_all)
        h.addWidget(run_btn)

        rerun_btn = self._icon_btn(self._draw_rerun_icon(), "Re-run failed tests")
        rerun_btn.clicked.connect(self._on_rerun_failed)
        h.addWidget(rerun_btn)

        refresh_btn = self._icon_btn(self._draw_refresh_icon(), "Refresh test list")
        refresh_btn.clicked.connect(self.refresh)
        h.addWidget(refresh_btn)

        layout.addWidget(header)

        # Tree + output share a vertical splitter so the user can drag
        # the boundary to give either side more room.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #1e1e1e; height: 1px; }"
            "QSplitter::handle:hover { background: #0e639c; }"
        )

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(16)
        self._tree.setRootIsDecorated(True)
        self._tree.setAnimated(True)
        self._tree.setStyleSheet(
            "QTreeWidget { background: #1e1e1e; color: #ddd; border: none; "
            "outline: none; font-size: 12px; }"
            "QTreeWidget::item { padding: 4px 4px; }"
            "QTreeWidget::item:selected { background: #094771; }"
            "QTreeWidget::item:hover { background: #2a2d2e; }"
            "QTreeWidget::branch { background: transparent; }"
            "QTreeWidget::branch:has-children:!has-siblings:closed,"
            "QTreeWidget::branch:closed:has-children:has-siblings {"
            " border-image: none;"
            " image: none;"
            "}"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        splitter.addWidget(self._tree)

        # Output area — header bar with title + pop-out + clear, then
        # a monospace text area below.
        output_wrap = QWidget()
        output_wrap.setStyleSheet("background: #181818; border-top: 1px solid #333;")
        ow_layout = QVBoxLayout(output_wrap)
        ow_layout.setContentsMargins(0, 0, 0, 0)
        ow_layout.setSpacing(0)

        out_header = QWidget()
        out_header.setFixedHeight(26)
        out_header.setStyleSheet("background: #1f1f1f; border-bottom: 1px solid #2a2a2a;")
        oh = QHBoxLayout(out_header)
        oh.setContentsMargins(10, 0, 6, 0)
        oh.setSpacing(4)
        out_title = QLabel("OUTPUT")
        out_title.setStyleSheet(
            "font-size: 10px; font-weight: 600; color: #888; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        oh.addWidget(out_title)
        oh.addStretch()
        popout_btn = self._icon_btn(self._draw_popout_icon(), "Open output in a separate window")
        popout_btn.clicked.connect(self._on_popout_output)
        oh.addWidget(popout_btn)
        clear_btn = self._icon_btn(self._draw_clear_icon(), "Clear output")
        clear_btn.clicked.connect(lambda: self._output.clear())
        oh.addWidget(clear_btn)
        ow_layout.addWidget(out_header)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._output.setStyleSheet(
            "QTextEdit { background: #181818; color: #d0d0d0; border: none; "
            "font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace; "
            "font-size: 11px; padding: 6px 8px; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar:horizontal { height: 8px; background: transparent; }"
            "QScrollBar::handle:vertical, QScrollBar::handle:horizontal { "
            "background: #444; border-radius: 4px; }"
        )
        self._output.setPlaceholderText("Test output will appear here…")
        ow_layout.addWidget(self._output)

        splitter.addWidget(output_wrap)
        splitter.setStretchFactor(0, 3)  # tree gets 3 parts
        splitter.setStretchFactor(1, 2)  # output gets 2
        splitter.setSizes([400, 260])

        layout.addWidget(splitter, stretch=1)

        # Empty / error state — a vertical box with a label and an
        # AI-fix button. The button is only shown when there's an actual
        # collection error (the AI can do something useful with it),
        # and hidden in the "no tests yet" / "no project open" cases.
        self._empty = QWidget()
        self._empty.setStyleSheet("background: #1e1e1e;")
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setContentsMargins(20, 20, 20, 20)
        empty_layout.setSpacing(12)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._empty_label = QLabel(
            "No tests discovered.\n\nClick refresh after opening a project,\n"
            "or install pytest in your venv:\n\npip install pytest"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._empty_label.setStyleSheet(
            "color: #c0c0c0; font-size: 12px; "
            "font-family: 'JetBrains Mono', monospace; "
            "background: transparent;"
        )
        empty_layout.addWidget(self._empty_label)

        self._empty_fix_btn = QPushButton("✨ Fix with AI")
        self._empty_fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._empty_fix_btn.setStyleSheet(
            "QPushButton { background: #0e639c; color: white; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #1a8ae8; }"
        )
        self._empty_fix_btn.clicked.connect(self._on_fix_collection_error)
        self._empty_fix_btn.hide()
        empty_layout.addWidget(self._empty_fix_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()

        # Stash the most recent collection error so the button can use it
        self._last_collect_error: str = ""

        self._empty.hide()
        layout.addWidget(self._empty)

    # ── Public API (called from ui_wiring) ──────────────────────────

    def set_project_root(self, path: Path) -> None:
        self._project_root = path
        self.refresh()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        # Refresh test list when files are saved (someone may have added a test).
        event_bus.subscribe("file:saved", lambda **kw: self._maybe_refresh_after_save(kw))

    def _maybe_refresh_after_save(self, kwargs: dict) -> None:
        path = kwargs.get("path", "")
        if not path:
            return
        # Only refresh if the saved file looks like a test file or conftest.
        name = Path(path).name
        if name.startswith("test_") or name == "conftest.py" or name.endswith("_test.py"):
            self.refresh()

    def refresh(self) -> None:
        """Re-run pytest collection."""
        if self._project_root is None:
            self._summary_label.setText("")
            self._tree.clear()
            self._items_by_node_id.clear()
            self._empty_label.setText("No project open.\n\nOpen a folder via File → Open Project.")
            self._empty_fix_btn.hide()
            self._empty.show()
            self._tree.hide()
            return
        if self._running:
            return
        self._summary_label.setText("Discovering…")
        self._summary_label.setStyleSheet(
            "font-size: 10px; color: #e5a00d; background: transparent; margin-left: 6px;"
        )
        safe_task(self._do_collect(), name="test_collect")

    async def _do_collect(self) -> None:
        try:
            result = await collect_tests(self._project_root)
        except Exception as e:
            logger.exception("test_collector: collect crashed")
            result = CollectResult(ok=False, error=str(e))
        self._collect_done.emit(result)

    # ── Tree population ─────────────────────────────────────────────

    def _apply_collect(self, result: CollectResult) -> None:
        self._tree.clear()
        self._items_by_node_id.clear()
        self._failed_node_ids.clear()

        if not result.ok:
            self._tree.hide()
            self._empty.show()
            error_text = result.error or "Test discovery failed."
            self._empty_label.setText(error_text)
            self._empty_label.setStyleSheet(
                "color: #f48771; font-size: 11px; "
                "font-family: 'JetBrains Mono', monospace; background: transparent;"
            )
            self._last_collect_error = error_text
            self._empty_fix_btn.show()
            self._summary_label.setText("Failed")
            self._summary_label.setStyleSheet(
                "font-size: 10px; color: #f48771; background: transparent; margin-left: 6px;"
            )
            return

        if not result.roots:
            self._tree.hide()
            self._empty.show()
            self._empty_label.setText(
                "No tests found.\n\npytest discovered no tests in this project."
            )
            self._empty_label.setStyleSheet(
                "color: #c0c0c0; font-size: 12px; "
                "font-family: 'JetBrains Mono', monospace; background: transparent;"
            )
            self._empty_fix_btn.hide()
            self._last_collect_error = ""
            self._summary_label.setText("0 tests")
            self._summary_label.setStyleSheet(
                "font-size: 10px; color: #888; background: transparent; margin-left: 6px;"
            )
            return

        self._empty.hide()
        self._tree.show()
        total = 0
        for root in result.roots:
            file_item = self._add_node(None, root)
            # Auto-expand top-level files so the user immediately sees
            # the test names — collapsed-by-default felt like a dead UI.
            file_item.setExpanded(True)
            total += self._count_tests(root)

        self._summary_label.setText(f"{total} tests")
        self._summary_label.setStyleSheet(
            "font-size: 10px; color: #4ec9b0; background: transparent; margin-left: 6px;"
        )

    def _count_tests(self, node: TestNode) -> int:
        if node.kind == "test":
            return 1
        return sum(self._count_tests(c) for c in node.children)

    def _add_node(self, parent: QTreeWidgetItem | None, node: TestNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._format_label(node)])
        item.setData(0, _NODE_ROLE, node)
        item.setIcon(0, self._status_icon(node.status, node.kind))
        if parent is None:
            self._tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items_by_node_id[node.node_id] = item
        for child in node.children:
            self._add_node(item, child)
        return item

    @staticmethod
    def _format_label(node: TestNode) -> str:
        return node.name

    # ── Status icons (painted) ──────────────────────────────────────

    def _status_icon(self, status: str, kind: str) -> QIcon:
        # Map both pytest's full names ("passed") AND short names ("pass")
        # so the panel works whether the caller normalises the status or
        # passes it through verbatim from the regex match.
        colours = {
            "passed": "#4ec9b0",
            "pass": "#4ec9b0",
            "failed": "#f48771",
            "fail": "#f48771",
            "error": "#f48771",
            "skipped": "#888888",
            "skip": "#888888",
            "xfail": "#888888",
            "xpass": "#4ec9b0",
            "running": "#e5a00d",
        }
        if status in colours:
            return self._draw_dot_icon(colours[status])
        # Unknown status — use kind-specific glyph
        if kind == "file":
            return self._draw_file_icon()
        if kind == "class":
            return self._draw_class_icon()
        return self._draw_dot_icon("#555555")

    @staticmethod
    def _draw_dot_icon(colour: str) -> QIcon:
        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(colour))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(3, 3, 8, 8)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_file_icon() -> QIcon:
        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#888"))
        pen.setWidthF(1.2)
        p.setPen(pen)
        p.drawRect(3, 1, 8, 11)
        p.drawLine(5, 4, 9, 4)
        p.drawLine(5, 7, 9, 7)
        p.drawLine(5, 10, 9, 10)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_class_icon() -> QIcon:
        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#aaa"))
        pen.setWidthF(1.2)
        p.setPen(pen)
        # Stylised C
        p.drawArc(QRectF(2, 2, 10, 10), 30 * 16, 300 * 16)
        p.end()
        return QIcon(pm)

    # ── Header icon buttons ─────────────────────────────────────────

    def _icon_btn(self, icon: QIcon, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("testHdrBtn")
        btn.setIcon(icon)
        btn.setFixedSize(22, 22)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#testHdrBtn { background: transparent; border: none; }"
            "#testHdrBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        return btn

    @staticmethod
    def _draw_play_icon() -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#4ec9b0"))
        p.setPen(Qt.PenStyle.NoPen)
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF

        triangle = QPolygonF([QPointF(4, 3), QPointF(13, 8), QPointF(4, 13)])
        p.drawPolygon(triangle)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_rerun_icon() -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#e5a00d"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawArc(QRectF(3, 3, 10, 10), 30 * 16, 300 * 16)
        p.drawLine(13, 2, 13, 6)
        p.drawLine(13, 6, 9, 6)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_popout_icon() -> QIcon:
        """Box-with-arrow ↗ glyph for the pop-out output button."""
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        # Box (window) outline
        p.drawRect(2, 5, 9, 9)
        # Arrow pointing up-right
        p.drawLine(7, 9, 14, 2)
        p.drawLine(9, 2, 14, 2)
        p.drawLine(14, 2, 14, 7)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_clear_icon() -> QIcon:
        """Trash / clear glyph for the clear-output button."""
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        # Lid
        p.drawLine(3, 5, 13, 5)
        p.drawLine(6, 5, 6, 3)
        p.drawLine(6, 3, 10, 3)
        p.drawLine(10, 3, 10, 5)
        # Bin body
        p.drawLine(4, 5, 5, 14)
        p.drawLine(12, 5, 11, 14)
        p.drawLine(5, 14, 11, 14)
        # Vertical strokes
        p.drawLine(7, 7, 7, 12)
        p.drawLine(9, 7, 9, 12)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _draw_refresh_icon() -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cccccc"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawArc(QRectF(3, 3, 10, 10), 60 * 16, 280 * 16)
        p.drawLine(12, 2, 12, 6)
        p.drawLine(12, 6, 8, 6)
        p.end()
        return QIcon(pm)

    # ── Run actions ─────────────────────────────────────────────────

    def _on_run_all(self) -> None:
        self._run_node(None)

    def _on_rerun_failed(self) -> None:
        if not self._failed_node_ids:
            self._append_output("No failed tests to re-run.")
            return
        # pytest accepts multiple node ids on the command line.
        self._run_multiple(list(self._failed_node_ids))

    def _run_selected(self) -> None:
        item = self._tree.currentItem()
        if not item:
            return
        node: TestNode = item.data(0, _NODE_ROLE)
        if node:
            self._run_node(node.node_id)

    def _run_node(self, node_id: str | None) -> None:
        if self._project_root is None:
            self._append_output("Open a project first.")
            return
        if self._running:
            self._append_output("A test run is already in progress.")
            return
        self._failed_node_ids.clear()
        self._output.clear()
        target = node_id or "(all tests)"
        self._append_output(f"$ pytest {target}")
        self._running = True
        self._mark_running(node_id)
        safe_task(self._do_run(node_id), name="test_run")

    def _run_multiple(self, node_ids: list[str]) -> None:
        if self._project_root is None or self._running or not node_ids:
            return
        self._output.clear()
        self._append_output(f"$ pytest {' '.join(node_ids)}")
        self._running = True
        for nid in node_ids:
            self._mark_running(nid)
        safe_task(self._do_run_multi(node_ids), name="test_run_multi")

    async def _do_run(self, node_id: str | None) -> None:
        try:
            async for event in run_tests(self._project_root, node_id=node_id):
                if event.kind == "result":
                    self._result_received.emit(event.node_id, event.status)
                self._output_line.emit(event.text)
        except Exception as e:
            logger.exception("test_panel: run crashed")
            self._output_line.emit(f"[run crashed] {e}")
        finally:
            self._run_finished.emit()

    async def _do_run_multi(self, node_ids: list[str]) -> None:
        try:
            for nid in node_ids:
                async for event in run_tests(self._project_root, node_id=nid):
                    if event.kind == "result":
                        self._result_received.emit(event.node_id, event.status)
                    self._output_line.emit(event.text)
        except Exception as e:
            logger.exception("test_panel: multi-run crashed")
            self._output_line.emit(f"[run crashed] {e}")
        finally:
            self._run_finished.emit()

    def _mark_running(self, node_id: str | None) -> None:
        if node_id is None:
            for nid, item in self._items_by_node_id.items():
                node = item.data(0, _NODE_ROLE)
                if node and node.kind == "test":
                    node.status = "running"
                    item.setIcon(0, self._status_icon("running", "test"))
        else:
            item = self._items_by_node_id.get(node_id)
            if item:
                node = item.data(0, _NODE_ROLE)
                if node:
                    node.status = "running"
                    item.setIcon(0, self._status_icon("running", node.kind))

    # ── Streamed updates ────────────────────────────────────────────

    def _apply_result(self, node_id: str, status: str) -> None:
        item = self._items_by_node_id.get(node_id)
        if not item:
            logger.debug("test_panel: result for unknown node_id %r", node_id)
            return
        node = item.data(0, _NODE_ROLE)
        if node is None:
            return
        node.status = status
        item.setIcon(0, self._status_icon(status, node.kind))
        if status in ("failed", "fail", "error"):
            self._failed_node_ids.add(node_id)
        # Roll status up to file/class so the parent dot reflects worst child.
        self._update_parent_status(item)

    def _update_parent_status(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            children = [parent.child(i) for i in range(parent.childCount())]
            statuses = [
                (c.data(0, _NODE_ROLE).status if c.data(0, _NODE_ROLE) else "unknown")
                for c in children
            ]
            if any(s in ("failed", "fail", "error") for s in statuses):
                rolled = "failed"
            elif all(s in ("passed", "pass") for s in statuses):
                rolled = "passed"
            elif any(s == "running" for s in statuses):
                rolled = "running"
            elif all(s in ("passed", "pass", "skipped", "skip") for s in statuses):
                rolled = "passed"
            else:
                rolled = "unknown"
            node = parent.data(0, _NODE_ROLE)
            if node:
                node.status = rolled
                parent.setIcon(0, self._status_icon(rolled, node.kind))
            parent = parent.parent()

    def _append_output(self, text: str) -> None:
        self._output.append(text)
        sb = self._output.verticalScrollBar()
        sb.setValue(sb.maximum())
        # Mirror into the pop-out window if one is open.
        if getattr(self, "_popout_dialog", None) is not None:
            try:
                self._popout_text.append(text)
                psb = self._popout_text.verticalScrollBar()
                psb.setValue(psb.maximum())
            except RuntimeError:
                # Dialog was closed without us hearing about it.
                self._popout_dialog = None

    def _on_popout_output(self) -> None:
        """Open the test output in a separate resizable window."""
        if getattr(self, "_popout_dialog", None) is not None:
            self._popout_dialog.raise_()
            self._popout_dialog.activateWindow()
            return

        dlg = QDialog(self.window())
        dlg.setWindowTitle("Test output — Polyglot AI")
        dlg.setModal(False)  # non-modal so the user can keep working
        dlg.resize(900, 600)
        dlg.setStyleSheet("QDialog { background: #1e1e1e; }")

        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Test output")
        title.setStyleSheet(
            "color: #ddd; font-size: 13px; font-weight: 600; background: transparent;"
        )
        header.addWidget(title)
        header.addStretch()
        copy_btn = QPushButton("Copy all")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(
            "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
            "border-radius: 4px; padding: 5px 12px; font-size: 11px; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        copy_btn.clicked.connect(lambda: self._copy_popout_to_clipboard())
        header.addWidget(copy_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(copy_btn.styleSheet())
        clear_btn.clicked.connect(lambda: self._popout_text.clear())
        header.addWidget(clear_btn)
        v.addLayout(header)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        text.setStyleSheet(
            "QTextEdit { background: #181818; color: #d0d0d0; border: 1px solid #333; "
            "border-radius: 4px; "
            "font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace; "
            "font-size: 12px; padding: 8px 10px; }"
            "QScrollBar:vertical { width: 10px; background: transparent; }"
            "QScrollBar:horizontal { height: 10px; background: transparent; }"
            "QScrollBar::handle:vertical, QScrollBar::handle:horizontal { "
            "background: #444; border-radius: 5px; }"
        )
        # Pre-populate with what's already in the inline output.
        text.setPlainText(self._output.toPlainText())
        v.addWidget(text, stretch=1)

        self._popout_dialog = dlg
        self._popout_text = text
        dlg.finished.connect(self._on_popout_closed)
        dlg.show()

    def _copy_popout_to_clipboard(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        clip = QGuiApplication.clipboard()
        if clip is not None and getattr(self, "_popout_text", None) is not None:
            clip.setText(self._popout_text.toPlainText())

    def _on_popout_closed(self, _result: int) -> None:
        self._popout_dialog = None
        self._popout_text = None

    def _on_run_finished(self) -> None:
        self._running = False

    # ── Tree interactions ───────────────────────────────────────────

    def _show_tree_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if not item:
            return
        node: TestNode = item.data(0, _NODE_ROLE)
        if node is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #252526; color: #ddd; border: 1px solid #444; }"
            "QMenu::item:selected { background: #094771; }"
        )
        run_action = menu.addAction(f"Run {node.kind}")
        open_action = menu.addAction("Open in editor")
        fix_action = None
        if node.status in ("failed", "fail", "error"):
            fix_action = menu.addAction("✨ Fix with AI")
        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen == run_action:
            self._run_node(node.node_id)
        elif chosen == open_action:
            self._open_in_editor(node)
        elif fix_action and chosen == fix_action:
            self._fix_with_ai(node)

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        node: TestNode = item.data(0, _NODE_ROLE)
        if node is None:
            return
        if node.kind == "test":
            self._open_in_editor(node)
        else:
            item.setExpanded(not item.isExpanded())

    def _open_in_editor(self, node: TestNode) -> None:
        if not node.file_path or self._project_root is None:
            return
        full = self._project_root / node.file_path
        if not full.is_file():
            return
        window = self.window()
        editor = getattr(window, "_editor_panel", None)
        if editor is None:
            return
        try:
            editor.open_file(full)
        except Exception:
            logger.exception("test_panel: failed to open %s in editor", full)

    def _on_fix_collection_error(self) -> None:
        """Send the collection error to the chat panel for an AI fix.

        Collection errors usually have an obvious cause (missing import,
        missing dependency, syntax error, wrong fixture name) so the
        AI can typically fix them in one shot.
        """
        if not self._last_collect_error:
            return
        prompt = (
            "pytest could not collect tests in this project. Please investigate "
            "and propose a fix.\n\n"
            "Collection error:\n```\n"
            f"{self._last_collect_error[-3000:]}\n"
            "```\n\n"
            "Read the relevant test files / conftest.py / requirements files and "
            "explain the root cause, then suggest a minimal fix. If a missing "
            "package is the issue, list the exact install command for this project."
        )
        self._send_to_chat(prompt)

    def _fix_with_ai(self, node: TestNode) -> None:
        """Send the failing test's output to the chat panel for an AI fix."""
        output_text = self._output.toPlainText()
        prompt = (
            f"The following pytest test is failing. Please investigate and "
            f"propose a fix.\n\n"
            f"Test: `{node.node_id}`\n"
            f"File: `{node.file_path}`\n\n"
            f"Recent test output:\n```\n{output_text[-3000:]}\n```\n\n"
            f"Please read the relevant source files, explain what's going wrong, "
            f"and suggest a minimal fix."
        )
        self._send_to_chat(prompt)

    def _send_to_chat(self, prompt: str) -> None:
        """Pre-fill the chat input and switch the right-side tabs to Chat.

        Chat lives in ``_right_tabs`` (not the sidebar stack) so we
        need to call ``setCurrentWidget`` on the right tab widget,
        not on the sidebar.
        """
        window = self.window()
        chat = getattr(window, "chat_panel", None)
        if chat is None:
            self._append_output("Chat panel not available.")
            return
        # Use the public prefill_input API instead of touching chat._input
        # directly so future renames don't silently break this feature.
        try:
            chat.prefill_input(prompt)
        except AttributeError:
            # Older chat panel without prefill_input — fall back gracefully
            # but log so we know to fix it.
            logger.warning("chat_panel.prefill_input missing; falling back to private _input")
            try:
                chat._input.setPlainText(prompt)
                chat._input.setFocus()
            except Exception:
                logger.exception("test_panel: failed to populate chat input")
                return

        # Switch the right-side tabs to the chat tab. The chat panel
        # lives in window._right_tabs (not the sidebar stack).
        right_tabs = getattr(window, "_right_tabs", None)
        if right_tabs is not None:
            try:
                idx = right_tabs.indexOf(chat)
                if idx >= 0:
                    right_tabs.setCurrentIndex(idx)
            except Exception:
                logger.exception("test_panel: failed to switch to chat tab")
