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

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

# Status icons and colors
_STATUS_MAP = {
    "success": ("✔", "#4ec9b0"),
    "completed": ("✔", "#4ec9b0"),
    "failure": ("✘", "#f44747"),
    "cancelled": ("—", "#6a6a6a"),
    "skipped": ("—", "#6a6a6a"),
    "in_progress": ("●", "#cca700"),
    "queued": ("○", "#cca700"),
    "waiting": ("○", "#cca700"),
    "requested": ("○", "#cca700"),
    "pending": ("○", "#cca700"),
}


class CICDPanel(QWidget):
    """CI/CD Pipeline Inspector — shows GitHub Actions workflow runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._runs_data: list[dict] = []
        self._gh_available: bool | None = None
        self._selected_run_id: int | None = None
        self._has_in_progress_jobs = False

        self._setup_ui()

        # Auto-refresh every 30 seconds to catch live status changes
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(30_000)

        # Job refresh timer — polls every 5s while a run has in-progress jobs
        self._job_timer = QTimer(self)
        self._job_timer.timeout.connect(self._refresh_selected_jobs)
        self._job_timer.setInterval(5000)

    def showEvent(self, event) -> None:
        """Refresh when tab becomes visible."""
        super().showEvent(event)
        if self._project_root:
            QTimer.singleShot(100, self._refresh_runs)

    def set_project_root(self, path: Path | str) -> None:
        self._project_root = Path(path) if isinstance(path, str) else path

    def _auto_refresh(self) -> None:
        """Silent auto-refresh — only if we have a project and the tab is visible."""
        if self._project_root and self.isVisible():
            self._refresh_runs()

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

        self._logs_btn = QPushButton("View Failed Logs")
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
        self._jobs_table.setColumnWidth(0, 100)
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

    def _run_gh(self, args: list[str]) -> tuple[str, int]:
        """Run a gh CLI command and return (output, returncode)."""
        if not self._check_gh():
            return "Error: GitHub CLI (gh) not found. Install from https://cli.github.com", 1
        try:
            result = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                timeout=30,
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
            self._status_label.setText("  Open a project first")
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

        self._runs_table.setRowCount(len(self._runs_data))
        for row, run in enumerate(self._runs_data):
            status = run.get("conclusion") or run.get("status", "unknown")
            icon, color = _STATUS_MAP.get(status, ("?", tc.get("text_muted")))

            # Status icon
            status_item = QTableWidgetItem(icon)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(self._make_color(color))
            self._runs_table.setItem(row, 0, status_item)

            # Workflow name
            self._runs_table.setItem(row, 1, QTableWidgetItem(run.get("name", "")))

            # Branch
            self._runs_table.setItem(row, 2, QTableWidgetItem(run.get("headBranch", "")))

            # Time
            created = run.get("createdAt", "")
            display_time = self._format_time(created)
            self._runs_table.setItem(row, 3, QTableWidgetItem(display_time))

            # Conclusion
            conclusion = run.get("conclusion") or run.get("status", "")
            conc_item = QTableWidgetItem(conclusion)
            conc_item.setForeground(self._make_color(color))
            self._runs_table.setItem(row, 4, conc_item)

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._status_label.setText(f"  Last refreshed: {now} | {len(self._runs_data)} runs")

        # Auto-load jobs for the most recent run
        if self._runs_data:
            self._runs_table.selectRow(0)
            first_run = self._runs_data[0]
            run_id = first_run.get("databaseId")
            if run_id:
                self._selected_run_id = run_id
                run_status = first_run.get("status", "")
                conclusion = first_run.get("conclusion") or run_status
                self._logs_btn.setVisible(conclusion == "failure")
                self._log_viewer.setVisible(False)

                output, code = self._run_gh(["run", "view", str(run_id), "--json", "jobs"])
                self._on_jobs_loaded(output, code)

                # Start job polling if run is in progress
                if run_status == "in_progress" or not first_run.get("conclusion"):
                    self._job_timer.start()
                else:
                    self._job_timer.stop()

    def _on_run_selected(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if row < 0 or row >= len(self._runs_data):
            return

        run = self._runs_data[row]
        run_id = run.get("databaseId")
        if not run_id:
            return

        run_status = run.get("status", "")
        conclusion = run.get("conclusion") or run_status
        self._jobs_label.setText(f"Loading jobs for run #{run_id}...")
        self._logs_btn.setVisible(conclusion == "failure")
        self._log_viewer.setVisible(False)

        self._selected_run_id = run_id
        self._job_timer.stop()

        output, code = self._run_gh(["run", "view", str(run_id), "--json", "jobs"])
        self._on_jobs_loaded(output, code)

        # Start polling if the run is still in progress
        if run_status == "in_progress" or not run.get("conclusion"):
            self._job_timer.start()

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

        self._jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            # conclusion is set when job finishes (success/failure/cancelled)
            # status is the current state (queued/in_progress/completed)
            job_conclusion = job.get("conclusion")
            job_status = job.get("status", "unknown")

            # Use conclusion if available, otherwise use status
            if job_conclusion and job_conclusion != "":
                display_status = job_conclusion
            else:
                display_status = job_status

            icon, color = _STATUS_MAP.get(display_status, ("?", tc.get("text_muted")))

            status_item = QTableWidgetItem(f"{icon} {display_status}")
            status_item.setForeground(self._make_color(color))
            self._jobs_table.setItem(row, 0, status_item)

            self._jobs_table.setItem(row, 1, QTableWidgetItem(job.get("name", "")))

            # Duration
            started = job.get("startedAt", "")
            completed = job.get("completedAt", "")
            if completed:
                duration = self._calc_duration(started, completed)
            elif started:
                # Still running — show elapsed time
                duration = self._calc_duration(started, datetime.now(timezone.utc).isoformat())
                if duration:
                    duration = f"{duration}..."
            else:
                duration = ""
            self._jobs_table.setItem(row, 2, QTableWidgetItem(duration))

        # Check if any jobs are still running
        all_done = all(job.get("conclusion") and job.get("conclusion") != "" for job in jobs)
        if all_done:
            self._job_timer.stop()
            # Determine overall conclusion from jobs
            if all(j.get("conclusion") == "success" for j in jobs):
                overall = "success"
            elif any(j.get("conclusion") == "failure" for j in jobs):
                overall = "failure"
            else:
                overall = "completed"
            # Update the selected run's status directly in the table
            self._update_run_status(overall)
            # Also refresh the full runs list
            QTimer.singleShot(2000, self._refresh_runs)

        running = sum(1 for j in jobs if not j.get("conclusion") or j.get("conclusion") == "")
        if running:
            self._jobs_label.setText(f"{len(jobs)} jobs ({running} running)")
        else:
            self._jobs_label.setText(f"{len(jobs)} jobs")

    def _update_run_status(self, conclusion: str) -> None:
        """Update the currently selected run's status in the runs table immediately."""
        row = self._runs_table.currentRow()
        if row < 0:
            return
        icon, color = _STATUS_MAP.get(conclusion, ("?", tc.get("text_muted")))
        # Update status icon
        status_item = QTableWidgetItem(icon)
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        status_item.setForeground(self._make_color(color))
        self._runs_table.setItem(row, 0, status_item)
        # Update conclusion column
        conc_item = QTableWidgetItem(conclusion)
        conc_item.setForeground(self._make_color(color))
        self._runs_table.setItem(row, 4, conc_item)
        # Update local data
        if row < len(self._runs_data):
            self._runs_data[row]["conclusion"] = conclusion
            self._runs_data[row]["status"] = "completed"
        # Show failed logs button if applicable
        self._logs_btn.setVisible(conclusion == "failure")

    def _refresh_selected_jobs(self) -> None:
        """Re-fetch jobs for the currently selected run (called by timer)."""
        if not self._selected_run_id:
            self._job_timer.stop()
            return
        output, code = self._run_gh(["run", "view", str(self._selected_run_id), "--json", "jobs"])
        self._on_jobs_loaded(output, code)

    def _fetch_failed_logs(self) -> None:
        if not hasattr(self, "_selected_run_id"):
            return

        run_id = self._selected_run_id
        self._logs_btn.setEnabled(False)
        self._logs_btn.setText("Loading...")

        output, code = self._run_gh(["run", "view", str(run_id), "--log-failed"])
        self._on_logs_loaded(output, code)

    def _on_logs_loaded(self, output: str, code: int) -> None:
        self._logs_btn.setEnabled(True)
        self._logs_btn.setText("View Failed Logs")
        self._log_viewer.setVisible(True)

        if code != 0:
            self._log_viewer.setPlainText(f"Error fetching logs: {output[:500]}")
        else:
            # Truncate very long logs
            if len(output) > 50_000:
                output = output[:50_000] + "\n\n... (log truncated)"
            self._log_viewer.setPlainText(output)

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
