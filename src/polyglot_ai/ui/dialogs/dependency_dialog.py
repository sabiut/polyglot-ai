"""First-run dialog that surfaces missing optional system dependencies."""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import safe_task
from polyglot_ai.core.dependency_check import (
    Dependency,
    InstallResult,
    detect_distro,
    has_pkexec,
    install_system_deps,
    install_uv,
    new_installer_log_path,
    parse_progress_marker,
)

logger = logging.getLogger(__name__)


class DependencyDialog(QDialog):
    """Non-blocking info dialog listing missing optional dependencies.

    For each missing item the dialog shows:
    - the runtime name
    - what it unlocks
    - a distro-appropriate install command (or docs URL)

    Provides a "Copy all commands" button and, for ``uv``, an inline
    "Install uv now" button that runs the official userland installer
    with no sudo required.
    """

    def __init__(
        self,
        missing: list[Dependency],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._missing = missing
        self._distro = detect_distro()
        self._dont_show_again = False

        self.setWindowTitle("Optional features need setup")
        self.setMinimumSize(640, 480)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background: #1e1e1e; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        # ── Header ──
        header = QLabel("⚠ Some optional features are unavailable")
        header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #e5a00d; background: transparent;"
        )
        layout.addWidget(header)

        subtitle_text = (
            "Polyglot AI uses external tools for MCP servers and DevOps "
            "panels. The ones listed below are not installed — the app "
            "will still run, but these features won't work until you "
            "install them."
        )
        if self._distro == "unknown":
            subtitle_text += (
                " <br><span style='color: #e5a00d;'>Could not auto-detect your Linux distribution "
                "— automatic install is disabled. See the manual commands below.</span>"
            )
        subtitle = QLabel(subtitle_text)
        subtitle.setTextFormat(Qt.TextFormat.RichText)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #aaa; font-size: 12px; background: transparent;")
        layout.addWidget(subtitle)

        # ── Scrollable list of missing deps ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #333; background: #252526; border-radius: 6px; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        content = QWidget()
        content.setStyleSheet("background: #252526;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 12)
        content_layout.setSpacing(14)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._uv_row_status: QLabel | None = None
        for dep in missing:
            content_layout.addWidget(self._create_dep_row(dep))

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        # ── Progress block (hidden until "Install all" is clicked) ──
        # Lives between the dep list and the bottom button row so
        # the user's eye lands on it during install. We keep it
        # ``setVisible(False)`` until needed so the empty-state
        # dialog doesn't show a 0 % bar over nothing.
        self._progress_box = self._build_progress_box()
        self._progress_box.setVisible(False)
        layout.addWidget(self._progress_box)

        # ── Global status line ──
        self._global_status = QLabel("")
        self._global_status.setWordWrap(True)
        self._global_status.setStyleSheet(
            "color: #4ec9b0; font-size: 11px; background: transparent; padding: 4px 0;"
        )
        layout.addWidget(self._global_status)

        # State for live log tail. Populated by _install_all and
        # consumed by ``_poll_install_log`` while the install runs.
        self._install_log_path: Path | None = None
        self._install_log_seek: int = 0
        self._install_log_timer: QTimer | None = None
        self._install_total: int = 0
        # Remember which slugs we've already coloured green in the
        # dep list so the second progress tick doesn't re-render
        # them every poll. Keyed by ``Dependency.key``.
        self._dep_status_labels: dict[str, QLabel] = {}
        # Recent stderr/stdout lines for the collapsed output panel.
        # ``deque`` with a bounded length keeps memory predictable
        # when an installer logs a verbose dependency tree.
        self._output_buffer: deque[str] = deque(maxlen=300)

        # ── "Don't show again" + buttons ──
        bottom = QHBoxLayout()
        self._dont_show_cb = QCheckBox("Don't show this again")
        self._dont_show_cb.setStyleSheet(
            "QCheckBox { color: #aaa; font-size: 12px; background: transparent; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )
        bottom.addWidget(self._dont_show_cb)
        bottom.addStretch()

        copy_btn = QPushButton("Copy all commands")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(
            "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
            "border-radius: 4px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        copy_btn.clicked.connect(self._on_copy)
        bottom.addWidget(copy_btn)

        # Install-all button — only shown when at least one dep can be
        # auto-installed on this distro (i.e. its hint is a shell command,
        # not a docs URL).
        if self._has_auto_installable():
            install_all_btn = QPushButton("Install all")
            install_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            install_all_btn.setToolTip(
                "Prompts for your password once (via pkexec) and installs "
                "all missing system dependencies. uv is installed separately."
            )
            install_all_btn.setStyleSheet(
                "QPushButton { background: #4ec9b0; color: #0a1512; border: none; "
                "border-radius: 4px; padding: 6px 14px; font-size: 12px; font-weight: 600; }"
                "QPushButton:hover { background: #6fe0c8; }"
                "QPushButton:disabled { background: #355; color: #888; }"
            )
            install_all_btn.clicked.connect(lambda _, b=install_all_btn: self._install_all(b))
            bottom.addWidget(install_all_btn)
            self._install_all_btn: QPushButton | None = install_all_btn
        else:
            self._install_all_btn = None

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet(
            "QPushButton { background: #0e639c; color: white; border: none; "
            "border-radius: 4px; padding: 6px 16px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #1a8ae8; }"
        )
        dismiss_btn.clicked.connect(self._on_dismiss)
        bottom.addWidget(dismiss_btn)

        layout.addLayout(bottom)

    def _create_dep_row(self, dep: Dependency) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        # Header line: • name
        header = QLabel(f"• <b>{dep.name}</b> not found")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet("color: #e0e0e0; font-size: 13px; background: transparent;")
        rl.addWidget(header)

        # Purpose line
        purpose = QLabel(f"    {dep.purpose}")
        purpose.setWordWrap(True)
        purpose.setStyleSheet("color: #888; font-size: 11px; background: transparent;")
        rl.addWidget(purpose)

        # Install command line
        hint = dep.install_hint(self._distro)
        if hint.startswith(("http://", "https://")):
            cmd_text = f"    → See {hint}"
        else:
            cmd_text = f"    → Run: {hint}"
        cmd = QLabel(cmd_text)
        cmd.setWordWrap(True)
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmd.setStyleSheet(
            "color: #4ec9b0; font-size: 11px; font-family: monospace; background: transparent;"
        )
        rl.addWidget(cmd)

        # Special case: uv can be installed in-process (no sudo required).
        if dep.key == "uv":
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(16, 4, 0, 0)
            install_btn = QPushButton("Install uv now")
            install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            install_btn.setStyleSheet(
                "QPushButton { background: #4ec9b0; color: #0a1512; border: none; "
                "border-radius: 4px; padding: 5px 12px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background: #6fe0c8; }"
                "QPushButton:disabled { background: #355; color: #888; }"
            )
            status = QLabel("")
            status.setWordWrap(True)
            status.setStyleSheet("color: #888; font-size: 11px; background: transparent;")
            self._uv_row_status = status

            install_btn.clicked.connect(lambda _, b=install_btn: self._install_uv(b))
            btn_row.addWidget(install_btn)
            btn_row.addWidget(status, stretch=1)
            rl.addLayout(btn_row)

        return row

    def _install_uv(self, button: QPushButton) -> None:
        """Kick off the uv installer on a worker thread."""
        button.setEnabled(False)
        button.setText("Installing…")
        if self._uv_row_status:
            self._uv_row_status.setText("Running the official uv installer…")
            self._uv_row_status.setStyleSheet(
                "color: #e5a00d; font-size: 11px; background: transparent;"
            )
        safe_task(self._run_uv_install(button), name="install_uv")

    async def _run_uv_install(self, button: QPushButton) -> None:
        """Worker coroutine: runs the blocking installer off the UI thread."""
        import asyncio

        try:
            result: InstallResult = await asyncio.to_thread(install_uv)
        except Exception as e:
            logger.exception("uv installer raised unexpectedly")
            result = InstallResult(ok=False, message=f"Installer crashed: {e}")
        self._apply_uv_result(button, result)

    def _apply_uv_result(self, button: QPushButton, result: InstallResult) -> None:
        if self._uv_row_status:
            colour = "#4ec9b0" if result.ok else "#f48771"
            self._uv_row_status.setText(result.message)
            self._uv_row_status.setStyleSheet(
                f"color: {colour}; font-size: 11px; background: transparent;"
            )
        if result.ok:
            button.setText("Installed ✓")
        else:
            button.setText("Install failed — retry")
            button.setEnabled(True)

    def _has_auto_installable(self) -> bool:
        """True if any missing dep (besides uv) has a shell install command."""
        for dep in self._missing:
            if dep.key == "uv":
                continue  # handled by inline button
            hint = dep.install_hint(self._distro)
            if hint and not hint.startswith(("http://", "https://")):
                return True
        return False

    def _build_progress_box(self) -> QWidget:
        """Construct the install-progress widget block.

        Three rows:
        1. A status label ("Installing 1 of 3: arduino-cli")
        2. A determinate ``QProgressBar`` (range 0..total)
        3. A "Show output" toggle revealing a small monospace tail
           of the installer's stdout/stderr — useful for users who
           want to know what's happening but not so prominent that
           it overwhelms the rest of the dialog.
        """
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(6)

        self._progress_label = QLabel("Preparing…")
        self._progress_label.setStyleSheet(
            "color: #e0e0e0; font-size: 12px; background: transparent;"
        )
        v.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #1a1a1a; border: 1px solid #333; "
            "border-radius: 4px; }"
            "QProgressBar::chunk { background: #4ec9b0; border-radius: 3px; }"
        )
        v.addWidget(self._progress_bar)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        self._show_output_btn = QPushButton("Show installer output ▾")
        self._show_output_btn.setCheckable(True)
        self._show_output_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_output_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; "
            "font-size: 11px; padding: 2px 0; text-align: left; }"
            "QPushButton:hover { color: #ccc; }"
        )
        self._show_output_btn.toggled.connect(self._on_toggle_output)
        toggle_row.addWidget(self._show_output_btn)
        toggle_row.addStretch()
        v.addLayout(toggle_row)

        self._output_view = QPlainTextEdit()
        self._output_view.setReadOnly(True)
        self._output_view.setVisible(False)
        self._output_view.setMinimumHeight(120)
        self._output_view.setMaximumHeight(180)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self._output_view.setFont(font)
        self._output_view.setStyleSheet(
            "QPlainTextEdit { background: #0a0a0a; color: #cfcfcf; "
            "border: 1px solid #333; border-radius: 4px; padding: 6px; }"
        )
        v.addWidget(self._output_view)

        return wrap

    def _on_toggle_output(self, checked: bool) -> None:
        self._show_output_btn.setText(
            "Hide installer output ▴" if checked else "Show installer output ▾"
        )
        self._output_view.setVisible(checked)
        if checked:
            # Scroll to the latest line whenever the panel is
            # revealed so the user lands on "what just happened".
            sb = self._output_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _install_all(self, button: QPushButton) -> None:
        """Kick off the system installer on a worker thread.

        On pkexec systems this waits for the installer to actually
        finish and checks the exit code. On the terminal-fallback
        path it can only verify the terminal spawned.
        """
        to_install = [d for d in self._missing if d.key != "uv"]
        if not to_install:
            return

        button.setEnabled(False)
        via = "pkexec" if has_pkexec() else "a terminal"
        # Reveal the progress block; preset a determinate range so
        # the bar advances by ticks instead of pulsing indeterminately
        # (which reads as "stuck" in user testing).
        self._install_total = len(to_install)
        self._progress_bar.setRange(0, self._install_total)
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"Preparing… (0 of {self._install_total})")
        self._progress_box.setVisible(True)
        self._output_buffer.clear()
        self._output_view.setPlainText("")
        # Stash the per-dep label refs so the progress poll can
        # mark each dep ✓ as it completes.
        self._dep_status_labels = self._collect_dep_status_labels()

        if has_pkexec():
            button.setText("Installing…")
            self._global_status.setText(
                f"Starting installer via {via}. Enter your password when prompted. "
                "The dialog stays responsive while it runs."
            )
        else:
            button.setText("Launching installer…")
            self._global_status.setText(
                "Opening a terminal — enter your sudo password there. Return here "
                "and restart Polyglot AI once it finishes."
            )
        self._global_status.setStyleSheet(
            "color: #e5a00d; font-size: 11px; background: transparent; padding: 4px 0;"
        )
        safe_task(self._run_install_all(button, to_install), name="install_system_deps")

    async def _run_install_all(self, button: QPushButton, to_install: list[Dependency]) -> None:
        """Worker coroutine: runs the blocking installer off the UI thread."""
        import asyncio

        # Pre-allocate the log file so the GUI can tail it from the
        # moment the password prompt appears, instead of finding out
        # where the installer wrote *after* it's done.
        log_path = new_installer_log_path()
        self._install_log_path = log_path
        self._install_log_seek = 0
        self._start_log_poll_timer()

        try:
            result: InstallResult = await asyncio.to_thread(
                install_system_deps, to_install, log_path=log_path
            )
        except Exception as e:
            logger.exception("system installer raised unexpectedly")
            result = InstallResult(ok=False, message=f"Installer crashed: {e}")
        finally:
            self._stop_log_poll_timer()
            # One last drain so any lines written between the last
            # poll tick and the worker's return aren't lost.
            self._poll_install_log()
        self._apply_install_all_result(button, result)

    def _start_log_poll_timer(self) -> None:
        """Begin tailing the installer log on a 250 ms cadence.

        250 ms feels live without burning CPU. Slower would make the
        progress bar visibly lag the password prompt's progress;
        faster would re-stat the file > 4 times a second for no
        perceptible benefit.
        """
        if self._install_log_timer is not None:
            self._install_log_timer.stop()
        self._install_log_timer = QTimer(self)
        self._install_log_timer.timeout.connect(self._poll_install_log)
        self._install_log_timer.start(250)

    def _stop_log_poll_timer(self) -> None:
        if self._install_log_timer is not None:
            self._install_log_timer.stop()
            self._install_log_timer = None

    def _poll_install_log(self) -> None:
        """Read any newly-appended bytes from the installer log.

        Stateful — each call resumes from the byte offset the last
        call ended at, so we don't re-process lines we've already
        rendered. Keeps the latest 300 lines in
        ``self._output_buffer`` so the toggleable output panel can
        show them; meanwhile, ``@@PROGRESS@@`` markers update the
        progress bar and current-step label.
        """
        if self._install_log_path is None:
            return
        try:
            with self._install_log_path.open("rb") as f:
                f.seek(self._install_log_seek)
                chunk = f.read()
                self._install_log_seek = f.tell()
        except OSError:
            # File may not have been created yet (pkexec auth still
            # pending) — try again on the next tick.
            return
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        for line in text.splitlines():
            self._handle_log_line(line)

    def _handle_log_line(self, line: str) -> None:
        """Route a single log line to either the progress bar or the output panel."""
        progress = parse_progress_marker(line)
        if progress is not None:
            if progress.done:
                self._progress_bar.setValue(self._install_total)
                self._progress_label.setText("Wrapping up…")
                return
            self._progress_bar.setValue(progress.current)
            display = self._slug_to_display_name(progress.slug)
            self._progress_label.setText(
                f"Installing {progress.current} of {progress.total}: {display}"
            )
            # Mark the previous slug as ✓ in the dep list when we
            # advance past it. (Skipped if this is the first tick.)
            if progress.current > 1:
                self._mark_previous_dep_done(progress.current)
            return

        # Not a progress marker — stash for the output panel.
        self._output_buffer.append(line)
        if self._show_output_btn.isChecked():
            self._output_view.appendPlainText(line)

    def _slug_to_display_name(self, slug: str) -> str:
        for dep in self._missing:
            if dep.key == slug:
                return dep.name
        return slug

    def _collect_dep_status_labels(self) -> dict[str, QLabel]:
        """Return a map ``slug -> per-row status label`` so the
        progress poll can post a green ✓ next to each dep as it
        finishes. The labels are added on demand the first time
        we need them; the dep rows themselves were built earlier.
        """
        # Walk the dep rows we already created, tagging each with
        # an empty status label we can update later. Idempotent —
        # safe to call multiple times.
        labels: dict[str, QLabel] = {}
        scroll_area = self.findChild(QScrollArea)
        if scroll_area is None:
            return labels
        # We don't know which row corresponds to which dep without
        # extra bookkeeping; rather than retrofit that, we just
        # update the global progress label and bar, which is what
        # the user actually watches anyway.
        return labels

    def _mark_previous_dep_done(self, current: int) -> None:
        # Reserved for a future per-row tick. Currently a no-op —
        # the global progress bar and "Installing X of Y" line are
        # the visible feedback. Kept as a hook so adding per-row
        # ticks later is a one-place change.
        pass

    def _apply_install_all_result(self, button: QPushButton, result: InstallResult) -> None:
        self._global_status.setText(result.message)
        colour = "#4ec9b0" if result.ok else "#f48771"
        self._global_status.setStyleSheet(
            f"color: {colour}; font-size: 11px; background: transparent; padding: 4px 0;"
        )
        if result.ok:
            button.setText("Done ✓")
        else:
            button.setText("Install all")
            button.setEnabled(True)

    def _on_copy(self) -> None:
        lines = []
        for dep in self._missing:
            hint = dep.install_hint(self._distro)
            if hint.startswith(("http://", "https://")):
                lines.append(f"# {dep.name}: see {hint}")
            else:
                lines.append(f"# {dep.name} — {dep.purpose}")
                lines.append(hint)
        text = "\n".join(lines)
        clip = QGuiApplication.clipboard()
        if clip is None:
            logger.warning("Clipboard unavailable; cannot copy install commands")
            self._global_status.setText(
                "Clipboard unavailable — select the text manually from the list above."
            )
            self._global_status.setStyleSheet(
                "color: #e5a00d; font-size: 11px; background: transparent; padding: 4px 0;"
            )
            return
        clip.setText(text)
        logger.info("Copied dependency install commands to clipboard")
        self._global_status.setText("Install commands copied to clipboard.")
        self._global_status.setStyleSheet(
            "color: #4ec9b0; font-size: 11px; background: transparent; padding: 4px 0;"
        )

    def _on_dismiss(self) -> None:
        self._dont_show_again = self._dont_show_cb.isChecked()
        self.accept()

    @property
    def dont_show_again(self) -> bool:
        return self._dont_show_again
