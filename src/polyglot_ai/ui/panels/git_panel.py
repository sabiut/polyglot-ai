"""Git source control panel — branch info, staged/unstaged files, commit."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels.git_dialogs import (
    prompt_branch_name,
    show_message,
    validate_branch_name,
)
from polyglot_ai.ui.panels.git_icons import draw_branch_icon, draw_refresh_icon

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class GitPanel(QWidget):
    """VS Code-style source control sidebar."""

    # Signals used to marshal results from the background refresh thread
    # back onto the Qt main thread. QTimer.singleShot is broken when
    # called from non-Qt threads, so we use proper signals instead.
    _refresh_done = pyqtSignal(str, str)  # branch, status_output
    _refresh_error = pyqtSignal(object)  # exception

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._event_bus = None
        self._review_engine = None  # set via set_review_engine()
        # Set later in set_event_bus() once init_task_manager has run.
        self._task_manager = None
        # Instance attribute (not class attribute) so multiple GitPanel
        # instances don't share the same refresh-in-progress flag.
        self._refreshing = False
        # Cached "is the current project a git repo?" — recomputed on
        # every project switch in ``set_project_root``. Defaults to
        # False so the periodic timer is a no-op until a project is
        # actually open.
        self._is_git_repo = False

        # Cross-thread signal connections
        self._refresh_done.connect(self._apply_refresh)
        self._refresh_error.connect(self._apply_refresh_error)

        self._setup_ui()

        # Periodic refresh
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(10_000)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        title = QLabel("SOURCE CONTROL")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Header buttons — painted QPixmap icons with per-button style
        # (parent QPushButton rules don't apply to objectName'd widgets
        # that set their own stylesheet). Same pattern as mcp_sidebar.
        branch_btn = self._icon_button(draw_branch_icon(), "Create new branch")
        branch_btn.clicked.connect(self._on_new_branch)
        header_layout.addWidget(branch_btn)

        refresh_btn = self._icon_button(draw_refresh_icon(), "Refresh")
        refresh_btn.clicked.connect(self._refresh)
        header_layout.addWidget(refresh_btn)

        layout.addWidget(header)

        # Branch label
        self._branch_label = QLabel("  No project open")
        self._branch_label.setFixedHeight(28)
        self._branch_label.setStyleSheet(
            f"font-size: {tc.FONT_MD}px; color: {tc.get('text_primary')}; "
            f"background: {tc.get('bg_base')}; padding-left: {tc.SPACING_LG}px;"
        )
        layout.addWidget(self._branch_label)

        # Task branch hint — shown only when the active task points at a
        # different branch than the one currently checked out. Clicking
        # it runs `git checkout <task_branch>`.
        self._task_branch_hint: str | None = None
        self._task_hint_label = QLabel("")
        self._task_hint_label.setFixedHeight(20)
        self._task_hint_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._task_hint_label.setStyleSheet(
            "color: #e5a00d; font-size: 11px; background: transparent; padding-left: 12px;"
        )
        self._task_hint_label.mousePressEvent = lambda _e: self._on_task_hint_clicked()  # type: ignore
        self._task_hint_label.hide()
        layout.addWidget(self._task_hint_label)

        # Commit input
        commit_widget = QWidget()
        commit_widget.setStyleSheet(f"background: {tc.get('bg_base')};")
        commit_layout = QVBoxLayout(commit_widget)
        commit_layout.setContentsMargins(8, 6, 8, 6)
        commit_layout.setSpacing(4)

        # Commit type hint
        hint_label = QLabel("feat: | fix: | refactor: | docs: | test:")
        hint_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: transparent; padding: 0 2px;"
        )
        commit_layout.addWidget(hint_label)

        self._commit_input = QLineEdit()
        self._commit_input.setPlaceholderText("feat: add new feature")
        self._commit_input.setStyleSheet(f"""
            QLineEdit {{
                background: {tc.get("bg_input")}; color: {tc.get("text_heading")};
                border: 1px solid {tc.get("border_input")};
                border-radius: {tc.RADIUS_SM}px; padding: 6px 8px;
                font-size: {tc.FONT_MD}px;
            }}
            QLineEdit:focus {{ border: 1px solid {tc.get("border_focus")}; }}
        """)
        self._commit_input.returnPressed.connect(self._do_commit)
        commit_layout.addWidget(self._commit_input)

        # Commit + Push row, side by side. Commit is the primary action
        # (filled blue), push is the secondary (outlined).
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)

        self._commit_btn = QPushButton("✓  Commit")
        self._commit_btn.setFixedHeight(28)
        self._commit_btn.setToolTip("Commit the staged changes")
        self._commit_btn.setStyleSheet(f"""
            QPushButton {{
                background: {tc.get("accent_primary")}; color: {tc.get("text_on_accent")};
                border: none; border-radius: {tc.RADIUS_SM}px;
                font-size: {tc.FONT_MD}px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {tc.get("accent_primary_hover")}; }}
            QPushButton:disabled {{ background: {tc.get("bg_hover")}; color: {tc.get("text_disabled")}; }}
        """)
        self._commit_btn.clicked.connect(self._do_commit)
        actions_row.addWidget(self._commit_btn, stretch=1)

        self._push_btn = QPushButton("⇡  Push")
        self._push_btn.setFixedHeight(28)
        self._push_btn.setToolTip(
            "Push the current branch to origin. Sets the upstream tracking "
            "branch automatically the first time."
        )
        self._push_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {tc.get("text_primary")};
                border: 1px solid {tc.get("border_card")}; border-radius: {tc.RADIUS_SM}px;
                font-size: {tc.FONT_MD}px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {tc.get("bg_hover")}; }}
            QPushButton:disabled {{ color: {tc.get("text_disabled")}; }}
        """)
        self._push_btn.clicked.connect(self._on_push)
        actions_row.addWidget(self._push_btn, stretch=1)

        commit_layout.addLayout(actions_row)

        # AI PR description generator — runs the branch diff through the
        # review engine with a dedicated prompt and shows the result in
        # a dialog with copy + `gh pr create` actions.
        self._pr_btn = QPushButton("✨ Generate PR description")
        self._pr_btn.setFixedHeight(28)
        self._pr_btn.setToolTip(
            "Generate a PR title, summary, test plan and risks from the "
            "branch diff (vs main/master) using your configured AI model."
        )
        self._pr_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {tc.get("text_primary")};
                border: 1px solid {tc.get("border_card")}; border-radius: {tc.RADIUS_SM}px;
                font-size: {tc.FONT_SM}px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {tc.get("bg_hover")}; }}
            QPushButton:disabled {{ color: {tc.get("text_disabled")}; }}
        """)
        self._pr_btn.clicked.connect(self._on_generate_pr)
        commit_layout.addWidget(self._pr_btn)

        layout.addWidget(commit_widget)

        # Staged section
        staged_label = QLabel("  STAGED CHANGES")
        staged_label.setFixedHeight(24)
        staged_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            f"background: {tc.get('bg_surface')}; letter-spacing: 0.5px; padding-left: {tc.SPACING_MD}px;"
        )
        layout.addWidget(staged_label)

        self._staged_list = QListWidget()
        self._staged_list.setMaximumHeight(120)
        self._staged_list.setStyleSheet(self._list_style())
        self._staged_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._staged_list.customContextMenuRequested.connect(
            lambda pos: self._show_file_menu(pos, staged=True)
        )
        layout.addWidget(self._staged_list)

        # Unstaged section
        unstaged_label = QLabel("  CHANGES")
        unstaged_label.setFixedHeight(24)
        unstaged_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            f"background: {tc.get('bg_surface')}; letter-spacing: 0.5px; padding-left: {tc.SPACING_MD}px;"
        )
        layout.addWidget(unstaged_label)

        self._unstaged_list = QListWidget()
        self._unstaged_list.setStyleSheet(self._list_style())
        self._unstaged_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._unstaged_list.customContextMenuRequested.connect(
            lambda pos: self._show_file_menu(pos, staged=False)
        )
        layout.addWidget(self._unstaged_list)

        layout.addStretch()

    def _list_style(self) -> str:
        return f"""
            QListWidget {{
                background: {tc.get("bg_base")}; border: none; color: {tc.get("text_primary")};
                font-size: {tc.FONT_MD}px; outline: none;
            }}
            QListWidget::item {{ padding: 3px {tc.SPACING_MD}px; }}
            QListWidget::item:selected {{ background: {tc.get("bg_active")}; }}
            QListWidget::item:hover:!selected {{ background: {tc.get("bg_hover_subtle")}; }}
        """

    # ── Painted header icons ──

    def _icon_button(self, icon: QIcon, tooltip: str) -> QPushButton:
        """Return a flat transparent icon button that won't inherit
        the parent QPushButton blue fill."""
        btn = QPushButton()
        btn.setObjectName("gitHdrBtn")
        btn.setIcon(icon)
        btn.setFixedSize(24, 24)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#gitHdrBtn { background: transparent; border: none; }"
            "#gitHdrBtn:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        return btn

    def set_project_root(self, path: Path) -> None:
        self._project_root = path
        # Cache "is this a git repo?" once per project switch so the
        # 10-second poll can short-circuit cheaply when the user opens
        # a folder that isn't under version control. Without this the
        # panel shells out to ``git branch --show-current`` every tick,
        # gets ``fatal: not a git repository``, and spams the log.
        self._is_git_repo = self._detect_git_repo(path)
        if not self._is_git_repo:
            # One-shot UI update; no background work needed.
            self._branch_label.setText("  ⎇ (not a git repository)")
            self._branch_label.setToolTip(
                f"{path} is not a git repository — branch / status polling is paused."
            )
            self._staged_list.clear()
            self._unstaged_list.clear()
            logger.info("git_panel: %s is not a git repository, polling paused", path)
            return
        self._refresh()

    @staticmethod
    def _detect_git_repo(path: Path) -> bool:
        """Return True if ``path`` (or any parent) contains a ``.git`` entry.

        Walks up the parent chain so a project opened at a subdirectory
        of a repo still counts as a git project. ``.git`` is normally a
        directory but can also be a file (worktrees, submodules), so we
        check ``exists`` rather than ``is_dir``.
        """
        try:
            current = Path(path).resolve()
        except (OSError, RuntimeError):
            return False
        for candidate in (current, *current.parents):
            if (candidate / ".git").exists():
                return True
        return False

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        """When the panel becomes visible, opportunistically detect a
        project root if none has been set yet (covers cases where the
        project was opened via a code path that didn't fire any event
        the panel listens to)."""
        super().showEvent(event)
        if self._project_root is None:
            self._try_autodetect_project_root()

    def _try_autodetect_project_root(self) -> None:
        """Find a project root by checking, in order:

        1. The file explorer's current root (if any).
        2. The directory of the active editor tab.
        3. The directories of all open editor tabs.
        4. The current working directory.

        For each candidate, walk *up* the directory tree looking for a
        ``.git`` folder so files opened deep inside a repo still resolve.
        No-op if a project root has already been set.
        """
        logger.warning("git_panel: autodetect entry (project_root=%s)", self._project_root)
        if self._project_root is not None:
            return
        candidates: list[Path] = []
        seen: set[Path] = set()

        def add(p: Path | None) -> None:
            if p is None:
                return
            try:
                resolved = p.resolve()
            except OSError:
                return
            if resolved in seen or not resolved.exists():
                return
            seen.add(resolved)
            candidates.append(resolved)

        window = self.window()

        # 1. File explorer root
        fe = getattr(window, "_file_explorer", None)
        add(getattr(fe, "_project_root", None) if fe is not None else None)

        # 2 & 3. Active editor tab's directory + every open tab
        editor = getattr(window, "_editor_panel", None)
        if editor is not None:
            try:
                current_tab = editor.get_current_tab()
                if current_tab and getattr(current_tab, "file_path", None):
                    add(Path(current_tab.file_path).parent)
            except Exception:
                logger.debug("git_panel: could not read current editor tab", exc_info=True)
            try:
                for tab in getattr(editor, "_tabs", []):
                    fp = getattr(tab, "file_path", None)
                    if fp:
                        add(Path(fp).parent)
            except Exception:
                pass

        # 4. Process working directory
        try:
            add(Path.cwd())
        except OSError:
            pass

        for candidate in candidates:
            git_root = self._find_git_root(candidate)
            if git_root is not None:
                logger.warning("git_panel: auto-detected project root: %s", git_root)
                self.set_project_root(git_root)
                return
        logger.warning(
            "git_panel: autodetect found no .git in %d candidate(s): %s",
            len(candidates),
            [str(c) for c in candidates],
        )

    @staticmethod
    def _find_git_root(start: Path) -> Path | None:
        """Walk upward from ``start`` looking for a directory with .git."""
        try:
            current = start if start.is_dir() else start.parent
        except OSError:
            return None
        for path in [current, *current.parents]:
            if (path / ".git").exists():
                return path
        return None

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        event_bus.subscribe("file:saved", lambda **kw: self._refresh())
        event_bus.subscribe("file:created", lambda **kw: self._refresh())
        # Also listen for project_refreshed (fired by file_explorer when
        # its root is set through any path) so we catch projects opened
        # via drag-drop / command palette / future entry points that
        # don't go through the project_manager.
        event_bus.subscribe("project_refreshed", self._on_project_refreshed)
        event_bus.subscribe("project:opened", self._on_project_refreshed)
        # Track the active task so commits / pushes / PRs append to its
        # timeline and the panel can offer to switch branches when the
        # user activates a task that has a different branch checked out.
        from polyglot_ai.core.task_manager import EVT_TASK_CHANGED, get_task_manager

        self._task_manager = get_task_manager()

        def _on_task_changed(task=None, **_):
            self._on_active_task_changed(task)

        event_bus.subscribe(EVT_TASK_CHANGED, _on_task_changed)
        # Run autodetect once after wiring is complete (in case showEvent
        # already fired before any of these wires existed). 500 ms delay
        # so the editor panel has time to restore its tabs from session.
        QTimer.singleShot(500, self._try_autodetect_project_root)

    def _on_active_task_changed(self, task) -> None:
        """Surface a hint when the active task's branch differs from HEAD.

        We deliberately do NOT auto-checkout — that would silently nuke
        unstaged work. Instead the branch label gets a small clickable
        indicator the user can act on.
        """
        if task is None:
            self._task_branch_hint = None
            self._update_task_hint_label()
            return
        task_branch = getattr(task, "branch", None)
        if not task_branch:
            self._task_branch_hint = None
            self._update_task_hint_label()
            return
        # Compare against the current branch label text we already have.
        current = self._branch_label.text().strip().lstrip("⎇").strip()
        if current and current == task_branch:
            self._task_branch_hint = None
        else:
            self._task_branch_hint = task_branch
        self._update_task_hint_label()

    def _update_task_hint_label(self) -> None:
        """Show / hide the 'Switch to <task branch>?' hint under the branch label."""
        if not hasattr(self, "_task_hint_label"):
            return
        if self._task_branch_hint:
            self._task_hint_label.setText(
                f"  Active task uses '{self._task_branch_hint}' — click to switch"
            )
            self._task_hint_label.show()
        else:
            self._task_hint_label.hide()

    def _on_task_hint_clicked(self) -> None:
        """Run `git checkout <task_branch>` after the user clicks the hint."""
        if not self._task_branch_hint or self._project_root is None:
            return
        target = self._task_branch_hint

        from polyglot_ai.core.async_utils import safe_task

        async def _do_checkout() -> None:
            try:
                await self._run_git("checkout", target)
            except Exception as e:
                logger.exception("git_panel: checkout %s failed", target)
                show_message(self, "Checkout failed", str(e), kind="error")
                return
            self._task_branch_hint = None
            self._update_task_hint_label()
            self._refresh()

        safe_task(_do_checkout(), name="task_branch_checkout")

    def _on_project_refreshed(self, **kwargs) -> None:
        path = kwargs.get("path")
        if not path:
            return
        new_root = Path(path)
        if not new_root.is_dir():
            return
        if self._project_root == new_root:
            return
        logger.info("git_panel: adopting project root from event: %s", new_root)
        self.set_project_root(new_root)

    def set_review_engine(self, engine) -> None:
        """Inject the ReviewEngine so ✨ Generate PR description works."""
        self._review_engine = engine

    def _current_model_id(self) -> str:
        """Best-effort lookup of the user's currently selected model."""
        window = self.window()
        if hasattr(window, "chat_panel"):
            try:
                model_id, _ = window.chat_panel._get_selected_model()
                return model_id or ""
            except Exception:
                logger.warning(
                    "git_panel: could not read current model from chat panel",
                    exc_info=True,
                )
        return ""

    def _on_generate_pr(self) -> None:
        """Kick off AI PR summary generation on a background task."""
        if self._project_root is None:
            show_message(
                self,
                "No project",
                "Open a project first before generating a PR description.",
                kind="info",
            )
            return
        if self._review_engine is None:
            show_message(
                self,
                "PR generator unavailable",
                "Review engine is not wired. This is a setup bug — please report it.",
                kind="warn",
            )
            return

        self._pr_btn.setEnabled(False)
        self._pr_btn.setText("✨ Generating PR description…")

        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_pr_generation(), name="generate_pr_summary")

    async def _run_pr_generation(self) -> None:
        """Worker coroutine: pull the branch diff and call the review engine."""
        from polyglot_ai.core.review.models import PRSummary
        from polyglot_ai.core.review.review_engine import get_git_diff
        from polyglot_ai.ui.dialogs.pr_summary_dialog import PRSummaryDialog

        project_root = self._project_root
        assert project_root is not None  # guarded by caller

        try:
            diff = await get_git_diff(str(project_root), mode="branch")
        except Exception as e:
            logger.exception("git_panel: failed to get branch diff")
            self._reset_pr_button()
            show_message(
                self,
                "Could not read git diff",
                f"Failed to run `git diff` against main/master:\n\n{e}",
                kind="error",
            )
            return

        if not diff.strip():
            self._reset_pr_button()
            show_message(
                self,
                "Nothing to summarise",
                "No changes found between this branch and main/master. "
                "Commit some changes first, then try again.",
                kind="info",
            )
            return

        try:
            result: PRSummary = await self._review_engine.generate_pr_summary(
                diff, model_id=self._current_model_id()
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("git_panel: PR summary generation crashed")
            self._reset_pr_button()
            show_message(
                self,
                "PR generation failed",
                f"The AI request crashed:\n\n{e}",
                kind="error",
            )
            return

        self._reset_pr_button()
        dlg = PRSummaryDialog(result, project_root=project_root, parent=self)
        dlg.pr_created.connect(self._on_pr_created)
        dlg.exec()

    def _on_pr_created(self, url: str, title: str, body: str) -> None:
        """Transition the active task to REVIEW and stash the PR URL.

        Triggered by ``PRSummaryDialog.pr_created`` after a successful
        ``gh pr create`` invocation.
        """
        if self._task_manager is None or self._task_manager.active is None:
            return
        try:
            # Try to extract the PR number from the URL.
            pr_number: int | None = None
            try:
                pr_number = int(url.rstrip("/").rsplit("/", 1)[-1])
            except (ValueError, IndexError):
                pass
            self._task_manager.update_active(pr_url=url, pr_number=pr_number)
            self._task_manager.add_note(
                "pr_opened",
                f"Opened PR: {title or url}",
                data={"url": url, "pr_number": pr_number},
            )
            from polyglot_ai.core.tasks import TaskState

            self._task_manager.update_state(TaskState.REVIEW)
            _ = body  # currently unused, kept for future enrichments
        except Exception:
            logger.exception("git_panel: failed to record PR on task")

    def _reset_pr_button(self) -> None:
        self._pr_btn.setEnabled(True)
        self._pr_btn.setText("✨ Generate PR description")

    def _on_push(self) -> None:
        """Push the current branch to origin on a background task.

        Uses ``-u origin <branch>`` so first-time push automatically
        sets the upstream tracking branch. Subsequent pushes are
        equivalent to plain ``git push``.
        """
        if self._project_root is None:
            show_message(self, "No project", "Open a project first.", kind="info")
            return
        self._push_btn.setEnabled(False)
        self._push_btn.setText("⇡ Pushing…")
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_push(), name="git_push")

    async def _run_push(self) -> None:
        try:
            branch = (await self._run_git("branch", "--show-current")).strip()
            if not branch:
                self._reset_push_button()
                show_message(
                    self,
                    "Detached HEAD",
                    "You are not on a branch. Check out a branch before pushing.",
                    kind="warn",
                )
                return
            await self._run_git("push", "-u", "origin", branch)
        except Exception as e:
            logger.exception("git_panel: push failed")
            self._reset_push_button()
            show_message(self, "Push failed", str(e), kind="error")
            return
        self._reset_push_button()
        # Record on the active task so the timeline shows when the
        # branch went up to origin.
        if self._task_manager is not None and self._task_manager.active is not None:
            try:
                self._task_manager.add_note(
                    "pushed",
                    f"Pushed '{branch}' to origin",
                    data={"branch": branch},
                )
                # Bind the branch to the task if it wasn't already.
                if not self._task_manager.active.branch:
                    self._task_manager.update_active(branch=branch)
            except Exception:
                logger.exception("git_panel: could not record push on task")
        show_message(
            self,
            "Push succeeded",
            f"Pushed branch '{branch}' to origin.",
            kind="info",
        )

    def _reset_push_button(self) -> None:
        self._push_btn.setEnabled(True)
        self._push_btn.setText("⇡ Push branch")

    def _on_new_branch(self) -> None:
        """Prompt for a branch name and run `git checkout -b <name>`."""
        if self._project_root is None:
            show_message(
                self,
                "No project",
                "Open a project before creating a branch.",
                kind="info",
            )
            return
        name = prompt_branch_name(self)
        if not name:
            return

        # Validate the name client-side so the user gets a friendly
        # error instead of git's cryptic "fatal: '<name>' is not a
        # valid branch name". Mirrors the rules from `git check-ref-format`.
        invalid_reason = validate_branch_name(name)
        if invalid_reason:
            show_message(
                self,
                "Invalid branch name",
                f"'{name}' is not a valid git branch name.\n\n{invalid_reason}",
                kind="error",
            )
            return

        # Run `git checkout -b` off the GUI thread via the same async
        # _run_git helper the rest of the panel uses, so a slow hook
        # script doesn't freeze the UI.
        from polyglot_ai.core.async_utils import safe_task

        async def do_create() -> None:
            try:
                # Capture the previous branch BEFORE checking out so we
                # can use it as the task's base_branch.
                base = ""
                try:
                    base = (await self._run_git("branch", "--show-current")).strip()
                except Exception:
                    logger.debug("git_panel: could not capture base branch", exc_info=True)
                await self._run_git("checkout", "-b", name)
            except Exception as e:
                logger.exception("git_panel: branch creation failed")
                show_message(self, "Branch creation failed", str(e), kind="error")
                return
            logger.info("git_panel: created branch %s", name)
            self._refresh()
            # Bind the new branch to the active task if it doesn't have
            # one yet, and record a timeline event.
            if self._task_manager is not None and self._task_manager.active is not None:
                try:
                    if not self._task_manager.active.branch:
                        self._task_manager.update_active(branch=name, base_branch=base or None)
                    self._task_manager.add_note(
                        "branch_created",
                        f"Created branch '{name}'",
                        data={"branch": name, "base": base},
                    )
                except Exception:
                    logger.exception("git_panel: could not record branch on task")

        safe_task(do_create(), name="git_checkout_b")

    def _refresh(self) -> None:
        if not self._project_root or self._refreshing:
            return
        # Skip the shell-out entirely when the open project isn't a
        # git repo. ``_is_git_repo`` is recomputed on every project
        # switch via ``set_project_root``, so this stays correct as
        # the user moves between repos and non-repo folders.
        if not self._is_git_repo:
            return
        self._refreshing = True
        # Run git commands in a thread to avoid qasync task conflicts.
        # Qt widgets are updated via QTimer.singleShot from the thread result.
        import threading

        threading.Thread(
            target=self._do_refresh_threaded,
            daemon=True,
        ).start()

    def _do_refresh_threaded(self) -> None:
        """Run git commands in a background thread, then signal the main thread.

        We MUST use Qt signals here, not QTimer.singleShot — singleShot
        from a non-Qt thread silently fails to dispatch to the GUI thread,
        which left the panel stuck on "No project open" indefinitely.
        """
        import subprocess

        # Snapshot project_root once so we don't see a half-switched
        # state if the user opens a different project mid-refresh.
        project_root = self._project_root
        if project_root is None:
            return

        try:
            branch_proc = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if branch_proc.returncode != 0:
                err = (branch_proc.stderr or branch_proc.stdout or "").strip()
                raise RuntimeError(
                    f"git branch --show-current failed (rc={branch_proc.returncode}): {err}"
                )
            branch = branch_proc.stdout.strip()

            # IMPORTANT: do NOT call .strip() here. git status --porcelain
            # uses the FIRST column for index status; an unstaged-modified
            # file appears as ' M README.md' (leading space). .strip()
            # would chop that leading space, shift every line left by one
            # column, and (a) classify the file as staged instead of
            # unstaged and (b) eat the first letter of every such filename.
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if status_proc.returncode != 0:
                err = (status_proc.stderr or status_proc.stdout or "").strip()
                raise RuntimeError(f"git status failed (rc={status_proc.returncode}): {err}")
            status = status_proc.stdout.rstrip("\n")

            # Cross-thread signal — Qt auto-marshals to the GUI thread.
            self._refresh_done.emit(branch, status)
        except FileNotFoundError as exc:
            logger.warning("git_panel: git binary not found: %s", exc)
            self._refresh_error.emit(RuntimeError("git is not installed or not on PATH"))
        except subprocess.TimeoutExpired:
            logger.warning("git_panel: git status timed out")
            self._refresh_error.emit(RuntimeError("git status timed out after 5s"))
        except Exception as exc:
            # Demote the expected "not a git repository" race to info;
            # the main-thread handler ``_apply_refresh_error`` will
            # also flip ``_is_git_repo`` so subsequent ticks short-
            # circuit. Genuine failures still log at WARNING.
            if "not a git" in str(exc).lower():
                logger.info("git_panel: refresh thread: %s", exc)
            else:
                logger.warning("git_panel: refresh thread failed: %s", exc)
            self._refresh_error.emit(exc)

    def _apply_refresh(self, branch: str, status_output: str) -> None:
        """Apply git refresh results to UI (must run on main thread)."""
        try:
            self._branch_label.setText(f"  ⎇ {branch or 'detached HEAD'}")
            self._staged_list.clear()
            self._unstaged_list.clear()
            # Recompute the task branch hint with the freshly-known current branch.
            if self._task_manager is not None and self._task_manager.active is not None:
                task_branch = self._task_manager.active.branch
                if task_branch and branch and task_branch != branch:
                    self._task_branch_hint = task_branch
                else:
                    self._task_branch_hint = None
                self._update_task_hint_label()

            for line in status_output.split("\n"):
                if not line or len(line) < 3:
                    continue
                index_status = line[0]
                work_status = line[1]
                filepath = line[3:]

                if index_status in ("A", "M", "D", "R"):
                    color = {
                        "A": tc.get("git_added"),
                        "M": tc.get("git_modified"),
                        "D": tc.get("git_deleted"),
                        "R": tc.get("git_added"),
                    }
                    item = QListWidgetItem(f"  {index_status}  {filepath}")
                    item.setForeground(QColor(color.get(index_status, tc.get("text_primary"))))
                    item.setData(Qt.ItemDataRole.UserRole, filepath)
                    self._staged_list.addItem(item)

                if work_status in ("M", "D", "?"):
                    color = {
                        "M": tc.get("git_modified"),
                        "D": tc.get("git_deleted"),
                        "?": tc.get("git_untracked"),
                    }
                    label = "U" if work_status == "?" else work_status
                    item = QListWidgetItem(f"  {label}  {filepath}")
                    item.setForeground(QColor(color.get(work_status, tc.get("text_primary"))))
                    item.setData(Qt.ItemDataRole.UserRole, filepath)
                    self._unstaged_list.addItem(item)
        except Exception:
            logger.exception("git_panel: failed to populate refresh UI")
        finally:
            self._refreshing = False

    def _apply_refresh_error(self, error: Exception) -> None:
        """Handle git refresh error on main thread.

        Surfaces the actual reason as the branch label tooltip so the
        user can see it on hover, instead of the catch-all "Not a git
        repository" that hid every failure mode.
        """
        msg = str(error) or "Git refresh failed"
        # Extract a short user-friendly tag for the label.
        not_a_repo = "not a git" in msg.lower()
        if "not installed" in msg.lower():
            short = "git not installed"
        elif "timed out" in msg.lower():
            short = "git timed out"
        elif not_a_repo:
            short = "Not a git repository"
            # Cache the result so the timer stops re-checking. This
            # covers the rare race where the project root WAS a repo
            # at ``set_project_root`` time but ``.git`` was removed
            # between then and now.
            self._is_git_repo = False
        else:
            short = "git error"
        self._branch_label.setText(f"  ⚠ {short}")
        self._branch_label.setToolTip(msg)
        # "Not a git repo" is an expected steady state for non-repo
        # projects, not an error worth a WARNING per tick. Only log
        # genuine failures (git missing, timeouts, unknown) loudly.
        if not_a_repo:
            logger.info("git_panel: %s", msg)
        else:
            logger.warning("git_panel: refresh error: %s", msg)
        self._refreshing = False

    def _show_file_menu(self, pos, staged: bool) -> None:
        lst = self._staged_list if staged else self._unstaged_list
        item = lst.itemAt(pos)
        if not item:
            return
        filepath = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface_overlay')}; border: 1px solid {tc.get('border_menu')}; "
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        from polyglot_ai.core.async_utils import safe_task

        if staged:
            unstage = menu.addAction("Unstage")
            unstage.triggered.connect(
                lambda: safe_task(
                    self._run_git("restore", "--staged", filepath), name="git_unstage"
                )
            )
        else:
            stage = menu.addAction("Stage")
            stage.triggered.connect(
                lambda: safe_task(self._run_git("add", filepath), name="git_stage")
            )

        menu.exec(lst.viewport().mapToGlobal(pos))

    def _do_commit(self) -> None:
        msg = self._commit_input.text().strip()
        if not msg:
            return
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_commit(msg), name="git_commit")

    async def _run_commit(self, message: str) -> None:
        try:
            # Snapshot the staged file list BEFORE committing so we can
            # roll the names into the task timeline note. After commit
            # the index is empty so this would return nothing.
            staged_files: list[str] = []
            try:
                staged_raw = await self._run_git("diff", "--cached", "--name-only")
                staged_files = [f for f in staged_raw.splitlines() if f.strip()]
            except Exception:
                logger.debug("git_panel: could not snapshot staged files", exc_info=True)

            await self._run_git("commit", "-m", message)
            self._commit_input.clear()
            self._refresh()
            if self._event_bus:
                self._event_bus.emit("git:committed", message=message)

            # Record the commit on the active task (if any) so the
            # task timeline reflects the work that just shipped.
            self._record_commit_on_task(message, staged_files)
        except Exception as e:
            # Always log the full traceback so post-mortem on a
            # cryptic commit failure (pre-commit hook crash, IO error,
            # signing failure) is possible from the logs.
            logger.exception("git_panel: commit failed")
            show_message(self, "Commit failed", str(e), kind="error")

    def _record_commit_on_task(self, message: str, files: list[str]) -> None:
        """Append a 'committed' note to the active task and merge files
        into ``task.modified_files`` so the system prompt knows what's
        been touched."""
        if self._task_manager is None:
            return
        active = self._task_manager.active
        if active is None:
            return
        try:
            self._task_manager.add_note(
                "committed",
                message.splitlines()[0][:120] if message else "(no message)",
                data={"files": files, "message": message},
            )
            if files:
                merged = list(active.modified_files)
                for f in files:
                    if f not in merged:
                        merged.append(f)
                self._task_manager.update_active(modified_files=merged)
        except Exception:
            logger.exception("git_panel: could not record commit on task")

    async def _run_git(self, *args: str) -> str:
        """Run a git command and return its stdout.

        Raises ``RuntimeError`` on ANY non-zero exit code (not just
        when stdout is empty — many git commands write partial stdout
        before failing, so the old "and not output" guard silently
        reported failed commits/pushes/stages as successful).

        Always kills the subprocess on timeout to avoid zombie
        processes holding the repo lock.
        """
        if not self._project_root:
            return ""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("git_panel: git %s timed out, killing process", args[0])
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            raise RuntimeError(f"git {args[0]} timed out after 30s") from None

        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            # Include both stderr and any stdout — git commit failures
            # in particular often put hook output in stdout, not stderr.
            detail = err.strip() or output.strip() or f"rc={proc.returncode}"
            raise RuntimeError(f"git {args[0]} failed: {detail}")
        # Refresh list after stage/unstage
        if args[0] in ("add", "restore"):
            QTimer.singleShot(200, self._refresh)
        return output
