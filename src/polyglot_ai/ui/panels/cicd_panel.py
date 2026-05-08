"""CI/CD Pipeline Inspector — view GitHub Actions workflow runs and logs."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.tasks import CIRunSnapshot, TaskKind
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

# Status icons and colors
_STATUS_MAP = {
    "success": ("✓", "#4ec9b0"),
    "completed": ("✓", "#4ec9b0"),
    "failure": ("✗", "#f44747"),
    "cancelled": ("⊘", "#6a6a6a"),
    "skipped": ("⊘", "#6a6a6a"),
    "in_progress": ("⏳", "#cca700"),
    "queued": ("⏳", "#cca700"),
    "waiting": ("⏳", "#cca700"),
    "requested": ("⏳", "#cca700"),
    "pending": ("⏳", "#cca700"),
}


class CICDPanel(QWidget):
    """CI/CD Pipeline Inspector — shows GitHub Actions workflow runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._runs_data: list[dict] = []
        self._gh_available: bool | None = None
        # Task wiring — populated by set_event_bus(). When the active
        # task has a branch and the filter toggle is on, the runs list
        # is filtered to that branch only.
        self._task_manager = None
        self._filter_to_task = True
        self._active_task_branch: str | None = None
        # Cached list of currently-visible runs (post-filter). Used by
        # row-based handlers so selection still works when the filter
        # is on and table row N is no longer ``_runs_data[N]``.
        self._visible_runs_cache: list[dict] = []

        self._setup_ui()

        # Auto-refresh every 30 seconds to catch live status changes
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(30_000)

    def set_event_bus(self, event_bus) -> None:
        """Subscribe to task lifecycle events.

        Tracks the active task's branch so the runs table can be
        scoped to that branch and the latest CI status can be
        recorded back on the task.
        """
        from polyglot_ai.core.task_manager import EVT_TASK_CHANGED, get_task_manager

        self._task_manager = get_task_manager()

        def _on_task_changed(task=None, **_):
            self._active_task_branch = getattr(task, "branch", None) if task is not None else None
            # Re-render whatever's already loaded so the filter applies
            # immediately, then schedule a fresh fetch.
            self._render_runs()
            if self._project_root and self.isVisible():
                QTimer.singleShot(50, self._refresh_runs)

        event_bus.subscribe(EVT_TASK_CHANGED, _on_task_changed)

    def showEvent(self, event) -> None:
        """Refresh when tab becomes visible."""
        super().showEvent(event)
        if self._project_root:
            QTimer.singleShot(100, self._refresh_runs)

    def set_project_root(self, path: Path | str) -> None:
        self._project_root = Path(path) if isinstance(path, str) else path
        # When the project changes, clear the runs table and re-evaluate
        # so a freshly-opened non-git folder doesn't keep showing the
        # previous project's pipeline runs.
        if hasattr(self, "_runs_table"):
            self._runs_table.setRowCount(0)
            self._runs_data = []
        if self.isVisible():
            self._refresh_runs()

    def _auto_refresh(self) -> None:
        """Silent auto-refresh — only if we have a project and the tab is visible."""
        if self._project_root and self.isVisible():
            self._refresh_runs()

    # ── Repo capability checks ──────────────────────────────────────
    #
    # The CI/CD panel only makes sense for git-tracked projects with a
    # GitHub remote. Without these checks, opening a brand-new Arduino
    # sketch (or any non-git folder) used to surface ``gh``'s raw
    # ``fatal: not a git repository`` error in the panel — not useful
    # to anyone. We probe up front and render a friendly empty state
    # instead.

    def _is_git_repo(self) -> bool:
        """Return True when the project root is inside a git working tree."""
        if self._project_root is None:
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _has_github_remote(self) -> bool:
        """Return True when at least one configured remote points at GitHub.

        We accept both ``github.com`` (public) and ``ghe.<corp>.com``
        style hosts so users on GitHub Enterprise see CI runs too.
        """
        if self._project_root is None:
            return False
        try:
            result = subprocess.run(
                ["git", "remote", "-v"],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        text = result.stdout.lower()
        return "github.com" in text or "github.io" in text or "ghe." in text

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("cicdHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#cicdHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("CI/CD PIPELINES")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_tertiary')}; letter-spacing: 0.5px; "
            "background: transparent;"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()

        self._refresh_btn = QPushButton("⟳ Refresh")
        self._refresh_btn.setObjectName("cicdRefresh")
        self._refresh_btn.setFixedHeight(24)
        self._refresh_btn.setStyleSheet(
            f"#cicdRefresh {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 3px; "
            f"padding: 0 12px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#cicdRefresh:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        self._refresh_btn.clicked.connect(self._refresh_runs)
        h_layout.addWidget(self._refresh_btn)

        layout.addWidget(header)

        # Main splitter: runs table + details
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )

        # Runs table
        self._runs_table = QTableWidget()
        self._runs_table.setColumnCount(5)
        self._runs_table.setHorizontalHeaderLabels(
            ["Status", "Workflow", "Branch", "Time", "Conclusion"]
        )
        self._runs_table.setStyleSheet(
            f"QTableWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; "
            f"gridline-color: {tc.get('border_secondary')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 4px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
            f"QTableWidget::item {{ padding: 4px; }}"
            f"QTableWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
        )
        self._runs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._runs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._runs_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._runs_table.customContextMenuRequested.connect(self._on_runs_context_menu)
        self._runs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._runs_table.setColumnWidth(0, 50)
        self._runs_table.setColumnWidth(2, 120)
        self._runs_table.setColumnWidth(3, 140)
        self._runs_table.setColumnWidth(4, 90)
        self._runs_table.currentCellChanged.connect(self._on_run_selected)
        splitter.addWidget(self._runs_table)

        # Details area
        details_widget = QWidget()
        d_layout = QVBoxLayout(details_widget)
        d_layout.setContentsMargins(0, 0, 0, 0)
        d_layout.setSpacing(0)

        # Jobs header
        jobs_header = QWidget()
        jobs_header.setObjectName("cicdJobsHeader")
        jobs_header.setFixedHeight(28)
        jobs_header.setStyleSheet(
            f"#cicdJobsHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        jh_layout = QHBoxLayout(jobs_header)
        jh_layout.setContentsMargins(8, 0, 8, 0)

        self._jobs_label = QLabel("Select a run to view details")
        self._jobs_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        jh_layout.addWidget(self._jobs_label)
        jh_layout.addStretch()

        self._logs_btn = QPushButton("View Logs")
        self._logs_btn.setObjectName("cicdLogsBtn")
        self._logs_btn.setFixedHeight(20)
        self._logs_btn.setVisible(False)
        self._logs_btn.setStyleSheet(
            f"#cicdLogsBtn {{ background: {tc.get('accent_error')}; "
            f"color: #ffffff; border: none; border-radius: 3px; padding: 0 8px; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; }}"
            f"#cicdLogsBtn:hover {{ background: #d43f3f; }}"
        )
        self._logs_btn.clicked.connect(self._fetch_failed_logs)
        jh_layout.addWidget(self._logs_btn)

        d_layout.addWidget(jobs_header)

        # Jobs table
        self._jobs_table = QTableWidget()
        self._jobs_table.setColumnCount(3)
        self._jobs_table.setHorizontalHeaderLabels(["Status", "Job", "Duration"])
        self._jobs_table.setStyleSheet(self._runs_table.styleSheet())
        self._jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._jobs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._jobs_table.setColumnWidth(0, 50)
        self._jobs_table.setColumnWidth(2, 100)
        d_layout.addWidget(self._jobs_table)

        # Log viewer
        self._log_viewer = QPlainTextEdit()
        self._log_viewer.setReadOnly(True)
        self._log_viewer.setVisible(False)
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log_viewer.setFont(mono)
        self._log_viewer.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; "
            f"border-top: 1px solid {tc.get('border_secondary')}; padding: 6px; }}"
        )
        self._log_viewer.setMaximumHeight(200)
        d_layout.addWidget(self._log_viewer)

        splitter.addWidget(details_widget)
        splitter.setSizes([250, 250])

        layout.addWidget(splitter)

        # Status bar
        self._status_label = QLabel("  Click Refresh to load pipeline runs")
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px;"
        )
        layout.addWidget(self._status_label)

    # ── Data Fetching (threaded) ────────────────────────────────────

    def _check_gh(self) -> bool:
        if self._gh_available is None:
            self._gh_available = shutil.which("gh") is not None
        return self._gh_available

    def _run_gh(self, args: list[str], timeout: int = 30) -> tuple[str, int]:
        """Run a gh CLI command and return (output, returncode)."""
        if not self._check_gh():
            return "Error: GitHub CLI (gh) not found. Install from https://cli.github.com", 1
        try:
            result = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._project_root or None,
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1

    def _refresh_runs(self) -> None:
        if not self._project_root:
            self._set_empty_state("  Open a project first")
            return

        # Friendly empty states *before* we shell out — these guard
        # against the "fatal: not a git repository" stderr that
        # otherwise leaked into the status bar when a non-git
        # project was open.
        if not self._is_git_repo():
            self._set_empty_state(
                "  This project isn't a git repository — CI/CD only works for git-tracked projects."
            )
            return
        if not self._has_github_remote():
            self._set_empty_state(
                "  No GitHub remote configured for this project. Add "
                "one with: git remote add origin "
                "git@github.com:<you>/<repo>.git"
            )
            return

        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Loading...")

        output, code = self._run_gh(
            [
                "run",
                "list",
                "--json",
                "status,conclusion,name,headBranch,createdAt,databaseId,event",
                "--limit",
                "25",
            ]
        )
        self._on_runs_loaded(output, code)

    def _set_empty_state(self, message: str) -> None:
        """Clear the runs table and show ``message`` as the status.

        Centralises the "we have nothing to show, here's why" path so
        every guard branch reaches the same end state — table empty,
        refresh button re-enabled, status text explanatory.
        """
        if hasattr(self, "_runs_table"):
            self._runs_table.setRowCount(0)
        self._runs_data = []
        if hasattr(self, "_refresh_btn"):
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("⟳ Refresh")
        self._status_label.setText(message)

    def _on_runs_loaded(self, output: str, code: int) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("⟳ Refresh")

        if code != 0:
            self._status_label.setText(f"  Error: {output[:80]}")
            return

        try:
            self._runs_data = json.loads(output)
        except json.JSONDecodeError:
            self._status_label.setText("  Error: Failed to parse gh output")
            return

        self._render_runs()
        # Now that we have fresh data, push the latest CI status for the
        # active task's branch onto the task itself so other panels (the
        # Tasks sidebar, the Today page) reflect the new state.
        self._record_ci_on_task()

    def _visible_runs(self) -> list[dict]:
        """Return the subset of runs that should be displayed.

        When ``_filter_to_task`` is on AND there's an active task branch,
        only runs whose ``headBranch`` matches the task's branch are
        shown. Otherwise the full list is returned.
        """
        if self._filter_to_task and self._active_task_branch:
            return [r for r in self._runs_data if r.get("headBranch") == self._active_task_branch]
        return self._runs_data

    def _render_runs(self) -> None:
        """Re-render the runs table from ``_runs_data`` (no fetch)."""
        rows = self._visible_runs()
        # Cache so row-based handlers stay correct even when filtered.
        self._visible_runs_cache = rows
        self._runs_table.setRowCount(len(rows))
        for row_idx, run in enumerate(rows):
            status = run.get("conclusion") or run.get("status", "unknown")
            icon, color = _STATUS_MAP.get(status, ("?", tc.get("text_muted")))

            # Status icon
            status_item = QTableWidgetItem(icon)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(self._make_color(color))
            self._runs_table.setItem(row_idx, 0, status_item)

            # Workflow name
            self._runs_table.setItem(row_idx, 1, QTableWidgetItem(run.get("name", "")))

            # Branch
            self._runs_table.setItem(row_idx, 2, QTableWidgetItem(run.get("headBranch", "")))

            # Time
            created = run.get("createdAt", "")
            display_time = self._format_time(created)
            self._runs_table.setItem(row_idx, 3, QTableWidgetItem(display_time))

            # Conclusion
            conclusion = run.get("conclusion") or run.get("status", "")
            conc_item = QTableWidgetItem(conclusion)
            conc_item.setForeground(self._make_color(color))
            self._runs_table.setItem(row_idx, 4, conc_item)

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        scope = ""
        if self._filter_to_task and self._active_task_branch:
            scope = f" (filtered to '{self._active_task_branch}')"
        self._status_label.setText(f"  Last refreshed: {now} | {len(rows)} runs{scope}")

    def _record_ci_on_task(self) -> None:
        """Update the active task's last_ci_run with the most recent run
        for that task's branch."""
        if (
            self._task_manager is None
            or self._task_manager.active is None
            or not self._active_task_branch
        ):
            return
        # Find the newest run on that branch.
        branch_runs = [
            r for r in self._runs_data if r.get("headBranch") == self._active_task_branch
        ]
        if not branch_runs:
            return
        newest = branch_runs[0]  # gh run list returns newest first
        try:
            import time as _time

            status = newest.get("conclusion") or newest.get("status", "")
            snapshot = CIRunSnapshot(
                status=status,
                workflow=newest.get("name", ""),
                url="",  # gh run list doesn't include htmlUrl in our query
                timestamp=_time.time(),
            )
            existing = self._task_manager.active.last_ci_run
            self._task_manager.update_active(last_ci_run=snapshot)
            # Only add a timeline note when the status actually changes,
            # otherwise the auto-refresh would spam the timeline every 30s.
            if existing is None or existing.status != status:
                self._task_manager.add_note(
                    "ci_run",
                    f"CI {status}: {newest.get('name', '')}",
                    data={
                        "status": status,
                        "workflow": newest.get("name", ""),
                        "branch": self._active_task_branch,
                    },
                )
        except Exception:
            logger.exception("cicd_panel: could not record CI status on task")

    def _on_runs_context_menu(self, pos) -> None:
        """Show a right-click menu on the runs table.

        For failing/cancelled runs the menu offers a "Debug this
        failure" action that creates an Incident task pre-populated
        with the run context, then activates it.
        """
        from PyQt6.QtWidgets import QMenu

        item = self._runs_table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        rows = self._visible_runs_cache or self._runs_data
        if row < 0 or row >= len(rows):
            return
        run = rows[row]

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #252526; color: #ddd; border: 1px solid #444; }"
            "QMenu::item { padding: 5px 18px; }"
            "QMenu::item:selected { background: #094771; }"
        )
        view_action = menu.addAction("View jobs / logs")
        debug_action = None
        conclusion = run.get("conclusion") or run.get("status", "")
        if conclusion in ("failure", "cancelled", "timed_out", "startup_failure"):
            debug_action = menu.addAction("✨ Debug this failure as a new task")

        chosen = menu.exec(self._runs_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == view_action:
            self._runs_table.selectRow(row)
        elif debug_action is not None and chosen == debug_action:
            self._create_incident_from_run(run)

    def _create_incident_from_run(self, run: dict) -> None:
        """Create an Incident task pre-populated with the failing run's context."""
        if self._task_manager is None or self._task_manager.project_root is None:
            return

        workflow = run.get("name", "(unnamed workflow)")
        branch = run.get("headBranch", "")
        run_id = run.get("databaseId")
        conclusion = run.get("conclusion") or run.get("status", "")
        title = f"Debug CI failure: {workflow}"[:120]
        description_parts = [
            f"Workflow: **{workflow}**",
            f"Branch: `{branch}`",
            f"Status: `{conclusion}`",
        ]
        if run_id:
            description_parts.append(f"Run id: `{run_id}`")
        description_parts.append("")
        description_parts.append(
            "Investigate the failing job(s), reproduce locally, and propose a fix."
        )
        description = "\n".join(description_parts)
        try:
            new_task = self._task_manager.create_task(TaskKind.INCIDENT, title, description)
            if new_task is not None and branch:
                # Bind the failing branch so the panel filter will pick
                # this run up under the new task.
                self._task_manager.update_active(branch=branch)
                self._task_manager.add_note(
                    "ci_failure_imported",
                    f"Imported from CI run #{run_id} ({conclusion})",
                    data={"run_id": run_id, "workflow": workflow, "branch": branch},
                )
        except Exception:
            logger.exception("cicd_panel: could not create incident task")

    def _on_run_selected(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        # Use the visible-runs cache so selection still maps correctly
        # when a task filter is active and table row N is no longer
        # the same as ``_runs_data[N]``.
        rows = self._visible_runs_cache or self._runs_data
        if row < 0 or row >= len(rows):
            return

        run = rows[row]
        run_id = run.get("databaseId")
        if not run_id:
            return

        conclusion = run.get("conclusion") or run.get("status", "")
        self._jobs_label.setText(f"Loading jobs for run #{run_id}...")
        # Show button for any completed run so users can inspect logs
        self._logs_btn.setVisible(conclusion in ("failure", "cancelled", "success", "completed"))
        self._log_viewer.setVisible(False)

        # Store selected run ID for log fetching
        self._selected_run_id = run_id

        output, code = self._run_gh(["run", "view", str(run_id), "--json", "jobs"])
        self._on_jobs_loaded(output, code)

    def _on_jobs_loaded(self, output: str, code: int) -> None:
        if code != 0:
            self._jobs_label.setText(f"Error: {output[:60]}")
            return

        try:
            data = json.loads(output)
            jobs = data.get("jobs", [])
        except json.JSONDecodeError:
            self._jobs_label.setText("Error: Failed to parse job data")
            return

        # Cache job metadata keyed by run id so the log fetcher can
        # use the per-job ``gh run view --job <id> --log`` endpoint
        # instead of the run-level ``--log-failed`` flag (which
        # downloads the *entire* run as a ZIP, even when only one
        # job failed — typically 30–60 s of network I/O for a CI
        # with two parallel jobs). Per-job log endpoints stream
        # plain text and finish in a few seconds.
        if not hasattr(self, "_cached_jobs"):
            self._cached_jobs: dict[int, list[dict]] = {}
        if hasattr(self, "_selected_run_id"):
            self._cached_jobs[self._selected_run_id] = jobs

        self._jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            status = job.get("conclusion") or job.get("status", "unknown")
            icon, color = _STATUS_MAP.get(status, ("?", tc.get("text_muted")))

            status_item = QTableWidgetItem(icon)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(self._make_color(color))
            self._jobs_table.setItem(row, 0, status_item)

            self._jobs_table.setItem(row, 1, QTableWidgetItem(job.get("name", "")))

            # Duration
            started = job.get("startedAt", "")
            completed = job.get("completedAt", "")
            duration = self._calc_duration(started, completed)
            self._jobs_table.setItem(row, 2, QTableWidgetItem(duration))

        self._jobs_label.setText(f"{len(jobs)} jobs")

    # ── Log fetching ────────────────────────────────────────────────
    #
    # The original implementation called ``gh run view <id> --log-failed``
    # which downloads the run's *entire* log archive as a ZIP from
    # the GitHub API, extracts it, and then locally filters to
    # failed jobs. Even when a single job failed, the user paid for
    # downloading every job's full log — empirically 30–60 s of
    # silent network I/O on a normal CI with two parallel jobs.
    #
    # The new implementation uses the per-job log endpoint
    # (``gh run view <id> --job <job_id> --log``), which streams
    # only the requested job's plain text. We:
    #
    # 1. Reuse the cached job list (already fetched on row click)
    # 2. Pick the failed jobs (or all if the run succeeded and the
    #    user explicitly asked for full logs)
    # 3. Fetch each job sequentially, with progress shown live in
    #    the dialog
    # 4. Cache the assembled output so re-clicking is instant
    #
    # A "Open on GitHub" button is also exposed on the dialog for
    # users on flaky networks who'd rather just bail to the web UI.

    def _fetch_failed_logs(self) -> None:
        if not hasattr(self, "_selected_run_id"):
            return

        run_id = self._selected_run_id
        self._logs_btn.setEnabled(False)
        self._logs_btn.setText("Loading...")

        row = self._runs_table.currentRow()
        is_failure = False
        title = f"Logs — Run #{run_id}"
        if 0 <= row < len(self._runs_data):
            conclusion = self._runs_data[row].get("conclusion", "")
            if conclusion == "failure":
                is_failure = True
                title = f"Failed logs — Run #{run_id}"

        # Show the dialog immediately so the user has feedback even
        # if ``gh`` is slow to fetch the first byte. Set an explicit
        # progress message right away — the dialog's generic timer
        # is a fallback for when nothing else has anything to say,
        # and "Connecting to GitHub…" is a much better first
        # impression than "Loading logs… (3s)".
        self._log_dialog = _CICDLogDialog(title, self)
        web_url = self._build_run_url(run_id)
        if web_url:
            self._log_dialog.set_web_url(web_url)
        self._log_dialog.set_progress("Connecting to GitHub…")
        self._log_dialog.show()

        if not self._check_gh():
            self._log_dialog.set_content("Error: GitHub CLI (gh) not found.")
            self._logs_btn.setEnabled(True)
            self._logs_btn.setText("View Logs")
            return

        # Re-clicking the same run should be instant.
        if not hasattr(self, "_logs_cache"):
            self._logs_cache: dict[int, str] = {}
        if run_id in self._logs_cache:
            self._log_dialog.set_content(self._logs_cache[run_id])
            self._logs_btn.setEnabled(True)
            self._logs_btn.setText("View Logs")
            return

        # We need the job list to drive the per-job fetch. It's
        # almost always already cached from the row-selection
        # handler, but if the user clicked View Logs before that
        # populated, fall back to fetching it inline.
        cached_jobs = getattr(self, "_cached_jobs", {}).get(run_id)
        if cached_jobs is None:
            self._log_dialog.set_progress("Asking GitHub for the job list…")
            self._fetch_jobs_then_logs(run_id, is_failure)
            return

        self._dispatch_per_job_fetch(run_id, cached_jobs, is_failure)

    def _fetch_jobs_then_logs(self, run_id: int, is_failure: bool) -> None:
        """Fetch the job list, then chain into the log fetcher.

        Used when ``View Logs`` is clicked before the row-selection
        handler has populated ``_cached_jobs`` — rare, but it can
        happen when the panel is restored from session state.
        """
        import threading

        def worker() -> None:
            logger.info("cicd: fetching job list for run %s", run_id)
            output, code = self._run_gh(["run", "view", str(run_id), "--json", "jobs"])
            if code != 0:
                logger.warning("cicd: job list fetch failed (rc=%s): %s", code, output[:200])
                QTimer.singleShot(
                    0, lambda: self._on_logs_loaded(f"Could not list jobs: {output[:500]}", 1)
                )
                return
            try:
                jobs = json.loads(output).get("jobs", [])
            except json.JSONDecodeError:
                QTimer.singleShot(0, lambda: self._on_logs_loaded("Could not parse job list.", 1))
                return
            logger.info("cicd: got %d jobs for run %s", len(jobs), run_id)
            if not hasattr(self, "_cached_jobs"):
                self._cached_jobs = {}
            self._cached_jobs[run_id] = jobs
            QTimer.singleShot(0, lambda: self._dispatch_per_job_fetch(run_id, jobs, is_failure))

        threading.Thread(target=worker, daemon=True).start()

    def _dispatch_per_job_fetch(self, run_id: int, jobs: list[dict], is_failure: bool) -> None:
        """Spawn the worker that streams logs for each (failed) job."""
        # When the run failed, only fetch the failed jobs — that's
        # what the user actually wants to read and skipping passing
        # jobs cuts download time roughly in half on a typical
        # two-job CI. When the run succeeded, fetch all jobs.
        targets = jobs
        if is_failure:
            targets = [j for j in jobs if j.get("conclusion") == "failure"]
            if not targets:
                # Edge case: gh reports failure on the run but no
                # individual job matches. Fall back to all jobs so
                # the user sees *something* useful.
                targets = jobs

        if not targets:
            self._on_logs_loaded("(no jobs to fetch logs for)", 0)
            return

        # Show concrete progress synchronously, BEFORE spawning the
        # worker. Without this, users on slow networks watch the
        # generic "Still fetching…" placeholder for the first 30 s
        # while ``gh`` opens its TLS connection and waits for the
        # first byte. Setting progress here means the dialog reads
        # "Fetching job 1/N: <name>…" within milliseconds of the
        # click — no thread-scheduling roundtrip required.
        first_name = targets[0].get("name") or f"job-{targets[0].get('databaseId')}"
        if self._log_dialog is not None:
            self._log_dialog.set_progress(f"Fetching job 1/{len(targets)}: {first_name}…")

        import threading

        def worker() -> None:
            logger.info("cicd: streaming logs for %d job(s) of run %s", len(targets), run_id)
            collected: list[str] = []
            total = len(targets)
            for idx, job in enumerate(targets, start=1):
                job_id = job.get("databaseId")
                name = job.get("name", f"job-{job_id}") or f"job-{job_id}"
                conclusion = job.get("conclusion") or job.get("status", "")
                if not job_id:
                    continue

                # idx==1 was already announced synchronously above,
                # so only update for jobs 2..N. Saves one round-trip
                # of UI churn on the common single-job case.
                if idx > 1:
                    QTimer.singleShot(
                        0,
                        lambda i=idx, n=name: self._update_log_progress(
                            f"Fetching job {i}/{total}: {n}…"
                        ),
                    )

                logger.info("cicd: gh run view --job %s --log (job %d/%d)", job_id, idx, total)
                proc = subprocess.Popen(
                    [
                        "gh",
                        "run",
                        "view",
                        str(run_id),
                        "--job",
                        str(job_id),
                        "--log",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # line-buffered so we can stream
                    cwd=self._project_root or None,
                )
                if self._log_dialog is not None:
                    QTimer.singleShot(0, lambda p=proc: self._set_dialog_subprocess(p))

                # Stream stdout line by line so the dialog fills
                # progressively instead of staring at a frozen
                # placeholder until the entire log finishes
                # downloading. On a CI run with multi-MB logs
                # (PyQt6 install output, etc.) this is the
                # difference between "30 s of nothing then a wall
                # of text" and "first lines visible in <2 s".
                import time as _time

                started = _time.monotonic()
                lines: list[str] = []
                last_push = started
                # ``communicate`` can't be used alongside line-by-
                # line iteration; manage the timeout ourselves.
                # 45 s is well above typical (5-15 s) but short
                # enough one stuck job doesn't block the rest.
                per_job_timeout = 45
                try:
                    assert proc.stdout is not None
                    for line in iter(proc.stdout.readline, ""):
                        lines.append(line)
                        # Push a partial progress update every
                        # ~250 ms (or every 100 lines) so users
                        # see the KB count climb. Frequent enough
                        # to feel live; rare enough to skip the
                        # cost of re-rendering after each line on
                        # a 100k-line job log.
                        now = _time.monotonic()
                        if (now - last_push) >= 0.25 or (len(lines) % 100 == 0):
                            partial_kb = sum(len(s) for s in lines) // 1024
                            QTimer.singleShot(
                                0,
                                lambda i=idx, n=name, t=total, kb=partial_kb: (
                                    self._update_log_progress(
                                        f"Fetching job {i}/{t}: {n}… ({kb} KB)"
                                    )
                                ),
                            )
                            last_push = now
                        if (now - started) > per_job_timeout:
                            raise subprocess.TimeoutExpired(proc.args, per_job_timeout)
                    proc.wait(timeout=2)
                    body = "".join(lines)
                    if proc.returncode != 0:
                        stderr_text = (proc.stderr.read() if proc.stderr else "") or ""
                        body = (
                            f"(gh exited {proc.returncode} for job {name!r}: "
                            f"{stderr_text[:300]})\n{body}"
                        )
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                    partial = "".join(lines)
                    body = (
                        f"(timed out after {per_job_timeout}s for job {name!r}; "
                        f"showing partial log — try Open on GitHub for the full output)\n"
                        f"{partial}"
                    )
                except Exception as exc:
                    body = f"(error fetching log for job {name!r}: {exc})\n{''.join(lines)}"

                header = f"\n{'=' * 70}\n=== Job: {name}  ({conclusion or 'unknown'})\n{'=' * 70}\n"
                collected.append(header + body)

            output = "".join(collected) if collected else "(no logs)"
            self._logs_cache[run_id] = output
            logger.info("cicd: per-job fetch complete (%d KB)", len(output) // 1024)
            QTimer.singleShot(0, lambda: self._on_logs_loaded(output, 0))

        threading.Thread(target=worker, daemon=True).start()

    def _update_log_progress(self, msg: str) -> None:
        """Push a progress line into the open log dialog."""
        if hasattr(self, "_log_dialog") and self._log_dialog is not None:
            self._log_dialog.set_progress(msg)

    def _set_dialog_subprocess(self, proc) -> None:
        """Helper so the worker thread can register the in-flight ``gh``."""
        if hasattr(self, "_log_dialog") and self._log_dialog is not None:
            self._log_dialog.set_subprocess(proc)

    def _build_run_url(self, run_id: int) -> str | None:
        """Return ``https://github.com/<owner>/<repo>/actions/runs/<id>``.

        Cached per-project; one ``gh repo view`` lookup per project
        root for the lifetime of the panel.
        """
        if not self._project_root:
            return None
        if not hasattr(self, "_repo_slug_cache"):
            self._repo_slug_cache: dict[str, str | None] = {}
        cached = self._repo_slug_cache.get(self._project_root)
        if cached is None and self._project_root not in self._repo_slug_cache:
            output, code = self._run_gh(
                ["repo", "view", "--json", "owner,name", "-q", '.owner.login + "/" + .name']
            )
            cached = output.strip() if code == 0 else None
            self._repo_slug_cache[self._project_root] = cached
        if not cached:
            return None
        return f"https://github.com/{cached}/actions/runs/{run_id}"

    def _on_logs_loaded(self, output: str, code: int) -> None:
        self._logs_btn.setEnabled(True)
        self._logs_btn.setText("View Logs")

        if not hasattr(self, "_log_dialog") or self._log_dialog is None:
            return

        if code != 0:
            self._log_dialog.set_content(f"Error fetching logs: {output[:500]}")
        else:
            # Truncate very long logs
            if len(output) > 200_000:
                output = output[:200_000] + "\n\n... (log truncated at 200KB)"
            self._log_dialog.set_content(output or "(no failed logs)")

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _format_time(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = now - dt
            if diff.total_seconds() < 60:
                return "just now"
            if diff.total_seconds() < 3600:
                return f"{int(diff.total_seconds() / 60)}m ago"
            if diff.total_seconds() < 86400:
                return f"{int(diff.total_seconds() / 3600)}h ago"
            return dt.strftime("%b %d, %H:%M")
        except (ValueError, TypeError):
            return iso_str[:19]

    @staticmethod
    def _calc_duration(started: str, completed: str) -> str:
        if not started or not completed:
            return ""
        try:
            start = datetime.fromisoformat(started.replace("Z", "+00:00"))
            end = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            secs = int((end - start).total_seconds())
            if secs < 0:
                return ""  # Invalid timestamps
            if secs < 60:
                return f"{secs}s"
            if secs < 3600:
                return f"{secs // 60}m {secs % 60}s"
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
        except (ValueError, TypeError):
            return ""

    @staticmethod
    def _make_color(hex_color: str):
        from PyQt6.QtGui import QColor

        return QColor(hex_color)


class _CICDLogDialog(QWidget):
    """Standalone resizable window for viewing CI/CD run logs."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(1000, 700)
        self.setMinimumSize(500, 400)
        self.setStyleSheet(f"background: {tc.get('bg_base')};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("cicdLogHeader")
        header.setFixedHeight(40)
        header.setStyleSheet(
            f"#cicdLogHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 12, 0)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(title_label)
        h_layout.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        h_layout.addWidget(self._count_label)

        # "Open on GitHub" — instant escape hatch for users on
        # flaky networks. Hidden by default; revealed via
        # ``set_web_url`` when the panel resolves the run URL.
        # ``tc.get`` raises KeyError on unknown tokens, so use one
        # that's defined in every theme (bg_surface_raised) rather
        # than a tentative ``or fallback`` lookup.
        self._web_btn = QPushButton("Open on GitHub")
        self._web_btn.setFixedHeight(24)
        self._web_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._web_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; border-radius: 3px; "
            f"padding: 0 10px; font-size: {tc.FONT_XS}px; margin-left: 8px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        self._web_btn.setVisible(False)
        self._web_btn.clicked.connect(self._open_in_browser)
        h_layout.addWidget(self._web_btn)
        self._web_url: str | None = None

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(24)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_error')}; color: #fff; "
            f"border: none; border-radius: 3px; padding: 0 10px; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; margin-left: 8px; }}"
            "QPushButton:hover { background: #d43f3f; }"
        )
        self._cancel_btn.clicked.connect(self._cancel_loading)
        h_layout.addWidget(self._cancel_btn)

        layout.addWidget(header)

        # Process handle — set by the panel so cancel can kill the
        # current ``gh`` invocation. Re-set on each per-job step so
        # Cancel always targets the in-flight one.
        self._subprocess = None

        # Log viewer
        self._viewer = QPlainTextEdit()
        self._viewer.setReadOnly(True)
        mono = QFont("Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._viewer.setFont(mono)
        self._viewer.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 8px; }}"
        )
        self._viewer.setPlainText("Loading logs...")
        layout.addWidget(self._viewer)

        # Progress counter — updates every second while loading.
        # ``_has_progress_msg`` flips True once the per-job fetcher
        # calls ``set_progress`` so the timer stops overwriting the
        # specific "Fetching job N/M…" text with the generic
        # "Loading logs…" placeholder.
        import time

        self._start_time = time.monotonic()
        self._loading = True
        self._has_progress_msg = False
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_timer.start(1000)

    def _tick_progress(self) -> None:
        if not self._loading:
            self._progress_timer.stop()
            return
        import time

        elapsed = int(time.monotonic() - self._start_time)
        # The viewer body is owned by ``set_progress`` once the
        # per-job fetcher has something specific to say (e.g.
        # "Fetching job 2/3: build-wheel"). Until then, we render
        # a generic "loading" message that matches what the user
        # sees with no per-job feedback yet. The count label always
        # shows elapsed seconds so it's obvious time is passing.
        if not self._has_progress_msg:
            if elapsed < 10:
                self._viewer.setPlainText(f"Loading logs... ({elapsed}s)")
            elif elapsed < 30:
                self._viewer.setPlainText(f"Fetching logs from GitHub... ({elapsed}s)")
            else:
                self._viewer.setPlainText(
                    f"Still fetching... try Open on GitHub if this drags on. ({elapsed}s)"
                )
        self._count_label.setText(f"{elapsed}s")

    def set_progress(self, msg: str) -> None:
        """Replace the body with a structured progress message.

        Called by the per-job fetcher so the user sees exactly
        which job is currently being downloaded ("Fetching job
        2/3: build-wheel…") instead of a generic counter. Once
        ``set_content`` arrives the timer stops touching the body.
        """
        self._has_progress_msg = True
        if self._loading:
            self._viewer.setPlainText(msg)

    def set_web_url(self, url: str) -> None:
        """Reveal the Open on GitHub button with the given URL."""
        self._web_url = url
        self._web_btn.setVisible(True)

    def _open_in_browser(self) -> None:
        if not self._web_url:
            return
        import webbrowser

        webbrowser.open(self._web_url)

    def set_subprocess(self, proc) -> None:
        """Register the running subprocess so Cancel can kill it.

        The per-job fetcher re-registers each new ``gh`` invocation
        as it starts; the previous process has already exited by
        then so dropping the reference here is safe.
        """
        self._subprocess = proc

    def _cancel_loading(self) -> None:
        """User clicked Cancel — kill the subprocess if running."""
        self._loading = False
        self._progress_timer.stop()
        if self._subprocess is not None:
            try:
                self._subprocess.kill()
            except Exception:
                pass
            self._subprocess = None
        self._cancel_btn.setVisible(False)
        self._viewer.setPlainText("Cancelled.")
        self._count_label.setText("")

    def set_content(self, content: str) -> None:
        self._loading = False
        self._progress_timer.stop()
        self._cancel_btn.setVisible(False)
        self._viewer.setPlainText(content)
        line_count = content.count("\n") + 1
        self._count_label.setText(f"{line_count:,} lines")
        # Scroll to bottom so errors are visible first
        self._viewer.moveCursor(self._viewer.textCursor().MoveOperation.End)
