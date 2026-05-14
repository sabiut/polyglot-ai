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
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel, TerminalWidget
from polyglot_ai.ui.panels.arduino_panel import ArduinoPanel, ArduinoWindow
from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow
from polyglot_ai.ui.panels.tasks_panel import TasksPanel
from polyglot_ai.ui.panels.test_panel import TestPanel
from polyglot_ai.ui.panels.today_panel import TodayPanel
from polyglot_ai.ui.widgets.activity_bar import ActivityBar
from polyglot_ai.ui.widgets.command_palette import CommandPalette


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")

        # Adaptive minimum and initial size based on the actual screen
        from polyglot_ai.ui import theme_colors as _tc

        size_class = _tc.screen_size_class()
        if size_class == "sm":
            self.setMinimumSize(900, 600)
        else:
            self.setMinimumSize(1024, 768)

        x, y, w, h = _tc.initial_window_geometry()
        self.setGeometry(x, y, w, h)

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
        self._tasks_panel = TasksPanel()
        self._today_panel = TodayPanel()
        self._arduino_panel = ArduinoPanel()
        self._editor_panel = EditorPanel()
        # Wire the editor panel into the test panel so coverage runs
        # paint hit/miss bars in the editor gutter. This is the only
        # cross-panel dependency for the coverage feature; everything
        # else flows through the EventBus.
        self._test_panel.set_editor_panel(self._editor_panel)
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
        self._sidebar_stack.addWidget(self._tasks_panel)  # 8: tasks
        self._sidebar_stack.addWidget(self._today_panel)  # 9: today
        # Note: ``_arduino_panel`` is intentionally NOT added to the
        # sidebar stack. The chip icon and Ctrl+Shift+A pop it as a
        # standalone window via ``_show_arduino_window`` — the four-
        # step wizard is too tall to be useful in the 200 px pane.
        self._sidebar_stack.setMinimumWidth(200)

        # Lazy-created on first ``_show_arduino_window`` call. Stored
        # so subsequent opens raise the existing window (preserving
        # chosen starter, status feed, etc.) instead of spawning a
        # blank one.
        self._arduino_window: ArduinoWindow | None = None

        # Same lazy-and-keep pattern for the Video editor window.
        # Once the user picks a clip and types a prompt, closing the
        # window shouldn't lose that state.
        self._video_panel: VideoPanel | None = None
        self._video_window: VideoWindow | None = None

        # ── Right side: Chat + Review + Plan + Changes tabs ──
        from PyQt6.QtWidgets import QTabWidget

        self._right_tabs = QTabWidget()
        self._right_tabs.setTabPosition(QTabWidget.TabPosition.North)
        from polyglot_ai.ui import theme_colors as tc

        # Tab bar kept deliberately slim: 5px vertical padding + FONT_SM
        # gives roughly 24px row height so the bar stops competing with
        # the panel content for vertical real estate.
        self._right_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; }}
            QTabBar::tab {{
                background: {tc.get("bg_surface")}; color: {tc.get("text_tertiary")};
                padding: 5px 10px; border: none;
                border-bottom: 2px solid transparent;
                font-size: {tc.FONT_SM}px; font-weight: 600;
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

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._sidebar_stack)
        self._main_splitter.addWidget(self._center_splitter)
        self._main_splitter.addWidget(self._right_tabs)

        # Adaptive splitter sizes based on screen
        from polyglot_ai.ui import theme_colors as _tc_split

        splits = _tc_split.initial_splitter_sizes(self.width())
        self._main_splitter.setSizes(splits["main"])
        self._center_splitter.setSizes(splits["center"])

        # Remembered sidebar widths so the expand button can toggle
        # between "normal" (whatever the user last had it at) and
        # "expanded" (much wider, for panels like Tests where output
        # benefits from more horizontal room). ``_sidebar_normal_sizes``
        # is captured the first time the user clicks expand.
        self._sidebar_normal_sizes: list[int] | None = None
        self._test_panel.expand_requested.connect(self._on_test_panel_expand_requested)

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

        # Arduino is the one panel that lives in its own window
        # rather than the sidebar — the wizard layout needs the
        # space and a separate window is more inviting for kids.
        if view_name == "arduino":
            self._show_arduino_window()
            return

        # Video editor follows the same standalone-window pattern —
        # the file picker + prompt area + status feed don't read
        # well wedged into the 200 px sidebar.
        if view_name == "video":
            self._show_video_window()
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
            "tasks": 8,
            "today": 9,
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

    def _on_test_panel_expand_requested(self) -> None:
        """Toggle the sidebar between its normal width and an expanded one.

        Triggered by the ⟷ button in the Tests panel header. Drags on
        the splitter handle still work for fine-grained sizing — this
        toggles between a remembered "normal" layout and a ~50%-sidebar
        "expanded" layout, stealing space from the right tabs first
        and then the editor (kept ≥ 240 px so the editing surface
        never disappears entirely).
        """
        import logging as _logging

        sizes = self._main_splitter.sizes()
        if not sizes or len(sizes) < 3:
            # The main splitter is built with exactly three children
            # (sidebar, center, right tabs). Anything else means the
            # layout has changed in a way this handler doesn't know
            # about — log so the no-op is debuggable instead of silent.
            _logging.getLogger(__name__).warning(
                "test_panel expand: unexpected splitter shape (%d panes, expected 3) — "
                "skipping resize",
                len(sizes),
            )
            return
        sidebar_w, _center_w, _right_w = sizes[0], sizes[1], sizes[2]
        total = sum(sizes)

        # Heuristic for "currently expanded": sidebar > 40% of the
        # window. Switch back to the remembered normal sizes.
        if self._sidebar_normal_sizes is not None and sidebar_w > total * 0.4:
            self._main_splitter.setSizes(self._sidebar_normal_sizes)
            self._sidebar_normal_sizes = None
            return

        # Expand: remember current sizes, then take ~50% of the window
        # for the sidebar by stealing from the right tabs first, then
        # the center if needed. Keep the editor visible (≥ 240 px) so
        # the user doesn't lose the editing surface entirely.
        self._sidebar_normal_sizes = list(sizes)
        target_sidebar = max(sidebar_w, int(total * 0.5))
        min_center = 240
        right_w = sizes[2]
        steal_from_right = min(right_w - 240, target_sidebar - sidebar_w)
        if steal_from_right < 0:
            steal_from_right = 0
        new_right = right_w - steal_from_right
        remaining_need = (target_sidebar - sidebar_w) - steal_from_right
        new_center = max(min_center, sizes[1] - max(0, remaining_need))
        new_sidebar = total - new_center - new_right
        self._main_splitter.setSizes([new_sidebar, new_center, new_right])

    def _show_arduino_window(self) -> None:
        """Open the Arduino panel as a standalone top-level window.

        Lazily constructs the window the first time it's requested
        and stores it on ``self`` so re-clicks raise the same window
        instead of spawning a fresh blank one — the kid keeps their
        starter selection, language toggle, and status feed across
        opens.
        """
        if self._arduino_window is None:
            self._arduino_window = ArduinoWindow(self._arduino_panel, self)
        self._arduino_window.show_and_raise()

    def _show_video_window(self) -> None:
        """Open the Video editor as a standalone top-level window.

        Constructs the panel and window lazily on first click —
        until then we don't pay the import cost or build the QSS-
        heavy step cards. Re-clicks raise the same window so the
        user's loaded clip and prompt survive a close → reopen
        cycle.
        """
        if self._video_panel is None:
            self._video_panel = VideoPanel()
        if self._video_window is None:
            self._video_window = VideoWindow(self._video_panel, self)
        self._video_window.show_and_raise()

    def _on_show_onboarding(self) -> None:
        """Re-launch the onboarding wizard from the Help menu.

        The dialog auto-marks itself as "seen" on close so users
        aren't nagged on every launch — but that means there's no
        other path back to it after the first dismissal. This
        menu item is the formal escape hatch for users who skipped
        too quickly or want a refresher tour.
        """
        from polyglot_ai.ui.dialogs.onboarding_dialog import OnboardingDialog

        try:
            dlg = OnboardingDialog(self)
            dlg.exec()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to show onboarding")

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

        self._action_toggle_tests = QAction("&Tests", self)
        self._action_toggle_tests.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self._action_toggle_tests.triggered.connect(lambda: self._on_activity_changed("tests"))
        view_menu.addAction(self._action_toggle_tests)

        self._action_toggle_tasks = QAction("Tas&ks", self)
        self._action_toggle_tasks.setShortcut(QKeySequence("Ctrl+Shift+J"))
        self._action_toggle_tasks.triggered.connect(lambda: self._on_activity_changed("tasks"))
        view_menu.addAction(self._action_toggle_tasks)

        self._action_toggle_today = QAction("To&day", self)
        self._action_toggle_today.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self._action_toggle_today.triggered.connect(lambda: self._on_activity_changed("today"))
        view_menu.addAction(self._action_toggle_today)

        self._action_toggle_arduino = QAction("&Arduino", self)
        self._action_toggle_arduino.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._action_toggle_arduino.triggered.connect(lambda: self._on_activity_changed("arduino"))
        view_menu.addAction(self._action_toggle_arduino)

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

        # Re-trigger the onboarding wizard. Since the dialog now
        # auto-marks itself as "seen" on close (so users aren't
        # nagged on every launch), there's no other path back to
        # it after the first dismissal — except this menu item.
        # Useful for users who skipped too quickly and want a tour.
        self._action_show_onboarding = QAction("Show &Onboarding…", self)
        self._action_show_onboarding.triggered.connect(self._on_show_onboarding)
        help_menu.addAction(self._action_show_onboarding)

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
            return tab.editor  # QScintilla
        if hasattr(tab, "source_editor"):
            return tab.source_editor  # DocumentTab (QPlainTextEdit)
        return None

    def _focused_terminal(self) -> "TerminalWidget | None":
        """Return the focused TerminalWidget if any.

        Edit-menu QActions for Ctrl+C/X/V/Z fire as window-level
        shortcuts, which means they consume the key event before any
        focused child widget sees it. When the focus is a terminal,
        a plain Ctrl+C should send SIGINT, not "copy" — so we detect
        that case here and forward the control byte to the PTY.
        """
        from PyQt6.QtWidgets import QApplication

        widget = QApplication.focusWidget()
        if isinstance(widget, TerminalWidget):
            return widget
        return None

    def _terminal_write(self, term: "TerminalWidget", data: bytes) -> None:
        pty = getattr(term, "_pty", None)
        if pty is not None and getattr(pty, "is_running", False):
            pty.write(data)

    def _forward_undo(self) -> None:
        # Ctrl+Z in a terminal sends SIGTSTP (suspend foreground job).
        term = self._focused_terminal()
        if term is not None:
            self._terminal_write(term, b"\x1a")
            return
        w = self._get_edit_widget()
        if w:
            w.undo()

    def _forward_redo(self) -> None:
        w = self._get_edit_widget()
        if w:
            w.redo()

    def _forward_cut(self) -> None:
        # Ctrl+X in readline-style shells is the start of a key
        # sequence (e.g. ``C-x C-e`` to edit the command line); pass
        # it through verbatim instead of calling cut().
        term = self._focused_terminal()
        if term is not None:
            self._terminal_write(term, b"\x18")
            return
        w = self._get_edit_widget()
        if w:
            w.cut()

    def _forward_copy(self) -> None:
        # Ctrl+C in a terminal sends SIGINT. Use Ctrl+Shift+C for
        # clipboard copy (handled inside TerminalWidget.keyPressEvent).
        term = self._focused_terminal()
        if term is not None:
            self._terminal_write(term, b"\x03")
            return
        w = self._get_edit_widget()
        if w:
            w.copy()

    def _forward_paste(self) -> None:
        # Ctrl+V in readline is "verbatim insert" (next key is taken
        # literally). Forward as a control byte; users who want to
        # paste clipboard text should use Ctrl+Shift+V.
        term = self._focused_terminal()
        if term is not None:
            self._terminal_write(term, b"\x16")
            return
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
        self._arduino_panel.set_project_root(path)
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

        # Task commands — palette-driven entry points so users can
        # create/open/block tasks without touching the sidebar. Each
        # action delegates to the TasksPanel public API so the same
        # behaviour runs whether it's triggered by a click or a
        # palette selection.
        reg.register(
            "task.new",
            "Task: New",
            self._task_cmd_new,
            "Tasks",
            # No keybinding on purpose — Ctrl+Shift+T is already the
            # Tests panel shortcut. Users reach Task: New via the
            # command palette (Ctrl+Shift+P → "Task: New") or by
            # clicking the + button in the Tasks sidebar.
        )
        reg.register(
            "task.new_with_details",
            "Task: New (with kind/description)",
            self._task_cmd_new_with_details,
            "Tasks",
        )
        reg.register(
            "task.switch",
            "Task: Switch Active…",
            self._task_cmd_switch,
            "Tasks",
        )
        reg.register(
            "task.open_active",
            "Task: Open Active Task Detail",
            self._task_cmd_open_active,
            "Tasks",
        )
        reg.register(
            "task.mark_done",
            "Task: Mark Active as Done",
            self._task_cmd_mark_done,
            "Tasks",
        )
        reg.register(
            "task.block",
            "Task: Block Active Task…",
            self._task_cmd_block,
            "Tasks",
        )
        reg.register(
            "task.show_panel",
            "Task: Show Tasks Panel",
            lambda: self._on_activity_changed("tasks"),
            "Tasks",
        )
        reg.register(
            "task.show_today",
            "Task: Show Today Panel",
            lambda: self._on_activity_changed("today"),
            "Tasks",
        )

    @property
    def action_registry(self) -> ActionRegistry:
        return self._action_registry

    # ── Task command callbacks ──────────────────────────────────────
    #
    # Each of these is the thin bridge between an action registry
    # entry and the TasksPanel public API. The "show panel first,
    # then do the thing" pattern means the user's eye lands on the
    # panel that's about to change — important when the command
    # palette triggers an action whose UI lives somewhere off-screen.

    def _task_cmd_new(self) -> None:
        if self._tasks_panel is None:
            return
        self._on_activity_changed("tasks")
        self._tasks_panel.trigger_new_task()

    def _task_cmd_new_with_details(self) -> None:
        if self._tasks_panel is None:
            return
        self._on_activity_changed("tasks")
        self._tasks_panel.trigger_new_task_dialog()

    def _task_cmd_switch(self) -> None:
        """Pop a tiny task picker for switching the active task.

        Uses QInputDialog for zero-dependency simplicity — the full
        palette already provides fuzzy search, and a dedicated task
        switcher would be a v2 nicety.
        """
        import logging as _logging

        from PyQt6.QtWidgets import QInputDialog

        if self._tasks_panel is None:
            return
        tm = getattr(self._tasks_panel, "_task_manager", None)
        if tm is None or tm.project_root is None:
            return
        try:
            tasks = [t for t in tm.list_tasks() if str(t.state.value) != "archived"]
        except Exception:
            _logging.getLogger(__name__).exception("main_window: task switch failed")
            return
        if not tasks:
            return
        labels = [f"{t.title}  ·  {t.state.value}" for t in tasks]
        choice, ok = QInputDialog.getItem(
            self,
            "Switch active task",
            "Task:",
            labels,
            current=0,
            editable=False,
        )
        if not ok or not choice:
            return
        try:
            idx = labels.index(choice)
        except ValueError:
            return
        tm.set_active(tasks[idx].id)
        self._on_activity_changed("tasks")

    def _task_cmd_open_active(self) -> None:
        if self._tasks_panel is None:
            return
        self._on_activity_changed("tasks")
        self._tasks_panel.open_active_task_detail()

    def _task_cmd_mark_done(self) -> None:
        if self._tasks_panel is None:
            return
        self._tasks_panel.mark_active_done()

    def _task_cmd_block(self) -> None:
        if self._tasks_panel is None:
            return
        self._tasks_panel.block_active_task()

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
        # Window geometry — clamp to actual screen so the window isn't
        # placed off-screen when switching between monitors / resolutions.
        geo = session_data.get("session.window_geometry")
        if geo and isinstance(geo, dict) and "w" in geo:
            x, y, w, h = geo.get("x", 100), geo.get("y", 100), geo["w"], geo["h"]
            try:
                from PyQt6.QtWidgets import QApplication

                screen = QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    # Clamp size to available screen
                    w = min(w, avail.width())
                    h = min(h, avail.height())
                    # Ensure window is visible (at least 100px on-screen)
                    if x + w < avail.x() + 100 or x > avail.right() - 100:
                        x = avail.x() + (avail.width() - w) // 2
                    if y + h < avail.y() + 50 or y > avail.bottom() - 50:
                        y = avail.y() + (avail.height() - h) // 2
            except Exception:
                pass
            self.setGeometry(x, y, w, h)

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
    def tasks_panel(self) -> TasksPanel:
        return self._tasks_panel

    @property
    def today_panel(self) -> TodayPanel:
        return self._today_panel

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
