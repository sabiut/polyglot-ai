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

        self._setup_ui()

        # Auto-refresh every 30 seconds to catch live status changes
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(30_000)

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

    def _on_run_selected(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if row < 0 or row >= len(self._runs_data):
            return

        run = self._runs_data[row]
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

    def _fetch_failed_logs(self) -> None:
        if not hasattr(self, "_selected_run_id"):
            return

        run_id = self._selected_run_id
        self._logs_btn.setEnabled(False)
        self._logs_btn.setText("Loading...")

        # Determine if we should fetch failed logs or all logs
        row = self._runs_table.currentRow()
        use_failed_only = False
        title = f"Logs — Run #{run_id}"
        if 0 <= row < len(self._runs_data):
            conclusion = self._runs_data[row].get("conclusion", "")
            if conclusion == "failure":
                use_failed_only = True
                title = f"Failed logs — Run #{run_id}"

        # Open dialog immediately with loading state, then populate in background
        self._log_dialog = _CICDLogDialog(title, self)
        self._log_dialog.show()

        log_flag = "--log-failed" if use_failed_only else "--log"

        # Use Popen so we can kill the process if the user cancels
        if not self._check_gh():
            self._log_dialog.set_content("Error: GitHub CLI (gh) not found.")
            self._logs_btn.setEnabled(True)
            self._logs_btn.setText("View Logs")
            return

        import threading

        proc = subprocess.Popen(
            ["gh", "run", "view", str(run_id), log_flag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self._project_root or None,
        )
        self._log_dialog.set_subprocess(proc)

        def do_fetch():
            try:
                # 2-minute hard cap — after that, kill the process
                stdout, stderr = proc.communicate(timeout=120)
                code = proc.returncode
                output = stdout if code == 0 else (stderr or stdout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.communicate()
                except Exception:
                    pass
                output = (
                    "Timed out after 2 minutes. GitHub Actions logs for this run "
                    "are too large to download. Try clicking on individual jobs in "
                    "the table instead, or view the run directly in GitHub."
                )
                code = 1
            except Exception as exc:
                output = f"Error: {exc}"
                code = 1
            QTimer.singleShot(0, lambda: self._on_logs_loaded(output, code))

        threading.Thread(target=do_fetch, daemon=True).start()

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

        # Process handle — set by the panel so cancel can kill it
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

        # Progress counter — updates every second while loading
        import time

        self._start_time = time.monotonic()
        self._loading = True
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_timer.start(1000)

    def _tick_progress(self) -> None:
        if not self._loading:
            self._progress_timer.stop()
            return
        import time

        elapsed = int(time.monotonic() - self._start_time)
        if elapsed < 10:
            msg = f"Loading logs... ({elapsed}s)"
        elif elapsed < 30:
            msg = f"Downloading logs from GitHub... ({elapsed}s)"
        elif elapsed < 60:
            msg = f"Still downloading... this can take a while ({elapsed}s)"
        else:
            msg = f"Taking longer than usual... ({elapsed}s)"
        self._viewer.setPlainText(msg)
        self._count_label.setText(f"{elapsed}s")

    def set_subprocess(self, proc) -> None:
        """Register the running subprocess so Cancel can kill it."""
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
