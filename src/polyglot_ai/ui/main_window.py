"""Main application window — VS Code-style layout with activity bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from pathlib import Path

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QWidget,
)

from polyglot_ai.constants import APP_NAME, APP_VERSION
from polyglot_ai.core.action_registry import ActionRegistry
from polyglot_ai.ui.panels.chat_panel import ChatPanel
from polyglot_ai.ui.panels.cicd_panel import CICDPanel
from polyglot_ai.ui.panels.database_panel import DatabasePanel
from polyglot_ai.ui.panels.docker_panel import DockerPanel
from polyglot_ai.ui.panels.k8s_panel import K8sPanel
from polyglot_ai.ui.panels.editor_panel import EditorPanel
from polyglot_ai.ui.panels.file_explorer import FileExplorer
from polyglot_ai.ui.panels.mcp_sidebar import MCPSidebar
from polyglot_ai.ui.panels.plan_panel import PlanPanel
from polyglot_ai.ui.panels.review_panel import ReviewPanel
from polyglot_ai.ui.panels.usage_panel import UsagePanel
from polyglot_ai.ui.panels.git_panel import GitPanel
from polyglot_ai.ui.panels.search_panel import SearchPanel
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel
from polyglot_ai.ui.panels.test_panel import TestPanel
from polyglot_ai.ui.widgets.activity_bar import ActivityBar
from polyglot_ai.ui.widgets.command_palette import CommandPalette


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1024, 768)
        self.resize(1400, 900)

        # Set window icon explicitly (some Linux DEs ignore app-level icon)
        from PyQt6.QtGui import QIcon

        icon_path = Path(__file__).parent.parent / "resources" / "icons" / "polyglot-ai.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Create panels
        self._file_explorer = FileExplorer()
        self._search_panel = SearchPanel()
        self._mcp_sidebar = MCPSidebar()
        self._git_panel = GitPanel()
        self._database_panel = DatabasePanel()
        self._docker_panel = DockerPanel()
        self._k8s_panel = K8sPanel()
        self._test_panel = TestPanel()
        self._editor_panel = EditorPanel()
        self._chat_panel = ChatPanel()
        self._review_panel = ReviewPanel()
        self._cicd_panel = CICDPanel()
        self._terminal_panel = TerminalPanel()

        # ── Activity bar (far left) ──
        self._activity_bar = ActivityBar()
        self._activity_bar.view_changed.connect(self._on_activity_changed)

        # ── Sidebar stack (switches based on activity bar) ──
        self._sidebar_stack = QStackedWidget()
        self._sidebar_stack.addWidget(self._file_explorer)  # 0: files
        self._sidebar_stack.addWidget(self._search_panel)  # 1: search
        self._sidebar_stack.addWidget(self._git_panel)  # 2: git
        self._sidebar_stack.addWidget(self._mcp_sidebar)  # 3: mcp
        self._sidebar_stack.addWidget(self._database_panel)  # 4: database
        self._sidebar_stack.addWidget(self._docker_panel)  # 5: docker
        self._sidebar_stack.addWidget(self._k8s_panel)  # 6: kubernetes
        self._sidebar_stack.addWidget(self._test_panel)  # 7: tests
        self._sidebar_stack.setMinimumWidth(200)

        # ── Right side: Chat + Review + Plan + Changes tabs ──
        from PyQt6.QtWidgets import QTabWidget

        self._right_tabs = QTabWidget()
        self._right_tabs.setTabPosition(QTabWidget.TabPosition.North)
        from polyglot_ai.ui import theme_colors as tc

        self._right_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; }}
            QTabBar::tab {{
                background: {tc.get("bg_surface")}; color: {tc.get("text_tertiary")};
                padding: 8px 16px; border: none;
                border-bottom: 2px solid transparent;
                font-size: {tc.FONT_MD}px; font-weight: 600;
            }}
            QTabBar::tab:selected {{
                color: {tc.get("text_heading")}; border-bottom: 2px solid {tc.get("accent_primary")};
            }}
            QTabBar::tab:hover:!selected {{
                color: {tc.get("text_primary")}; background: {tc.get("bg_hover_subtle")};
            }}
        """)
        from polyglot_ai.ui.panels.changeset_panel import ChangesetPanel

        self._changeset_panel = ChangesetPanel()
        self._plan_panel = PlanPanel()
        self._usage_panel = UsagePanel()

        self._right_tabs.addTab(self._chat_panel, "💬 Chat")
        self._right_tabs.addTab(self._plan_panel, "📋 Plan")
        self._right_tabs.addTab(self._changeset_panel, "📝 Changes")
        self._right_tabs.addTab(self._review_panel, "🔍 Review")
        self._right_tabs.addTab(self._usage_panel, "📊 Usage")
        self._right_tabs.addTab(self._cicd_panel, "🔄 CI/CD")

        # ── Main layout: ActivityBar | Sidebar | Center | RightTabs ──
        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Activity bar
        central_layout.addWidget(self._activity_bar)

        # Sidebar + Editor/Terminal + Right tabs in a splitter
        self._center_splitter = QSplitter(Qt.Orientation.Vertical)
        self._center_splitter.addWidget(self._editor_panel)
        self._center_splitter.addWidget(self._terminal_panel)
        self._center_splitter.setSizes([700, 300])

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._sidebar_stack)
        self._main_splitter.addWidget(self._center_splitter)
        self._main_splitter.addWidget(self._right_tabs)
        self._main_splitter.setSizes([250, 700, 350])

        central_layout.addWidget(self._main_splitter)
        self.setCentralWidget(central)

        self._sidebar_visible = True
        self._last_sidebar_view = "files"

        # Action registry & command palette
        self._action_registry = ActionRegistry()
        self._command_palette: CommandPalette | None = None

        self._setup_menus()
        self._setup_statusbar()
        self._connect_actions()
        self._register_actions()

    def _on_activity_changed(self, view_name: str) -> None:
        """Handle activity bar icon clicks."""
        if view_name == "settings":
            if hasattr(self, "_action_settings"):
                self._action_settings.trigger()
            return

        view_map = {
            "files": 0,
            "search": 1,
            "git": 2,
            "mcp": 3,
            "database": 4,
            "docker": 5,
            "kubernetes": 6,
            "tests": 7,
        }
        index = view_map.get(view_name, 0)

        if view_name == self._last_sidebar_view and self._sidebar_visible:
            # Toggle sidebar off
            self._sidebar_stack.hide()
            self._sidebar_visible = False
        else:
            self._sidebar_stack.setCurrentIndex(index)
            self._sidebar_stack.show()
            self._sidebar_visible = True
            self._last_sidebar_view = view_name

    def _show_cicd_tab(self) -> None:
        """Switch to the CI/CD tab in the right panel."""
        index = self._right_tabs.indexOf(self._cicd_panel)
        if index >= 0:
            self._right_tabs.setCurrentIndex(index)

    def _setup_menus(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        self._action_new = QAction("&New File", self)
        self._action_new.setShortcut(QKeySequence.StandardKey.New)
        file_menu.addAction(self._action_new)

        self._action_open_file = QAction("&Open File...", self)
        self._action_open_file.setShortcut(QKeySequence.StandardKey.Open)
        file_menu.addAction(self._action_open_file)

        self._action_open_project = QAction("Open &Project...", self)
        self._action_open_project.setShortcut(QKeySequence("Ctrl+Shift+O"))
        file_menu.addAction(self._action_open_project)

        file_menu.addSeparator()

        self._action_save = QAction("&Save", self)
        self._action_save.setShortcut(QKeySequence.StandardKey.Save)
        file_menu.addAction(self._action_save)

        self._action_save_all = QAction("Save &All", self)
        self._action_save_all.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addAction(self._action_save_all)

        file_menu.addSeparator()

        self._action_close_tab = QAction("&Close Tab", self)
        self._action_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        file_menu.addAction(self._action_close_tab)

        file_menu.addSeparator()

        self._action_settings = QAction("Se&ttings...", self)
        self._action_settings.setShortcut(QKeySequence("Ctrl+,"))
        file_menu.addAction(self._action_settings)

        file_menu.addSeparator()

        self._action_quit = QAction("&Quit", self)
        self._action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self._action_quit.triggered.connect(self.close)
        file_menu.addAction(self._action_quit)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        self._action_undo = QAction("&Undo", self)
        self._action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(self._action_undo)

        self._action_redo = QAction("&Redo", self)
        self._action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(self._action_redo)

        edit_menu.addSeparator()

        self._action_cut = QAction("Cu&t", self)
        self._action_cut.setShortcut(QKeySequence.StandardKey.Cut)
        edit_menu.addAction(self._action_cut)

        self._action_copy = QAction("&Copy", self)
        self._action_copy.setShortcut(QKeySequence.StandardKey.Copy)
        edit_menu.addAction(self._action_copy)

        self._action_paste = QAction("&Paste", self)
        self._action_paste.setShortcut(QKeySequence.StandardKey.Paste)
        edit_menu.addAction(self._action_paste)

        edit_menu.addSeparator()

        self._action_find = QAction("&Find...", self)
        self._action_find.setShortcut(QKeySequence.StandardKey.Find)
        edit_menu.addAction(self._action_find)

        self._action_replace = QAction("&Replace...", self)
        self._action_replace.setShortcut(QKeySequence("Ctrl+H"))
        edit_menu.addAction(self._action_replace)

        # View menu
        view_menu = menubar.addMenu("&View")

        self._action_toggle_explorer = QAction("&Explorer", self)
        self._action_toggle_explorer.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self._action_toggle_explorer.triggered.connect(lambda: self._on_activity_changed("files"))
        view_menu.addAction(self._action_toggle_explorer)

        self._action_toggle_search = QAction("&Search", self)
        self._action_toggle_search.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self._action_toggle_search.triggered.connect(lambda: self._on_activity_changed("search"))
        view_menu.addAction(self._action_toggle_search)

        self._action_toggle_git = QAction("Source &Control", self)
        self._action_toggle_git.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self._action_toggle_git.triggered.connect(lambda: self._on_activity_changed("git"))
        view_menu.addAction(self._action_toggle_git)

        self._action_toggle_mcp = QAction("&MCP Servers", self)
        self._action_toggle_mcp.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self._action_toggle_mcp.triggered.connect(lambda: self._on_activity_changed("mcp"))
        view_menu.addAction(self._action_toggle_mcp)

        self._action_toggle_database = QAction("&Database Explorer", self)
        self._action_toggle_database.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self._action_toggle_database.triggered.connect(
            lambda: self._on_activity_changed("database")
        )
        view_menu.addAction(self._action_toggle_database)

        self._action_toggle_cicd = QAction("CI/CD &Inspector", self)
        self._action_toggle_cicd.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self._action_toggle_cicd.triggered.connect(self._show_cicd_tab)
        view_menu.addAction(self._action_toggle_cicd)

        self._action_toggle_docker = QAction("Doc&ker", self)
        self._action_toggle_docker.setShortcut(QKeySequence("Ctrl+Shift+K"))
        self._action_toggle_docker.triggered.connect(lambda: self._on_activity_changed("docker"))
        view_menu.addAction(self._action_toggle_docker)

        self._action_toggle_k8s = QAction("&Kubernetes", self)
        self._action_toggle_k8s.setShortcut(QKeySequence("Ctrl+Shift+8"))
        self._action_toggle_k8s.triggered.connect(lambda: self._on_activity_changed("kubernetes"))
        view_menu.addAction(self._action_toggle_k8s)

        view_menu.addSeparator()

        self._action_toggle_terminal = QAction("&Terminal", self)
        self._action_toggle_terminal.setCheckable(True)
        self._action_toggle_terminal.setChecked(True)
        self._action_toggle_terminal.setShortcut(QKeySequence("Ctrl+`"))
        self._action_toggle_terminal.toggled.connect(self._terminal_panel.setVisible)
        view_menu.addAction(self._action_toggle_terminal)

        self._action_toggle_chat = QAction("&AI Chat", self)
        self._action_toggle_chat.setCheckable(True)
        self._action_toggle_chat.setChecked(True)
        self._action_toggle_chat.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._action_toggle_chat.toggled.connect(self._right_tabs.setVisible)
        view_menu.addAction(self._action_toggle_chat)

        view_menu.addSeparator()

        self._action_toggle_theme = QAction("Toggle &Dark/Light Theme", self)
        view_menu.addAction(self._action_toggle_theme)

        # AI menu
        ai_menu = menubar.addMenu("&AI")

        self._action_new_chat = QAction("&New Conversation", self)
        self._action_new_chat.setShortcut(QKeySequence("Ctrl+Shift+N"))
        ai_menu.addAction(self._action_new_chat)

        self._action_clear_history = QAction("&Clear History", self)
        ai_menu.addAction(self._action_clear_history)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        self._action_about = QAction("&About", self)
        help_menu.addAction(self._action_about)

        self._action_shortcuts = QAction("&Keyboard Shortcuts", self)
        help_menu.addAction(self._action_shortcuts)

        # Command palette shortcut
        self._action_command_palette = QAction("Command Palette", self)
        self._action_command_palette.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self._action_command_palette.triggered.connect(self._show_command_palette)
        self.addAction(self._action_command_palette)

    def _setup_statusbar(self) -> None:
        self.statusBar().showMessage("Ready")

    def _connect_actions(self) -> None:
        self._action_new.triggered.connect(self._editor_panel.new_file)
        self._action_open_file.triggered.connect(lambda: self._editor_panel.open_file())
        self._action_save.triggered.connect(self._editor_panel.save_current)
        self._action_save_all.triggered.connect(self._editor_panel.save_all)
        self._action_close_tab.triggered.connect(
            lambda: self._editor_panel.close_tab(self._editor_panel.currentIndex())
        )
        self._action_open_project.triggered.connect(self._open_project)

        # Wire file explorer double-click to open file in editor
        self._file_explorer.on_file_double_clicked = self._editor_panel.open_file

        # Wire search panel to open files
        self._search_panel.on_file_selected = self._editor_panel.open_file

        # Edit actions
        self._action_undo.triggered.connect(self._forward_undo)
        self._action_redo.triggered.connect(self._forward_redo)
        self._action_cut.triggered.connect(self._forward_cut)
        self._action_copy.triggered.connect(self._forward_copy)
        self._action_paste.triggered.connect(self._forward_paste)

        self._editor_panel.currentChanged.connect(self._on_editor_tab_changed)

    def _get_edit_widget(self):
        """Get the active text editor widget from current tab (EditorTab or DocumentTab)."""
        tab = self._editor_panel.get_current_tab()
        if not tab:
            return None
        if hasattr(tab, "editor"):
            return tab.editor  # EditorTab (QScintilla)
        if hasattr(tab, "source_editor"):
            return tab.source_editor  # DocumentTab (QPlainTextEdit)
        return None

    def _forward_undo(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.undo()

    def _forward_redo(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.redo()

    def _forward_cut(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.cut()

    def _forward_copy(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.copy()

    def _forward_paste(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.paste()

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Open Project", "", QFileDialog.Option.ShowDirsOnly
        )
        if not directory:
            return
        path = Path(directory)
        # Prefer the project-manager activator wired by ui_wiring — it
        # fires project:opened so the git, CI, Docker, database, and
        # MCP panels all hear about the new project. Falls back to the
        # legacy file-explorer-only path if the activator isn't wired
        # yet (shouldn't happen in normal startup).
        activator = getattr(self, "_activate_project", None)
        if activator is not None:
            activator(path)
            return
        self._file_explorer.set_root(path)
        self._search_panel.set_project_root(path)
        self.setWindowTitle(f"{path.name} — {APP_NAME} v{APP_VERSION}")
        self.statusBar().showMessage(f"Project: {path}")

    def _on_editor_tab_changed(self, index: int) -> None:
        tab = self._editor_panel.get_current_tab()
        if tab and tab.file_path:
            self.statusBar().showMessage(str(tab.file_path))
        else:
            self.statusBar().showMessage("Ready")

    # ── Command palette ────────────────────────────────────────────

    def _show_command_palette(self) -> None:
        palette = CommandPalette(self._action_registry, self)
        palette.show()

    def _register_actions(self) -> None:
        """Register all menu actions into the action registry."""
        reg = self._action_registry
        reg.register("file.new", "New File", self._editor_panel.new_file, "File", "Ctrl+N")
        reg.register(
            "file.open", "Open File", lambda: self._editor_panel.open_file(), "File", "Ctrl+O"
        )
        reg.register(
            "file.open_project", "Open Project", self._open_project, "File", "Ctrl+Shift+O"
        )
        reg.register("file.save", "Save", self._editor_panel.save_current, "File", "Ctrl+S")
        reg.register(
            "file.save_all", "Save All", self._editor_panel.save_all, "File", "Ctrl+Shift+S"
        )
        reg.register(
            "view.explorer",
            "Show Explorer",
            lambda: self._on_activity_changed("files"),
            "View",
            "Ctrl+Shift+E",
        )
        reg.register(
            "view.search",
            "Show Search",
            lambda: self._on_activity_changed("search"),
            "View",
            "Ctrl+Shift+F",
        )
        reg.register(
            "view.git",
            "Show Source Control",
            lambda: self._on_activity_changed("git"),
            "View",
            "Ctrl+Shift+G",
        )
        reg.register(
            "view.mcp",
            "Show MCP Servers",
            lambda: self._on_activity_changed("mcp"),
            "View",
            "Ctrl+Shift+M",
        )
        reg.register(
            "view.database",
            "Show Database Explorer",
            lambda: self._on_activity_changed("database"),
            "View",
            "Ctrl+Shift+D",
        )
        reg.register(
            "view.cicd",
            "Show CI/CD Inspector",
            self._show_cicd_tab,
            "View",
            "Ctrl+Shift+I",
        )
        reg.register(
            "view.docker",
            "Show Docker",
            lambda: self._on_activity_changed("docker"),
            "View",
            "Ctrl+Shift+K",
        )
        reg.register(
            "view.kubernetes",
            "Show Kubernetes",
            lambda: self._on_activity_changed("kubernetes"),
            "View",
            "Ctrl+Shift+8",
        )
        reg.register(
            "view.terminal",
            "Toggle Terminal",
            lambda: self._action_toggle_terminal.toggle(),
            "View",
            "Ctrl+`",
        )
        reg.register(
            "view.chat",
            "Toggle AI Chat",
            lambda: self._action_toggle_chat.toggle(),
            "View",
            "Ctrl+Shift+A",
        )
        reg.register(
            "view.theme",
            "Toggle Dark/Light Theme",
            lambda: self._action_toggle_theme.trigger(),
            "View",
        )
        reg.register(
            "ai.new_chat",
            "New Conversation",
            lambda: self._action_new_chat.trigger(),
            "AI",
            "Ctrl+Shift+N",
        )
        reg.register("edit.undo", "Undo", self._forward_undo, "Edit", "Ctrl+Z")
        reg.register("edit.redo", "Redo", self._forward_redo, "Edit", "Ctrl+Shift+Z")

    @property
    def action_registry(self) -> ActionRegistry:
        return self._action_registry

    # ── Shutdown ────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Ensure clean shutdown — session save happens in app.py after loop stops."""
        from PyQt6.QtWidgets import QApplication

        event.accept()
        QApplication.quit()

    # ── Session save / restore ────────────────────────────────────

    def save_session(self) -> dict:
        """Collect current session state as a dict."""
        # Open editor tabs
        open_tabs = []
        for i in range(self._editor_panel.count()):
            tab = self._editor_panel.widget(i)
            if hasattr(tab, "file_path") and tab.file_path:
                open_tabs.append(str(tab.file_path))

        return {
            "session.open_tabs": open_tabs,
            "session.active_tab_index": self._editor_panel.currentIndex(),
            "session.splitter_sizes": {
                "main": self._main_splitter.sizes(),
                "center": self._center_splitter.sizes(),
            },
            "session.window_geometry": {
                "x": self.x(),
                "y": self.y(),
                "w": self.width(),
                "h": self.height(),
            },
        }

    def restore_session(self, session_data: dict) -> None:
        """Restore session from saved settings."""
        # Window geometry
        geo = session_data.get("session.window_geometry")
        if geo and isinstance(geo, dict) and "w" in geo:
            self.setGeometry(geo.get("x", 100), geo.get("y", 100), geo["w"], geo["h"])

        # Splitter sizes
        sizes = session_data.get("session.splitter_sizes")
        if sizes and isinstance(sizes, dict):
            main_sizes = sizes.get("main")
            center_sizes = sizes.get("center")
            if main_sizes and len(main_sizes) == 3:
                self._main_splitter.setSizes(main_sizes)
            if center_sizes and len(center_sizes) == 2:
                self._center_splitter.setSizes(center_sizes)

        # Open tabs
        tabs = session_data.get("session.open_tabs", [])
        if isinstance(tabs, list):
            for tab_path in tabs:
                p = Path(tab_path)
                if p.exists():
                    self._editor_panel.open_file(p)

        # Active tab
        active_idx = session_data.get("session.active_tab_index", 0)
        if isinstance(active_idx, int) and 0 <= active_idx < self._editor_panel.count():
            self._editor_panel.setCurrentIndex(active_idx)

    # Public accessors for panels
    @property
    def file_explorer(self) -> FileExplorer:
        return self._file_explorer

    @property
    def search_panel(self) -> SearchPanel:
        return self._search_panel

    @property
    def mcp_sidebar(self) -> MCPSidebar:
        return self._mcp_sidebar

    @property
    def database_panel(self) -> DatabasePanel:
        return self._database_panel

    @property
    def docker_panel(self) -> DockerPanel:
        return self._docker_panel

    @property
    def k8s_panel(self) -> K8sPanel:
        return self._k8s_panel

    @property
    def test_panel(self) -> TestPanel:
        return self._test_panel

    @property
    def editor_panel(self) -> EditorPanel:
        return self._editor_panel

    @property
    def chat_panel(self) -> ChatPanel:
        return self._chat_panel

    @property
    def plan_panel(self) -> PlanPanel:
        return self._plan_panel

    @property
    def review_panel(self) -> ReviewPanel:
        return self._review_panel

    @property
    def changeset_panel(self):
        return self._changeset_panel

    @property
    def usage_panel(self) -> UsagePanel:
        return self._usage_panel

    @property
    def cicd_panel(self) -> CICDPanel:
        return self._cicd_panel

    @property
    def git_panel(self) -> GitPanel:
        return self._git_panel

    @property
    def terminal_panel(self) -> TerminalPanel:
        return self._terminal_panel
