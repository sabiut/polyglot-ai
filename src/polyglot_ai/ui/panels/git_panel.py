"""Git source control panel — branch info, staged/unstaged files, commit."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from polyglot_ai.ui import theme_colors as tc

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class GitPanel(QWidget):
    """VS Code-style source control sidebar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._event_bus = None

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

        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedSize(24, 24)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {tc.get('text_tertiary')}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {tc.get('text_heading')}; }}"
        )
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

        self._commit_btn = QPushButton("Commit")
        self._commit_btn.setFixedHeight(28)
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
        commit_layout.addWidget(self._commit_btn)
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

    def set_project_root(self, path: Path) -> None:
        self._project_root = path
        self._refresh()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        event_bus.subscribe("file:saved", lambda **kw: self._refresh())
        event_bus.subscribe("file:created", lambda **kw: self._refresh())

    def _refresh(self) -> None:
        if self._project_root and not self._refreshing:
            self._refreshing = True
            # Run git commands in a thread to avoid qasync task conflicts.
            # Qt widgets are updated via QTimer.singleShot from the thread result.
            import threading

            threading.Thread(
                target=self._do_refresh_threaded,
                daemon=True,
            ).start()

    _refreshing = False

    def _do_refresh_threaded(self) -> None:
        """Run git commands in a background thread, then update UI on main thread."""
        import subprocess

        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()

            # Schedule UI update on the main thread
            QTimer.singleShot(0, lambda: self._apply_refresh(branch, status))
        except Exception as exc:  # noqa: F841
            QTimer.singleShot(0, lambda err=exc: self._apply_refresh_error(err))

    def _apply_refresh(self, branch: str, status_output: str) -> None:
        """Apply git refresh results to UI (must run on main thread)."""
        try:
            self._branch_label.setText(f"  ⎇ {branch or 'detached HEAD'}")
            self._staged_list.clear()
            self._unstaged_list.clear()

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
        except Exception as e:
            logger.debug("Git refresh UI update failed: %s", e)
        finally:
            self._refreshing = False

    def _apply_refresh_error(self, error: Exception) -> None:
        """Handle git refresh error on main thread."""
        self._branch_label.setText("  Not a git repository")
        logger.debug("Git refresh failed: %s", error)
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
            await self._run_git("commit", "-m", message)
            self._commit_input.clear()
            self._refresh()
            if self._event_bus:
                self._event_bus.emit("git:committed", message=message)
        except Exception as e:
            QMessageBox.warning(self, "Commit Failed", str(e))

    async def _run_git(self, *args: str) -> str:
        if not self._project_root:
            return ""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not output:
            err = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(err.strip() or f"git {args[0]} failed")
        # Refresh list after stage/unstage
        if args[0] in ("add", "restore"):
            QTimer.singleShot(200, self._refresh)
        return output
