"""File explorer panel — VS Code-style project directory tree view."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QDir, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PyQt6.QtGui import (
    QColor,
    QFileSystemModel,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

HIDDEN_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
    "__pypackages__", ".tox", "dist", "build", "*.egg-info",
}

# File extension → (icon color, icon symbol)
_FILE_ICONS: dict[str, tuple[str, str]] = {
    ".py": ("#3572A5", "Py"),
    ".pyw": ("#3572A5", "Py"),
    ".js": ("#f1e05a", "JS"),
    ".jsx": ("#f1e05a", "JX"),
    ".ts": ("#3178c6", "TS"),
    ".tsx": ("#3178c6", "TX"),
    ".html": ("#e34c26", "<>"),
    ".htm": ("#e34c26", "<>"),
    ".css": ("#563d7c", "#"),
    ".scss": ("#c6538c", "#"),
    ".json": ("#a8a800", "{}"),
    ".yaml": ("#cb171e", "Y"),
    ".yml": ("#cb171e", "Y"),
    ".toml": ("#9c4221", "T"),
    ".cfg": ("#9c4221", "C"),
    ".ini": ("#9c4221", "I"),
    ".md": ("#083fa1", "M"),
    ".rst": ("#083fa1", "R"),
    ".txt": ("#6a737d", "T"),
    ".sh": ("#89e051", "$"),
    ".bash": ("#89e051", "$"),
    ".zsh": ("#89e051", "$"),
    ".rs": ("#dea584", "Rs"),
    ".go": ("#00ADD8", "Go"),
    ".java": ("#b07219", "Jv"),
    ".c": ("#555555", "C"),
    ".cpp": ("#f34b7d", "C+"),
    ".h": ("#555555", "H"),
    ".hpp": ("#f34b7d", "H+"),
    ".rb": ("#701516", "Rb"),
    ".php": ("#4F5D95", "Ph"),
    ".swift": ("#F05138", "Sw"),
    ".kt": ("#A97BFF", "Kt"),
    ".sql": ("#e38c00", "SQ"),
    ".xml": ("#e44b23", "XM"),
    ".svg": ("#ff9900", "SV"),
    ".png": ("#a855f7", "Im"),
    ".jpg": ("#a855f7", "Im"),
    ".jpeg": ("#a855f7", "Im"),
    ".gif": ("#a855f7", "Im"),
    ".ico": ("#a855f7", "Ic"),
    ".whl": ("#3572A5", "Wh"),
    ".lock": ("#6a737d", "Lk"),
    ".env": ("#ecd53f", "Ev"),
    ".gitignore": ("#f05032", "Gi"),
    ".dockerignore": ("#384d54", "Di"),
    ".qss": ("#563d7c", "Qs"),
}

_SPECIAL_FILENAMES: dict[str, tuple[str, str]] = {
    "Dockerfile": ("#384d54", "Dk"),
    "Makefile": ("#6a737d", "Mk"),
    "LICENSE": ("#d73a49", "Li"),
    "README.md": ("#083fa1", "Rm"),
    "pyproject.toml": ("#3572A5", "Pp"),
    "setup.py": ("#3572A5", "St"),
    "setup.cfg": ("#3572A5", "St"),
    "requirements.txt": ("#3572A5", "Rq"),
    "package.json": ("#cb3837", "Np"),
    "tsconfig.json": ("#3178c6", "Tc"),
    ".gitignore": ("#f05032", "Gi"),
}

# Cache icons to avoid re-creating them every paint
_icon_cache: dict[str, QIcon] = {}


def _make_file_icon(color_hex: str, text: str) -> QIcon:
    """Create a small colored icon with 1-2 letter label."""
    key = f"{color_hex}:{text}"
    if key in _icon_cache:
        return _icon_cache[key]

    size = 32  # high-res for crisp scaling
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Rounded rect background
    painter.setBrush(QColor(color_hex))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 4, size - 4, size - 8, 4, 4)

    # Text
    painter.setPen(QColor("#ffffff"))
    font = QFont("sans-serif")
    font.setPixelSize(14 if len(text) <= 2 else 11)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()

    icon = QIcon(pixmap)
    _icon_cache[key] = icon
    return icon


def _make_folder_icon(expanded: bool = False) -> QIcon:
    """Create a folder icon."""
    key = f"folder:{'open' if expanded else 'closed'}"
    if key in _icon_cache:
        return _icon_cache[key]

    size = 32
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = QColor("#dcb67a") if expanded else QColor("#c09553")
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)

    # Folder tab
    painter.drawRoundedRect(3, 6, 12, 4, 2, 2)
    # Folder body
    painter.drawRoundedRect(3, 9, size - 6, size - 16, 3, 3)

    painter.end()
    icon = QIcon(pixmap)
    _icon_cache[key] = icon
    return icon


def get_file_icon(file_path: str) -> QIcon:
    """Get appropriate icon for a file based on name/extension."""
    name = Path(file_path).name

    # Check special filenames first
    if name in _SPECIAL_FILENAMES:
        color, text = _SPECIAL_FILENAMES[name]
        return _make_file_icon(color, text)

    # Check extension
    suffix = Path(file_path).suffix.lower()
    if suffix in _FILE_ICONS:
        color, text = _FILE_ICONS[suffix]
        return _make_file_icon(color, text)

    # Default file icon
    return _make_file_icon("#6a737d", "·")


class FileIconDelegate(QStyledItemDelegate):
    """Custom delegate to draw file-type icons instead of system icons."""

    def __init__(self, fs_model: QFileSystemModel, proxy: QSortFilterProxyModel,
                 parent=None):
        super().__init__(parent)
        self._fs_model = fs_model
        self._proxy = proxy

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        source_index = self._proxy.mapToSource(index)
        file_path = self._fs_model.filePath(source_index)
        is_dir = self._fs_model.isDir(source_index)
        file_name = self._fs_model.fileName(source_index)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        x = rect.x()
        y = rect.y()
        h = rect.height()

        # Draw selection/hover background (full width)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#04395e"))
            # Left accent bar for selected item
            painter.fillRect(0, y, 2, h, QColor("#0078d4"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor("#2a2d2e"))

        # Draw indent guides (subtle vertical lines)
        indent = 16
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()

        guide_pen = QPen(QColor("#333333"))
        guide_pen.setWidthF(1.0)
        painter.setPen(guide_pen)
        for d in range(depth):
            guide_x = x + (d * indent) + 8
            painter.drawLine(guide_x, y, guide_x, y + h)

        # Content start position
        content_x = x + 4

        # Draw chevron for directories
        if is_dir:
            tree_view = option.widget
            is_expanded = False
            if tree_view and isinstance(tree_view, QTreeView):
                is_expanded = tree_view.isExpanded(index)

            chevron_x = content_x
            chevron_y = y + h // 2

            chevron_pen = QPen(QColor("#a0a0a0"))
            chevron_pen.setWidthF(1.2)
            chevron_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            chevron_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(chevron_pen)

            if is_expanded:
                # Down chevron ▼
                painter.drawLine(chevron_x + 2, chevron_y - 2,
                                 chevron_x + 5, chevron_y + 1)
                painter.drawLine(chevron_x + 5, chevron_y + 1,
                                 chevron_x + 8, chevron_y - 2)
            else:
                # Right chevron ►
                painter.drawLine(chevron_x + 3, chevron_y - 3,
                                 chevron_x + 6, chevron_y)
                painter.drawLine(chevron_x + 6, chevron_y,
                                 chevron_x + 3, chevron_y + 3)

            content_x += 12

        # Draw icon
        icon_size = 16
        icon_y = y + (h - icon_size) // 2

        if is_dir:
            tree_view = option.widget
            is_expanded = False
            if tree_view and isinstance(tree_view, QTreeView):
                is_expanded = tree_view.isExpanded(index)
            icon = _make_folder_icon(is_expanded)
        else:
            icon = get_file_icon(file_path)

        icon.paint(painter, content_x, icon_y, icon_size, icon_size)

        # Draw file name
        text_x = content_x + icon_size + 6
        if is_dir:
            painter.setPen(QColor("#cccccc"))
            font = painter.font()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
        else:
            painter.setPen(QColor("#cccccc"))
            font = painter.font()
            font.setPixelSize(13)
            font.setBold(False)
            painter.setFont(font)

        painter.drawText(text_x, y, rect.width() - text_x, h,
                         Qt.AlignmentFlag.AlignVCenter, file_name)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), 22)


class FilterProxyModel(QSortFilterProxyModel):
    """Filters out common hidden/build directories."""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, QFileSystemModel):
            return True
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        return name not in HIDDEN_DIRS and not name.endswith(".egg-info")


class _DragDropTreeView(QTreeView):
    """Tree view that supports drag & drop to move files/folders."""

    def __init__(self, explorer: "FileExplorer", parent=None):
        super().__init__(parent)
        self._explorer = explorer

    def dropEvent(self, event):
        """Handle drop — move the dragged file/folder to the target directory."""
        import shutil

        # Get the target index (where we're dropping)
        target_index = self.indexAt(event.position().toPoint())
        if not target_index.isValid():
            event.ignore()
            return

        # Resolve target path
        proxy = self._explorer._proxy_model
        fs_model = self._explorer._fs_model
        target_source = proxy.mapToSource(target_index)
        target_path = Path(fs_model.filePath(target_source))

        # If target is a file, use its parent directory
        if target_path.is_file():
            target_path = target_path.parent

        # Get the dragged item(s) from selection
        selected = self.selectedIndexes()
        if not selected:
            event.ignore()
            return

        source_index = selected[0]
        source_source = proxy.mapToSource(source_index)
        source_path = Path(fs_model.filePath(source_source))

        # Don't drop onto self or own parent
        if source_path == target_path or source_path.parent == target_path:
            event.ignore()
            return

        # Don't drop a folder into its own subtree
        try:
            target_path.relative_to(source_path)
            event.ignore()
            return
        except ValueError:
            pass  # Good — target is not inside source

        dest = target_path / source_path.name
        if dest.exists():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Move Failed",
                f"'{source_path.name}' already exists in '{target_path.name}'."
            )
            event.ignore()
            return

        try:
            shutil.move(str(source_path), str(dest))
            logger.info("Moved %s → %s", source_path, dest)
            event.accept()
        except Exception as e:
            logger.error("Move failed: %s", e)
            event.ignore()


class FileExplorer(QWidget):
    """VS Code-style tree view of the current project directory."""

    on_file_double_clicked: callable = None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background-color: #1e1e1e;")
        self._event_bus = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar — like VS Code "EXPLORER" with action buttons
        header_bar = QWidget()
        header_bar.setObjectName("explorerHeader")
        header_bar.setFixedHeight(32)
        header_bar.setStyleSheet(
            "#explorerHeader { background-color: #252526; border-bottom: 1px solid #333; }"
        )
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(4)

        self._header = QLabel("EXPLORER")
        self._header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #bbbbbb; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        header_layout.addWidget(self._header)
        header_layout.addStretch()

        # Action buttons in header
        for tooltip, icon_char in [
            ("New File", None),
            ("New Folder", None),
            ("Refresh", "↻"),
            ("Collapse All", "⊟"),
        ]:
            btn = QPushButton()
            btn.setObjectName("explorerActionBtn")
            btn.setFixedSize(24, 24)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                #explorerActionBtn {
                    background: transparent; border: none; color: #ffffff;
                    font-size: 15px; border-radius: 3px; padding: 0px;
                    font-weight: normal;
                }
                #explorerActionBtn:hover { background: #3e3e40; color: #ffffff; }
            """)
            if icon_char:
                btn.setText(icon_char)
            elif tooltip == "New File":
                btn.setIcon(self._draw_file_icon())
            elif tooltip == "New Folder":
                btn.setIcon(self._draw_folder_icon())
            if tooltip == "New File":
                btn.clicked.connect(self._new_file_at_root)
            elif tooltip == "New Folder":
                btn.clicked.connect(self._new_folder_at_root)
            elif tooltip == "Refresh":
                btn.clicked.connect(self._refresh)
            elif tooltip == "Collapse All":
                btn.clicked.connect(self._collapse_all)
            header_layout.addWidget(btn)

        layout.addWidget(header_bar)

        # Project name section (collapsible, like VS Code)
        self._project_header = QWidget()
        self._project_header.setObjectName("projectHeader")
        self._project_header.setFixedHeight(24)
        self._project_header.setStyleSheet(
            "#projectHeader { background-color: #252526; border-bottom: 1px solid #333; }"
        )
        self._project_header.hide()
        ph_layout = QHBoxLayout(self._project_header)
        ph_layout.setContentsMargins(6, 0, 6, 0)
        ph_layout.setSpacing(4)

        self._chevron = QLabel("▼")
        self._chevron.setStyleSheet(
            "font-size: 10px; color: #cccccc; background: transparent;"
        )
        self._chevron.setFixedWidth(12)
        ph_layout.addWidget(self._chevron)

        self._project_name = QLabel("")
        self._project_name.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #cccccc; "
            "background: transparent; letter-spacing: 0.3px;"
        )
        ph_layout.addWidget(self._project_name)
        ph_layout.addStretch()

        layout.addWidget(self._project_header)

        # Tree view with drag & drop file moving
        self._tree = _DragDropTreeView(self)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(16)
        self._tree.setUniformRowHeights(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setDragEnabled(True)
        self._tree.setAcceptDrops(True)
        self._tree.setDropIndicatorShown(True)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.clicked.connect(self._on_single_click)
        self._tree.setStyleSheet("""
            QTreeView {
                background-color: #1e1e1e;
                border: none;
                outline: none;
                font-size: 13px;
                show-decoration-selected: 1;
            }
            QTreeView::item {
                padding: 0px;
                height: 22px;
                border: none;
            }
            QTreeView::item:selected {
                background-color: #094771;
            }
            QTreeView::item:hover:!selected {
                background-color: #2a2d2e;
            }
            QTreeView::branch {
                background-color: #1e1e1e;
            }
            QTreeView::branch:has-siblings:!adjoins-item {
                border-image: none;
            }
            QTreeView::branch:has-siblings:adjoins-item {
                border-image: none;
            }
            QTreeView::branch:!has-children:!has-siblings:adjoins-item {
                border-image: none;
            }
            QTreeView::branch:has-children:!has-siblings:closed,
            QTreeView::branch:closed:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QTreeView::branch:open:has-children:!has-siblings,
            QTreeView::branch:open:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #424242;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #555;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        layout.addWidget(self._tree)

        # Placeholder when no project is open
        self._placeholder = QWidget()
        self._placeholder.setStyleSheet("background-color: #1e1e1e;")
        ph_main = QVBoxLayout(self._placeholder)
        ph_main.setAlignment(Qt.AlignmentFlag.AlignCenter)

        no_folder = QLabel("No project open")
        no_folder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        no_folder.setStyleSheet("color: #808080; font-size: 13px; background: transparent;")
        ph_main.addWidget(no_folder)

        open_hint = QLabel("File → Open Project...")
        open_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        open_hint.setStyleSheet("color: #606060; font-size: 12px; background: transparent;")
        ph_main.addWidget(open_hint)

        layout.addWidget(self._placeholder)

        self._tree.hide()

        # File system model
        self._fs_model = QFileSystemModel()
        self._fs_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )

        self._proxy_model = FilterProxyModel()
        self._proxy_model.setSourceModel(self._fs_model)
        self._tree.setModel(self._proxy_model)

        # Custom icon delegate
        self._delegate = FileIconDelegate(self._fs_model, self._proxy_model, self._tree)
        self._tree.setItemDelegateForColumn(0, self._delegate)

        self._project_root: Path | None = None

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def set_root(self, path: Path) -> None:
        """Set the project root directory for the tree view."""
        self._project_root = path
        root_index = self._fs_model.setRootPath(str(path))
        proxy_index = self._proxy_model.mapFromSource(root_index)
        self._tree.setRootIndex(proxy_index)

        # Show only the name column
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)

        self._tree.show()
        self._placeholder.hide()
        self._project_header.show()
        self._project_name.setText(path.name.upper())
        logger.info("File explorer root set to: %s", path)

    def clear(self) -> None:
        self._project_root = None
        self._tree.hide()
        self._placeholder.show()
        self._project_header.hide()

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    def _get_path_from_index(self, index: QModelIndex) -> Path | None:
        source_index = self._proxy_model.mapToSource(index)
        file_path = self._fs_model.filePath(source_index)
        return Path(file_path) if file_path else None

    def _on_double_click(self, index: QModelIndex) -> None:
        path = self._get_path_from_index(index)
        if path and path.is_file() and self.on_file_double_clicked:
            self.on_file_double_clicked(path)

    def _on_single_click(self, index: QModelIndex) -> None:
        """Single click: toggle directories, open files (like VS Code)."""
        path = self._get_path_from_index(index)
        if not path:
            return

        if path.is_dir():
            # Toggle expand/collapse on single click
            if self._tree.isExpanded(index):
                self._tree.collapse(index)
            else:
                self._tree.expand(index)
        elif path.is_file() and self.on_file_double_clicked:
            self.on_file_double_clicked(path)

    def _new_file_at_root(self) -> None:
        if self._project_root:
            self._new_file(self._project_root)

    def _new_folder_at_root(self) -> None:
        if self._project_root:
            self._new_folder(self._project_root)

    @staticmethod
    def _draw_file_icon():
        """Draw a new-file icon (document with + sign)."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Document shape
        p.drawLine(3, 1, 3, 15)
        p.drawLine(3, 15, 11, 15)
        p.drawLine(11, 15, 11, 4)
        p.drawLine(11, 4, 8, 1)
        p.drawLine(8, 1, 3, 1)
        # Fold corner
        p.drawLine(8, 1, 8, 4)
        p.drawLine(8, 4, 11, 4)
        # Plus sign
        pen2 = QPen(QColor("#ffffff"))
        pen2.setWidthF(1.5)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawLine(13, 7, 13, 13)
        p.drawLine(10, 10, 16, 10)
        p.end()
        return QIcon(pixmap)

    @staticmethod
    def _draw_folder_icon():
        """Draw a new-folder icon (folder with + sign)."""
        from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Folder shape
        p.drawRoundedRect(1, 5, 12, 9, 1.5, 1.5)
        # Folder tab
        p.drawLine(1, 5, 1, 3)
        p.drawLine(1, 3, 5, 3)
        p.drawLine(5, 3, 6, 5)
        # Plus sign
        pen2 = QPen(QColor("#ffffff"))
        pen2.setWidthF(1.5)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawLine(13, 7, 13, 13)
        p.drawLine(10, 10, 16, 10)
        p.end()
        return QIcon(pixmap)

    def _refresh(self) -> None:
        """Force refresh the file tree and notify the app."""
        if self._project_root:
            # Re-scan the filesystem
            root_path = str(self._project_root)
            self._fs_model.setRootPath("")  # Force re-read
            root_index = self._fs_model.setRootPath(root_path)
            proxy_index = self._proxy_model.mapFromSource(root_index)
            self._tree.setRootIndex(proxy_index)

            # Emit event so context builder refreshes too
            if self._event_bus:
                self._event_bus.emit("project_refreshed", path=root_path)

            logger.info("File explorer refreshed: %s", root_path)

    def _collapse_all(self) -> None:
        """Collapse all expanded directories."""
        self._tree.collapseAll()

    def _show_context_menu(self, position) -> None:
        index = self._tree.indexAt(position)
        path = self._get_path_from_index(index) if index.isValid() else self._project_root

        if path is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d30;
                border: 1px solid #454545;
                padding: 4px 0;
                color: #cccccc;
                font-size: 12px;
            }
            QMenu::item {
                padding: 4px 28px 4px 12px;
            }
            QMenu::item:selected {
                background-color: #094771;
            }
            QMenu::separator {
                height: 1px;
                background: #454545;
                margin: 4px 8px;
            }
        """)

        if path.is_dir():
            new_file_action = menu.addAction("New File...")
            new_file_action.triggered.connect(lambda: self._new_file(path))

            new_folder_action = menu.addAction("New Folder...")
            new_folder_action.triggered.connect(lambda: self._new_folder(path))

            menu.addSeparator()

        if index.isValid():
            rename_action = menu.addAction("Rename...")
            rename_action.triggered.connect(lambda: self._rename(path))

            delete_action = menu.addAction("Delete")
            delete_action.triggered.connect(lambda: self._delete(path))

            menu.addSeparator()

            copy_path_action = menu.addAction("Copy Path")
            copy_path_action.triggered.connect(lambda: self._copy_path(path))

            copy_rel_action = menu.addAction("Copy Relative Path")
            copy_rel_action.triggered.connect(lambda: self._copy_relative_path(path))

            menu.addSeparator()

            if path.is_file():
                reveal_action = menu.addAction("Reveal in File Manager")
                reveal_action.triggered.connect(lambda: self._reveal_in_file_manager(path))

        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _styled_input(self, title: str, label: str, placeholder: str = "", default: str = "") -> tuple[str, bool]:
        """Show a dark-themed input dialog matching the app style."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setFixedWidth(360)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #252526;
                border: 1px solid #444;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #e0e0e0;")
        layout.addWidget(title_label)

        # Input label
        input_label = QLabel(label)
        input_label.setStyleSheet("font-size: 13px; color: #bbbbbb;")
        layout.addWidget(input_label)

        # Input field
        input_field = QLineEdit()
        input_field.setPlaceholderText(placeholder or label.replace(":", "").strip())
        input_field.setText(default)
        input_field.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: 1px solid #555; border-radius: 6px;
                padding: 8px 12px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #0078d4; }
        """)
        input_field.selectAll()
        layout.addWidget(input_field)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("styledDialogBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            #styledDialogBtn {
                background: transparent; color: #aaa; font-size: 13px;
                padding: 6px 18px; border: 1px solid #555; border-radius: 6px;
            }
            #styledDialogBtn:hover { background: #333; color: #ddd; }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Create" if "New" in title else "Rename")
        ok_btn.setObjectName("styledDialogOkBtn")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            #styledDialogOkBtn {
                background: #0078d4; color: white; font-size: 13px;
                font-weight: 600; padding: 6px 22px; border: none; border-radius: 6px;
            }
            #styledDialogOkBtn:hover { background: #1a8ae8; }
        """)
        ok_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        # Enter key submits
        input_field.returnPressed.connect(dialog.accept)
        input_field.setFocus()

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return input_field.text().strip(), True
        return "", False

    def _new_file(self, parent_dir: Path) -> None:
        name, ok = self._styled_input("New File", "File name:", "example.py")
        if ok and name:
            new_path = parent_dir / name
            if new_path.exists():
                QMessageBox.warning(self, "Error", f"'{name}' already exists.")
                return
            new_path.touch()
            logger.info("Created file: %s", new_path)

    def _new_folder(self, parent_dir: Path) -> None:
        name, ok = self._styled_input("New Folder", "Folder name:", "my-folder")
        if ok and name:
            new_path = parent_dir / name
            if new_path.exists():
                QMessageBox.warning(self, "Error", f"'{name}' already exists.")
                return
            new_path.mkdir(parents=True)
            logger.info("Created folder: %s", new_path)

    def _rename(self, path: Path) -> None:
        name, ok = self._styled_input("Rename", "New name:", default=path.name)
        if ok and name and name != path.name:
            new_path = path.parent / name
            if new_path.exists():
                QMessageBox.warning(self, "Error", f"'{name}' already exists.")
                return
            path.rename(new_path)
            logger.info("Renamed: %s → %s", path.name, name)

    def _delete(self, path: Path) -> None:
        """Show a styled delete confirmation dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Delete")
        dialog.setFixedWidth(380)
        dialog.setStyleSheet("QDialog { background-color: #252526; }")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Delete")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #e0e0e0;")
        layout.addWidget(title)

        kind = "folder" if path.is_dir() else "file"
        msg = QLabel(f"Are you sure you want to delete the {kind}\n<b>{path.name}</b>?")
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 13px; color: #cccccc;")
        layout.addWidget(msg)

        if path.is_dir():
            warn = QLabel("⚠ This will delete the folder and all its contents.")
            warn.setStyleSheet("font-size: 12px; color: #e5a00d;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("styledDialogBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            #styledDialogBtn {
                background: transparent; color: #aaa; font-size: 13px;
                padding: 6px 18px; border: 1px solid #555; border-radius: 6px;
            }
            #styledDialogBtn:hover { background: #333; color: #ddd; }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("styledDeleteBtn")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet("""
            #styledDeleteBtn {
                background: #d32f2f; color: white; font-size: 13px;
                font-weight: 600; padding: 6px 22px; border: none; border-radius: 6px;
            }
            #styledDeleteBtn:hover { background: #e53935; }
        """)
        delete_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(delete_btn)

        layout.addLayout(btn_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                import shutil
                shutil.rmtree(path)
            logger.info("Deleted: %s", path)

    def _copy_path(self, path: Path) -> None:
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(str(path))

    def _copy_relative_path(self, path: Path) -> None:
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard and self._project_root:
            try:
                rel = path.relative_to(self._project_root)
                clipboard.setText(str(rel))
            except ValueError:
                clipboard.setText(str(path))

    def _reveal_in_file_manager(self, path: Path) -> None:
        import subprocess
        target = path.parent if path.is_file() else path
        try:
            subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            logger.warning("Could not open file manager for: %s", target)

    @property
    def tree(self) -> QTreeView:
        return self._tree
