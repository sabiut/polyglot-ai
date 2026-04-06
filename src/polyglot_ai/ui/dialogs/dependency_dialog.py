"""First-run dialog that surfaces missing optional system dependencies."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
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

        # ── Global status line ──
        self._global_status = QLabel("")
        self._global_status.setWordWrap(True)
        self._global_status.setStyleSheet(
            "color: #4ec9b0; font-size: 11px; background: transparent; padding: 4px 0;"
        )
        layout.addWidget(self._global_status)

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

        try:
            result: InstallResult = await asyncio.to_thread(install_system_deps, to_install)
        except Exception as e:
            logger.exception("system installer raised unexpectedly")
            result = InstallResult(ok=False, message=f"Installer crashed: {e}")
        self._apply_install_all_result(button, result)

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
