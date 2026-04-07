"""Dialog that displays an AI-generated pull-request description."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.review.models import PRSummary

logger = logging.getLogger(__name__)


def _load_pr_template(project_root: Path | None) -> str | None:
    """Look for a .github/PULL_REQUEST_TEMPLATE.md in the project.

    Returns the template text if found, ``None`` otherwise.
    """
    if project_root is None:
        return None
    candidates = [
        project_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        project_root / ".github" / "pull_request_template.md",
        project_root / "docs" / "PULL_REQUEST_TEMPLATE.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Could not read PR template %s: %s", path, e)
    return None


class PRSummaryDialog(QDialog):
    """Shows the generated PR title + body with copy / create-PR actions."""

    # Cross-thread signal so the worker thread running `gh pr create`
    # can deliver its result back onto the GUI thread without using
    # safe_task / qasync (which would re-enter the parent task that
    # opened this modal dialog and deadlock).
    _gh_finished = pyqtSignal(int, str, str)  # rc, stdout, stderr

    def __init__(
        self,
        result: PRSummary,
        project_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self._project_root = project_root
        self._template = _load_pr_template(project_root)
        self._gh_finished.connect(self._on_gh_finished)
        self._create_btn: QPushButton | None = None

        self.setWindowTitle("PR description")
        self.setMinimumSize(720, 560)
        self.setModal(True)
        self.setStyleSheet("QDialog { background: #1e1e1e; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        # Failure path: render an error card and a dismiss button.
        if result.status != "ok":
            self._render_error(layout)
            return

        # ── Title field ──
        title_lbl = QLabel("Title")
        title_lbl.setStyleSheet(
            "color: #888; font-size: 11px; font-weight: 600; background: transparent;"
        )
        layout.addWidget(title_lbl)

        self._title_edit = QLineEdit(result.title)
        self._title_edit.setStyleSheet(
            "QLineEdit { background: #252526; color: #e0e0e0; border: 1px solid #333; "
            "border-radius: 4px; padding: 8px 10px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0e639c; }"
        )
        layout.addWidget(self._title_edit)

        # ── Body field ──
        body_lbl = QLabel("Body")
        body_lbl.setStyleSheet(
            "color: #888; font-size: 11px; font-weight: 600; "
            "background: transparent; margin-top: 4px;"
        )
        layout.addWidget(body_lbl)

        self._body_edit = QTextEdit()
        self._body_edit.setPlainText(result.to_markdown(self._template))
        self._body_edit.setAcceptRichText(False)
        self._body_edit.setStyleSheet(
            "QTextEdit { background: #252526; color: #e0e0e0; border: 1px solid #333; "
            "border-radius: 4px; padding: 8px 10px; font-size: 12px; "
            "font-family: 'JetBrains Mono', monospace; }"
            "QTextEdit:focus { border-color: #0e639c; }"
        )
        layout.addWidget(self._body_edit, stretch=1)

        # ── Meta row: stats + model ──
        meta = QLabel(
            f"{result.files_changed} files · +{result.additions}/-{result.deletions} · "
            f"{result.provider or '?'}/{result.model or '?'}"
        )
        meta.setStyleSheet("color: #666; font-size: 11px; background: transparent;")
        layout.addWidget(meta)

        # ── Status line ──
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #4ec9b0; font-size: 11px; background: transparent;")
        layout.addWidget(self._status)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        copy_btn = self._make_button(
            "Copy title + body",
            primary=False,
            tooltip="Copy the title and markdown body to your clipboard",
        )
        copy_btn.clicked.connect(self._on_copy)
        btn_row.addWidget(copy_btn)

        if shutil.which("gh") and self._in_git_repo():
            create_btn = self._make_button(
                "Create PR with gh",
                primary=True,
                tooltip="Runs `gh pr create` with this title and body",
            )
            create_btn.clicked.connect(self._on_create_pr)
            btn_row.addWidget(create_btn)
            self._create_btn = create_btn

        close_btn = self._make_button("Close", primary=False)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ── Rendering helpers ──

    def _render_error(self, layout: QVBoxLayout) -> None:
        title = QLabel("🔴 Could not generate PR description")
        title.setStyleSheet(
            "color: #f44747; font-size: 14px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title)

        msg = QLabel(self._result.error or "The AI did not return a valid response.")
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #e0d0d0; font-size: 12px; background: transparent;")
        layout.addWidget(msg)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = self._make_button("Close", primary=True)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _make_button(self, label: str, primary: bool, tooltip: str = "") -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            btn.setToolTip(tooltip)
        if primary:
            btn.setStyleSheet(
                "QPushButton { background: #0e639c; color: white; border: none; "
                "border-radius: 4px; padding: 7px 16px; font-size: 12px; font-weight: 600; }"
                "QPushButton:hover { background: #1a8ae8; }"
                "QPushButton:disabled { background: #355; color: #888; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
                "border-radius: 4px; padding: 7px 14px; font-size: 12px; }"
                "QPushButton:hover { background: #4a4a4a; }"
            )
        return btn

    # ── Actions ──

    def _in_git_repo(self) -> bool:
        if self._project_root is None:
            return False
        return (self._project_root / ".git").exists()

    def _on_copy(self) -> None:
        title = self._title_edit.text()
        body = self._body_edit.toPlainText()
        text = f"{title}\n\n{body}"
        clip = QGuiApplication.clipboard()
        if clip is None:
            logger.warning("Clipboard unavailable; cannot copy PR description")
            self._set_status("Clipboard unavailable — select the text manually.", ok=False)
            return
        clip.setText(text)
        logger.info("Copied PR title + body to clipboard")
        self._set_status("Title and body copied to clipboard.", ok=True)

    def _on_create_pr(self) -> None:
        """Invoke `gh pr create --title … --body …` on a worker thread.

        Uses ``threading.Thread`` (not ``safe_task``) because this dialog
        is opened modally from inside another async task — calling
        ``safe_task`` here would re-enter the qasync loop and crash with
        ``RuntimeError: Cannot enter into task ... while another task
        is being executed``. The worker thread emits ``_gh_finished``
        when done; the signal is auto-marshalled to the GUI thread by Qt.
        """
        title = self._title_edit.text().strip()
        body = self._body_edit.toPlainText()
        if not title:
            self._set_status("Title cannot be empty.", ok=False)
            return
        if self._project_root is None or not self._in_git_repo():
            self._set_status("No git project open — cannot create a PR.", ok=False)
            return

        if self._create_btn is not None:
            self._create_btn.setEnabled(False)
            self._create_btn.setText("Creating PR…")
        self._set_status("Running `gh pr create`…", ok=True)

        import threading

        threading.Thread(
            target=self._gh_pr_create_worker,
            args=(title, body, str(self._project_root)),
            daemon=True,
            name="gh_pr_create",
        ).start()

    def _gh_pr_create_worker(self, title: str, body: str, cwd: str) -> None:
        """Background-thread worker. Runs gh pr create and emits a signal."""
        try:
            proc = subprocess.run(
                ["gh", "pr", "create", "--title", title, "--body", body],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self._gh_finished.emit(proc.returncode, proc.stdout or "", proc.stderr or "")
        except subprocess.TimeoutExpired:
            self._gh_finished.emit(-1, "", "timed out")
        except FileNotFoundError as e:
            self._gh_finished.emit(-2, "", str(e))
        except OSError as e:
            self._gh_finished.emit(-3, "", str(e))
        except Exception as e:  # noqa: BLE001 — defensive: signal MUST fire
            logger.exception("gh pr create worker crashed")
            self._gh_finished.emit(-4, "", str(e))

    def _on_gh_finished(self, rc: int, stdout: str, stderr: str) -> None:
        """Slot: runs on the GUI thread when the worker finishes."""
        if self._create_btn is not None:
            self._create_btn.setEnabled(True)
            self._create_btn.setText("Create PR with gh")

        if rc == -1:
            logger.error("gh pr create timed out")
            self._set_status("`gh pr create` timed out after 180s.", ok=False)
            return
        if rc == -2:
            logger.error("gh binary not found: %s", stderr)
            self._set_status(
                "gh CLI not installed. See https://cli.github.com/ to install it.",
                ok=False,
            )
            return
        if rc == -3:
            logger.error("gh pr create launch failed: %s", stderr)
            self._set_status(f"Could not launch gh: {stderr}", ok=False)
            return
        if rc == -4:
            self._set_status(f"Worker crashed: {stderr}", ok=False)
            return
        if rc != 0:
            err = stderr.strip() or stdout.strip() or "Unknown error"
            logger.error("gh pr create failed: rc=%d %s", rc, err)
            self._set_status(f"gh pr create failed: {err[:200]}", ok=False)
            return

        url = stdout.strip()
        logger.info("PR created: %s", url)
        self._set_status(f"PR created: {url}", ok=True)

    def _set_status(self, msg: str, ok: bool) -> None:
        colour = "#4ec9b0" if ok else "#f48771"
        self._status.setText(msg)
        self._status.setStyleSheet(f"color: {colour}; font-size: 11px; background: transparent;")
