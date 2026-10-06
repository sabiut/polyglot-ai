"""File explorer panel — VS Code-style project directory tree view."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QDir, QModelIndex, QSize, QSortFilterProxyModel, Qt, pyqtSignal
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
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels import shared_icons

logger = logging.getLogger(__name__)

# Header toolbar glyphs, by tooltip. All painted at the same stroke
# weight (the old set mixed two painted icons with two text glyphs).
_HEADER_ICONS = {
    "New File": shared_icons.draw_new_file_icon,
    "New Folder": shared_icons.draw_new_folder_icon,
    "Refresh": shared_icons.draw_refresh_icon,
    "Collapse All": shared_icons.draw_collapse_all_icon,
    "Hidden Files": shared_icons.draw_eye_icon,
}

#: Always filtered out of the tree, hidden-files toggle or not: VCS
#: internals, virtualenvs, caches and build output. Dotfiles that are
#: part of the project (.env, .gitignore, .github, …) are NOT here.
HIDDEN_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
    "__pypackages__",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
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

    def __init__(self, fs_model: QFileSystemModel, proxy: QSortFilterProxyModel, parent=None):
        super().__init__(parent)
        self._fs_model = fs_model
        self._proxy = proxy

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
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
            painter.fillRect(option.rect, QColor(tc.get("bg_active")))
            # Left accent bar for selected item
            painter.fillRect(0, y, 2, h, QColor(tc.get("accent_primary")))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(tc.get("bg_hover_subtle")))

        # Draw indent guides (subtle vertical lines)
        indent = 16
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()

        guide_pen = QPen(QColor(tc.get("border_secondary")))
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

            chevron_pen = QPen(QColor(tc.get("text_secondary")))
            chevron_pen.setWidthF(1.2)
            chevron_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            chevron_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(chevron_pen)

            if is_expanded:
                # Down chevron ▼
                painter.drawLine(chevron_x + 2, chevron_y - 2, chevron_x + 5, chevron_y + 1)
                painter.drawLine(chevron_x + 5, chevron_y + 1, chevron_x + 8, chevron_y - 2)
            else:
                # Right chevron ►
                painter.drawLine(chevron_x + 3, chevron_y - 3, chevron_x + 6, chevron_y)
                painter.drawLine(chevron_x + 6, chevron_y, chevron_x + 3, chevron_y + 3)

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
            painter.setPen(QColor(tc.get("text_primary")))
            font = painter.font()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
        else:
            painter.setPen(QColor(tc.get("text_primary")))
            font = painter.font()
            font.setPixelSize(13)
            font.setBold(False)
            painter.setFont(font)

        painter.drawText(
            text_x, y, rect.width() - text_x, h, Qt.AlignmentFlag.AlignVCenter, file_name
        )

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), 22)


class FilterProxyModel(QSortFilterProxyModel):
    """Filters out common hidden/build directories, and optionally all dotfiles."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._show_hidden = True

    @property
    def show_hidden(self) -> bool:
        return self._show_hidden

    def set_show_hidden(self, show: bool) -> None:
        if show != self._show_hidden:
            self._show_hidden = bool(show)
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, QFileSystemModel):
            return True
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name in HIDDEN_DIRS or name.endswith(".egg-info"):
            return False
        if not self._show_hidden and name.startswith("."):
            return False
        return True


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
                self, "Move Failed", f"'{source_path.name}' already exists in '{target_path.name}'."
            )
            event.ignore()
            return

        try:
            shutil.move(str(source_path), str(dest))
            logger.info("Moved %s → %s", source_path, dest)
            event.accept()
        except Exception as e:
            logger.error("Move failed: %s", e)
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Move Failed", f"Couldn't move '{source_path.name}':\n{e}")
            event.ignore()


class _SectionHeader(QWidget):
    """Clickable folder header row (chevron + name + badge)."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _FolderSection(QWidget):
    """One folder in the explorer: a collapsible header plus its own tree.

    All sections share the explorer's file-system model and filter
    proxy; each tree is just rooted at a different directory. The
    first section is the *project* (git, AI tools, indexing all key
    off it); the rest are extra folders for browsing and editing.
    """

    def __init__(self, explorer: "FileExplorer", root: Path, primary: bool) -> None:
        super().__init__(explorer)
        self._explorer = explorer
        self.root = Path(root)
        self.primary = primary
        self._collapsed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = _SectionHeader()
        self.header.setObjectName("projectHeader")
        self.header.setFixedHeight(24)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self.toggle_collapsed)
        self.header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header.customContextMenuRequested.connect(
            lambda pos: explorer._show_folder_menu(self, pos)
        )
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.setSpacing(4)
        self.chevron = QLabel("▼")
        self.chevron.setFixedWidth(12)
        hl.addWidget(self.chevron)
        self.name = QLabel(self.root.name.upper() or str(self.root))
        self.name.setToolTip(str(self.root))
        hl.addWidget(self.name)
        hl.addStretch()
        self.badge = QLabel("")
        hl.addWidget(self.badge)
        layout.addWidget(self.header)

        self.tree = _DragDropTreeView(explorer)
        tree = self.tree
        tree.setHeaderHidden(True)
        tree.setAnimated(False)
        tree.setIndentation(16)
        tree.setUniformRowHeights(True)
        tree.setExpandsOnDoubleClick(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setDragEnabled(True)
        tree.setAcceptDrops(True)
        tree.setDropIndicatorShown(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: explorer._show_context_menu(pos, self))
        tree.doubleClicked.connect(explorer._on_double_click)
        tree.clicked.connect(lambda idx: explorer._on_single_click(idx, tree))
        tree.setModel(explorer._proxy_model)
        self.delegate = FileIconDelegate(explorer._fs_model, explorer._proxy_model, tree)
        tree.setItemDelegateForColumn(0, self.delegate)
        layout.addWidget(tree)

        self.set_primary(primary)
        self.set_root_index()

    def set_primary(self, primary: bool) -> None:
        self.primary = primary
        self.badge.setText("PROJECT" if primary else "")
        self.header.setToolTip(
            "The project: git, tests and the AI's tools work on this folder"
            if primary
            else f"Extra folder — {self.root}\nRight-click to make it the project or remove it"
        )

    def set_root_index(self) -> None:
        fs = self._explorer._fs_model
        proxy = self._explorer._proxy_model
        src = fs.setRootPath(str(self.root)) if self.primary else fs.index(str(self.root))
        self.tree.setRootIndex(proxy.mapFromSource(src))
        for col in range(1, fs.columnCount()):
            self.tree.hideColumn(col)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.tree.setVisible(not collapsed)
        self.chevron.setText("▶" if collapsed else "▼")
        # The section lives in a QSplitter, which keeps handing a hidden
        # tree its share of the height. Pin a collapsed section to its
        # header row so the other folders get the space.
        self.setMaximumHeight(self.header.height() if collapsed else 16_777_215)
        splitter = self.parentWidget()
        if isinstance(splitter, QSplitter):
            splitter.refresh()

    def apply_styles(self, tree_qss: str) -> None:
        self.header.setStyleSheet(
            f"#projectHeader {{ background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        self.chevron.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_primary')}; background: transparent;"
        )
        self.name.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: bold; color: {tc.get('text_primary')}; "
            "background: transparent; letter-spacing: 0.3px;"
        )
        self.badge.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; background: transparent; "
            "letter-spacing: 0.5px;"
        )
        self.tree.setStyleSheet(tree_qss)


class FileExplorer(QWidget):
    """VS Code-style tree of the project directory, plus any extra folders.

    The first folder is the project; ``add_folder`` stacks further
    folders beneath it (File → Add Folder to Explorer…). Right-click a
    folder's header to make it the project or remove it.
    """

    on_file_double_clicked: callable = None

    #: All folders as strings, project first — emitted whenever the set changes.
    folders_changed = pyqtSignal(list)
    #: User asked for an extra folder to become the project (Path).
    set_as_project_requested = pyqtSignal(object)
    #: User asked to add a folder (the window owns the directory dialog).
    add_folder_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event_bus = None
        self._tree_qss = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar — like VS Code "EXPLORER" with action buttons
        self._header_bar = QWidget()
        self._header_bar.setObjectName("explorerHeader")
        self._header_bar.setFixedHeight(32)
        header_layout = QHBoxLayout(self._header_bar)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(4)

        self._header = QLabel("EXPLORER")
        header_layout.addWidget(self._header)
        header_layout.addStretch()

        # Action buttons in header
        self._action_btns: list[QPushButton] = []
        for tooltip in ("New File", "New Folder", "Refresh", "Collapse All", "Hidden Files"):
            btn = QPushButton()
            btn.setObjectName("explorerActionBtn")
            btn.setFixedSize(24, 24)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip == "Hidden Files":
                btn.setCheckable(True)
                btn.setChecked(True)
                btn.toggled.connect(self.set_show_hidden)
                self._hidden_btn = btn
            elif tooltip == "New File":
                btn.clicked.connect(self._new_file_at_root)
            elif tooltip == "New Folder":
                btn.clicked.connect(self._new_folder_at_root)
            elif tooltip == "Refresh":
                btn.clicked.connect(self._refresh)
            elif tooltip == "Collapse All":
                btn.clicked.connect(self._collapse_all)
            self._action_btns.append(btn)
            header_layout.addWidget(btn)

        layout.addWidget(self._header_bar)

        # One collapsible section per folder (project first), in a
        # vertical splitter so the user can share the height between them.
        self._sections_box = QSplitter(Qt.Orientation.Vertical)
        self._sections_box.setChildrenCollapsible(False)
        self._sections_box.setHandleWidth(1)
        self._sections_box.hide()
        layout.addWidget(self._sections_box, stretch=1)
        self._sections: list[_FolderSection] = []

        # Placeholder when no project is open
        self._placeholder = QWidget()
        ph_main = QVBoxLayout(self._placeholder)
        ph_main.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._no_folder = QLabel("No project open")
        self._no_folder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_main.addWidget(self._no_folder)

        self._open_hint = QLabel("File → Open Project...")
        self._open_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_main.addWidget(self._open_hint)

        layout.addWidget(self._placeholder, stretch=1)
        # When every folder is collapsed the splitter is pinned to its
        # headers and can't grow; without a spacer QVBoxLayout would
        # spread the leftover height around the items and the whole
        # explorer would drift to the middle of the sidebar.
        layout.addStretch(0)

        # File system model
        self._fs_model = QFileSystemModel()
        # ``Hidden`` is what makes dotfiles (.env, .gitignore, .github…)
        # reach the proxy at all; without it Qt drops them before our
        # own filter runs. Noise like .git and .venv is still removed
        # by FilterProxyModel via HIDDEN_DIRS.
        self._fs_model.setFilter(
            QDir.Filter.AllDirs
            | QDir.Filter.Files
            | QDir.Filter.NoDotAndDotDot
            | QDir.Filter.Hidden
        )

        self._proxy_model = FilterProxyModel()
        self._proxy_model.setSourceModel(self._fs_model)

        self._project_root: Path | None = None

        self._apply_theme_styles()
        theme.connect_theme_changed(self._apply_theme_styles)

    # ── Sections (folders) ────────────────────────────────────────

    @property
    def _primary(self) -> _FolderSection | None:
        return self._sections[0] if self._sections and self._sections[0].primary else None

    @property
    def _tree(self) -> QTreeView | None:
        """The project's tree (kept for callers that predate multi-folder)."""
        primary = self._primary
        return primary.tree if primary is not None else None

    @property
    def folders(self) -> list[Path]:
        """Every folder shown, project first."""
        return [s.root for s in self._sections]

    @property
    def extra_folders(self) -> list[Path]:
        return [s.root for s in self._sections if not s.primary]

    def _make_section(self, root: Path, primary: bool) -> _FolderSection:
        section = _FolderSection(self, root, primary)
        section.apply_styles(self._tree_qss)
        return section

    def _remove_section(self, section: _FolderSection) -> None:
        self._sections.remove(section)
        section.setParent(None)
        section.deleteLater()

    def _refresh_visibility(self) -> None:
        has_any = bool(self._sections)
        self._sections_box.setVisible(has_any)
        self._placeholder.setVisible(not has_any)

    def _emit_folders(self) -> None:
        self.folders_changed.emit([str(p) for p in self.folders])

    def _section_for(self, path: Path) -> _FolderSection | None:
        """Deepest section whose root contains ``path``."""
        best = None
        for section in self._sections:
            try:
                path.relative_to(section.root)
            except ValueError:
                continue
            if best is None or len(section.root.parts) > len(best.root.parts):
                best = section
        return best

    def _root_for(self, path: Path) -> Path | None:
        section = self._section_for(path)
        return section.root if section is not None else None

    def add_folder(self, path: Path | str) -> bool:
        """Show ``path`` as an extra folder beneath the project.

        With no project open the folder is proposed *as* the project
        instead (via ``set_as_project_requested``) so git, tests and the
        AI tools have something to work on.
        """
        path = Path(path)
        if not path.is_dir():
            return False
        if self._project_root is None:
            self.set_as_project_requested.emit(path)
            return False
        if any(s.root == path for s in self._sections):
            return False
        section = self._make_section(path, primary=False)
        self._sections.append(section)
        self._sections_box.addWidget(section)
        self._refresh_visibility()
        self._emit_folders()
        logger.info("File explorer: added folder %s", path)
        return True

    def remove_folder(self, path: Path | str) -> bool:
        path = Path(path)
        for section in list(self._sections):
            if not section.primary and section.root == path:
                self._remove_section(section)
                self._refresh_visibility()
                self._emit_folders()
                return True
        return False

    def _show_folder_menu(self, section: _FolderSection, position) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())
        menu.addAction("New File...").triggered.connect(lambda: self._new_file(section.root))
        menu.addAction("New Folder...").triggered.connect(lambda: self._new_folder(section.root))
        menu.addSeparator()
        menu.addAction("Add Folder to Explorer...").triggered.connect(
            self.add_folder_requested.emit
        )
        if not section.primary:
            menu.addAction("Set as Project").triggered.connect(
                lambda: self.set_as_project_requested.emit(section.root)
            )
            menu.addAction("Remove Folder from Explorer").triggered.connect(
                lambda: self.remove_folder(section.root)
            )
        menu.addSeparator()
        menu.addAction("Copy Path").triggered.connect(lambda: self._copy_path(section.root))
        menu.addAction("Reveal in File Manager").triggered.connect(
            lambda: self._reveal_in_file_manager(section.root)
        )
        menu.exec(section.header.mapToGlobal(position))

    def _menu_qss(self) -> str:
        return f"""
            QMenu {{
                background-color: {tc.get("bg_surface_overlay")};
                border: 1px solid {tc.get("border_menu")};
                padding: 4px 0;
                color: {tc.get("text_primary")};
                font-size: {tc.FONT_MD}px;
            }}
            QMenu::item {{
                padding: 4px 28px 4px 12px;
            }}
            QMenu::item:selected {{
                background-color: {tc.get("bg_active")};
            }}
            QMenu::separator {{
                height: 1px;
                background: {tc.get("border_menu")};
                margin: 4px 8px;
            }}
        """

    def _apply_theme_styles(self) -> None:
        self.setStyleSheet(f"background-color: {tc.get('bg_base')};")
        self._header_bar.setStyleSheet(
            f"#explorerHeader {{ background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        self._header.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: bold; color: {tc.get('text_secondary')}; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        for btn in self._action_btns:
            btn.setStyleSheet(f"""
                #explorerActionBtn {{
                    background: transparent; border: none; color: {tc.get("text_primary")};
                    font-size: 15px; border-radius: 3px; padding: 0px;
                    font-weight: normal;
                }}
                #explorerActionBtn:hover {{
                    background: {tc.get("bg_hover")}; color: {tc.get("text_primary")};
                }}
            """)
            # Re-painted on theme change so the strokes pick up the
            # current text/accent colours.
            if btn is getattr(self, "_hidden_btn", None):
                btn.setIcon(shared_icons.draw_eye_icon(crossed=not self.show_hidden))
                btn.setIconSize(QSize(16, 16))
                continue
            icon = _HEADER_ICONS.get(btn.toolTip())
            if icon is not None:
                btn.setIcon(icon())
                btn.setIconSize(QSize(16, 16))
        self._tree_qss = f"""
            QTreeView {{
                background-color: {tc.get("bg_base")};
                border: none;
                outline: none;
                font-size: {tc.FONT_BASE}px;
                show-decoration-selected: 1;
            }}
            QTreeView::item {{
                padding: 0px;
                height: 22px;
                border: none;
            }}
            QTreeView::item:selected {{
                background-color: {tc.get("bg_active")};
            }}
            QTreeView::item:hover:!selected {{
                background-color: {tc.get("bg_hover_subtle")};
            }}
            QTreeView::branch {{
                background-color: {tc.get("bg_base")};
            }}
            QTreeView::branch:has-siblings:!adjoins-item {{
                border-image: none;
            }}
            QTreeView::branch:has-siblings:adjoins-item {{
                border-image: none;
            }}
            QTreeView::branch:!has-children:!has-siblings:adjoins-item {{
                border-image: none;
            }}
            QTreeView::branch:has-children:!has-siblings:closed,
            QTreeView::branch:closed:has-children:has-siblings {{
                image: none;
                border-image: none;
            }}
            QTreeView::branch:open:has-children:!has-siblings,
            QTreeView::branch:open:has-children:has-siblings {{
                image: none;
                border-image: none;
            }}
            QScrollBar:vertical {{
                width: 8px;
                background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {tc.get("scrollbar_thumb")};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {tc.get("scrollbar_thumb_hover")};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """
        for section in self._sections:
            section.apply_styles(self._tree_qss)
        self._sections_box.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; }}"
        )
        self._placeholder.setStyleSheet(f"background-color: {tc.get('bg_base')};")
        self._no_folder.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_BASE}px; "
            "background: transparent;"
        )
        self._open_hint.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_MD}px; background: transparent;"
        )

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    @property
    def show_hidden(self) -> bool:
        return self._proxy_model.show_hidden

    def set_show_hidden(self, show: bool) -> None:
        """Show or hide dotfiles (the header eye button; persisted by the window)."""
        show = bool(show)
        self._proxy_model.set_show_hidden(show)
        btn = getattr(self, "_hidden_btn", None)
        if btn is not None:
            if btn.isChecked() != show:
                btn.blockSignals(True)
                btn.setChecked(show)
                btn.blockSignals(False)
            btn.setIcon(shared_icons.draw_eye_icon(crossed=not show))
            btn.setToolTip("Hide dotfiles" if show else "Show dotfiles (.env, .gitignore, …)")
        if self._event_bus is not None:
            self._event_bus.emit("explorer:show_hidden", show=show)

    def set_root(self, path: Path, *, keep_previous: bool = False) -> None:
        """Make ``path`` the project (first section).

        Extra folders stay. The previous project is dropped unless
        ``keep_previous`` is set, in which case it stays on as an extra
        folder — that's what "Set as Project" on an extra folder does,
        so promoting one never makes the other disappear.
        """
        path = Path(path)
        old = self._primary
        if old is not None and old.root == path:
            old.set_root_index()
            self._refresh_visibility()
            return
        # The new project may already be shown as an extra folder.
        for section in [s for s in self._sections if not s.primary and s.root == path]:
            self._remove_section(section)
        if old is not None:
            if keep_previous:
                old.set_primary(False)
                old.set_root_index()
            else:
                self._remove_section(old)
        self._project_root = path
        section = self._make_section(path, primary=True)
        self._sections.insert(0, section)
        self._sections_box.insertWidget(0, section)
        self._refresh_visibility()
        self._emit_folders()
        logger.info("File explorer root set to: %s", path)

    def clear(self) -> None:
        self._project_root = None
        for section in list(self._sections):
            self._remove_section(section)
        self._refresh_visibility()
        self._emit_folders()

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

    def _on_single_click(self, index: QModelIndex, tree: QTreeView | None = None) -> None:
        """Single click: toggle directories, open files (like VS Code)."""
        path = self._get_path_from_index(index)
        if not path:
            return
        tree = tree or self._tree
        if path.is_dir():
            if tree is None:
                return
            # Toggle expand/collapse on single click
            if tree.isExpanded(index):
                tree.collapse(index)
            else:
                tree.expand(index)
        elif path.is_file() and self.on_file_double_clicked:
            self.on_file_double_clicked(path)

    def _new_file_at_root(self) -> None:
        if self._project_root:
            self._new_file(self._project_root)

    def _new_folder_at_root(self) -> None:
        if self._project_root:
            self._new_folder(self._project_root)

    def _refresh(self) -> None:
        """Force refresh every folder's tree and notify the app."""
        if not self._sections:
            return
        self._fs_model.setRootPath("")  # Force re-read
        for section in self._sections:
            section.set_root_index()
        root_path = str(self._project_root) if self._project_root else ""
        # Emit event so context builder refreshes too
        if self._event_bus and root_path:
            self._event_bus.emit("project_refreshed", path=root_path)
        logger.info("File explorer refreshed: %s", root_path)

    def _collapse_all(self) -> None:
        """Collapse all expanded directories in every folder."""
        for section in self._sections:
            section.tree.collapseAll()

    def _show_context_menu(self, position, section: _FolderSection | None = None) -> None:
        section = section or self._primary
        if section is None:
            return
        tree = section.tree
        index = tree.indexAt(position)
        path = self._get_path_from_index(index) if index.isValid() else section.root

        if path is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())

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

        menu.exec(tree.viewport().mapToGlobal(position))

    def _styled_input(
        self, title: str, label: str, placeholder: str = "", default: str = ""
    ) -> tuple[str, bool]:
        """Show a dark-themed input dialog matching the app style."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setFixedWidth(360)
        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {tc.get("bg_surface")};
                border: 1px solid {tc.get("border_menu")};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {tc.get('text_heading')};"
        )
        layout.addWidget(title_label)

        # Input label
        input_label = QLabel(label)
        input_label.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; color: {tc.get('text_secondary')};"
        )
        layout.addWidget(input_label)

        # Input field
        input_field = QLineEdit()
        input_field.setPlaceholderText(placeholder or label.replace(":", "").strip())
        input_field.setText(default)
        input_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {tc.get("bg_base")}; color: {tc.get("text_heading")};
                border: 1px solid {tc.get("border_input")}; border-radius: 6px;
                padding: 8px 12px; font-size: {tc.FONT_BASE}px;
            }}
            QLineEdit:focus {{ border-color: {tc.get("border_focus")}; }}
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
        cancel_btn.setStyleSheet(f"""
            #styledDialogBtn {{
                background: transparent; color: {tc.get("text_secondary")}; font-size: {tc.FONT_BASE}px;
                padding: 6px 18px; border: 1px solid {tc.get("border_input")}; border-radius: 6px;
            }}
            #styledDialogBtn:hover {{ background: {tc.get("bg_hover")}; color: {tc.get("text_primary")}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Create" if "New" in title else "Rename")
        ok_btn.setObjectName("styledDialogOkBtn")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet(f"""
            #styledDialogOkBtn {{
                background: {tc.get("accent_primary")}; color: white; font-size: {tc.FONT_BASE}px;
                font-weight: 600; padding: 6px 22px; border: none; border-radius: 6px;
            }}
            #styledDialogOkBtn:hover {{ background: {tc.get("accent_primary_hover")}; }}
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
            try:
                new_path.touch()
            except OSError as exc:
                QMessageBox.warning(self, "Error", f"Couldn't create '{name}':\n{exc}")
                return
            logger.info("Created file: %s", new_path)

    def _new_folder(self, parent_dir: Path) -> None:
        name, ok = self._styled_input("New Folder", "Folder name:", "my-folder")
        if ok and name:
            new_path = parent_dir / name
            if new_path.exists():
                QMessageBox.warning(self, "Error", f"'{name}' already exists.")
                return
            try:
                new_path.mkdir(parents=True)
            except OSError as exc:
                QMessageBox.warning(self, "Error", f"Couldn't create folder '{name}':\n{exc}")
                return
            logger.info("Created folder: %s", new_path)

    def _rename(self, path: Path) -> None:
        name, ok = self._styled_input("Rename", "New name:", default=path.name)
        if ok and name and name != path.name:
            new_path = path.parent / name
            if new_path.exists():
                QMessageBox.warning(self, "Error", f"'{name}' already exists.")
                return
            try:
                path.rename(new_path)
            except OSError as exc:
                QMessageBox.warning(self, "Error", f"Couldn't rename '{path.name}':\n{exc}")
                return
            logger.info("Renamed: %s → %s", path.name, name)

    def _delete(self, path: Path) -> None:
        """Show a styled delete confirmation dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Delete")
        dialog.setFixedWidth(380)
        dialog.setStyleSheet(f"QDialog {{ background-color: {tc.get('bg_surface')}; }}")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Delete")
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {tc.get('text_heading')};")
        layout.addWidget(title)

        kind = "folder" if path.is_dir() else "file"
        msg = QLabel(f"Are you sure you want to delete the {kind}\n<b>{path.name}</b>?")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: {tc.FONT_BASE}px; color: {tc.get('text_primary')};")
        layout.addWidget(msg)

        if path.is_dir():
            warn = QLabel("This will delete the folder and all its contents.")
            warn.setStyleSheet(f"font-size: {tc.FONT_MD}px; color: {tc.get('accent_warning')};")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("styledDialogBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            #styledDialogBtn {{
                background: transparent; color: {tc.get("text_secondary")}; font-size: {tc.FONT_BASE}px;
                padding: 6px 18px; border: 1px solid {tc.get("border_input")}; border-radius: 6px;
            }}
            #styledDialogBtn:hover {{ background: {tc.get("bg_hover")}; color: {tc.get("text_primary")}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("styledDeleteBtn")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet(f"""
            #styledDeleteBtn {{
                background: {tc.get("accent_danger")}; color: white; font-size: {tc.FONT_BASE}px;
                font-weight: 600; padding: 6px 22px; border: none; border-radius: 6px;
            }}
            #styledDeleteBtn:hover {{ background: {tc.get("accent_danger_hover")}; }}
        """)
        delete_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(delete_btn)

        layout.addLayout(btn_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    import shutil

                    shutil.rmtree(path)
            except OSError as exc:
                QMessageBox.warning(self, "Error", f"Couldn't delete '{path.name}':\n{exc}")
                return
            logger.info("Deleted: %s", path)

    def _copy_path(self, path: Path) -> None:
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(str(path))

    def _copy_relative_path(self, path: Path) -> None:
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            root = self._root_for(path)
            clipboard.setText(str(path.relative_to(root)) if root else str(path))

    def _reveal_in_file_manager(self, path: Path) -> None:
        import subprocess

        target = path.parent if path.is_file() else path
        try:
            subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            logger.warning("Could not open file manager for: %s", target)

    @property
    def tree(self) -> QTreeView | None:
        """The project's tree view (None when no project is open)."""
        return self._tree
