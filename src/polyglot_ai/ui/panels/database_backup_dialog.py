"""Back up a database, and load a backup into one.

Two dialogs over :mod:`polyglot_ai.core.db_backup` and
:mod:`polyglot_ai.core.db_restore`. The work runs on a worker thread;
progress comes back through a Qt signal, which is what carries it
across to the GUI thread.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.async_utils import run_blocking, safe_task
from polyglot_ai.core.db_backup import (
    BackupCancelled,
    BackupError,
    BackupSource,
    Progress,
    default_filename,
    install_hint,
    methods_for,
    run_backup,
)
from polyglot_ai.core.db_restore import inspect_file, plan_restore, run_restore
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"


def _duration(seconds: float) -> str:
    if seconds < 1:
        return "under a second"
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    return f"{seconds / 60:.1f} minutes"


def progress_text(update: Progress) -> str:
    """One line saying what the backup or restore is doing now."""
    if update.stage == "connecting":
        return "Connecting…"
    if update.stage == "reading":
        return "Reading the list of tables…"
    if update.stage == "table":
        return (
            f"Table {update.index} of {update.total}: {update.table} — {update.rows:,} rows so far"
        )
    if update.stage == "dumping":
        return f"Dumping… {human_size(update.size)} written"
    if update.stage == "copying":
        return "Copying the database…"
    if update.stage == "checking":
        return "Checking the file…"
    if update.stage == "loading":
        done = f"{update.index:,} statements, " if update.index else ""
        return f"Loading… {done}{human_size(update.size)} of {human_size(update.total)} read"
    return ""


class _WorkDialog(QDialog):
    """Shared frame: a form, a progress line, and Start / Cancel."""

    _progress = pyqtSignal(object)
    TITLE = ""
    START = ""

    def __init__(self, connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = connection
        self._source = BackupSource.of(connection)
        self._cancel = threading.Event()
        self._running = False
        self.finished_ok = False
        self.setWindowTitle(f"{self.TITLE} — {connection.name}")
        self.setFixedWidth(540)
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; }}"
            f"QLabel {{ background: transparent; font-size: {tc.FONT_SM}px; }}"
            f"QLineEdit, QComboBox {{ background: {tc.get('bg_input')}; "
            f"color: {tc.get('text_primary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 4px; padding: 6px 9px; font-size: {tc.FONT_MD}px; }}"
            f"QLineEdit:focus, QComboBox:focus {{ border-color: {tc.get('accent_primary')}; }}"
            f"QCheckBox {{ color: {tc.get('text_secondary')}; font-size: {tc.FONT_SM}px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("dbBackupHeader")
        header.setStyleSheet(
            f"#dbBackupHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        head = QVBoxLayout(header)
        head.setContentsMargins(16, 10, 16, 10)
        head.setSpacing(2)
        title = QLabel(f"{self.TITLE}: {connection.name}")
        title.setStyleSheet(
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; color: {tc.get('text_heading')};"
        )
        head.addWidget(title)
        where = QLabel(self._source.where)
        where.setStyleSheet(f"color: {tc.get('text_muted')};")
        where.setWordWrap(True)
        head.addWidget(where)
        outer.addWidget(header)

        body = QWidget()
        self._form = QVBoxLayout(body)
        self._form.setContentsMargins(18, 14, 18, 10)
        self._form.setSpacing(8)
        outer.addWidget(body)
        self._build_form()

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._form.addWidget(self._status)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {tc.get('bg_input')}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {tc.get('accent_primary')}; border-radius: 3px; }}"
        )
        self._form.addWidget(self._bar)

        footer = QWidget()
        footer.setObjectName("dbBackupFooter")
        footer.setStyleSheet(
            f"#dbBackupFooter {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(16, 10, 16, 10)
        buttons.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.setObjectName("dbBackupClose")
        self._close_btn.setFixedHeight(30)
        self._close_btn.setStyleSheet(
            f"#dbBackupClose {{ background: transparent; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 4px; padding: 0 16px; "
            f"font-size: {tc.FONT_SM}px; }}"
            f"#dbBackupClose:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        self._close_btn.clicked.connect(self._close_or_cancel)
        buttons.addWidget(self._close_btn)
        self._start_btn = QPushButton(self.START)
        self._start_btn.setObjectName("dbBackupStart")
        self._start_btn.setFixedHeight(30)
        self._start_btn.setStyleSheet(
            f"#dbBackupStart {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 4px; "
            f"padding: 0 20px; font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"#dbBackupStart:hover {{ background: {tc.get('accent_primary_hover')}; }}"
            f"#dbBackupStart:disabled {{ background: {tc.get('bg_input')}; "
            f"color: {tc.get('text_disabled')}; }}"
        )
        self._start_btn.clicked.connect(self._start)
        buttons.addWidget(self._start_btn)
        outer.addWidget(footer)

        self._progress.connect(self._on_progress)
        self._ready()

    # ── for subclasses ──

    def _build_form(self) -> None:
        raise NotImplementedError

    def _ready(self) -> None:
        """Enable Start when the form can be acted on."""

    def _work(self):
        """The blocking call. Runs on a worker thread."""
        raise NotImplementedError

    def _before(self) -> bool:
        """Last checks on the GUI thread; False keeps the dialog as it is."""
        return True

    async def _prepare(self) -> None:
        """Anything to await before the work starts."""

    def _done(self, result) -> str:
        raise NotImplementedError

    # ── shared behaviour ──

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"font-weight: 600; color: {tc.get('text_secondary')};")
        return label

    def _note(self, text: str = "", colour: str = "text_muted") -> QLabel:
        note = QLabel(text)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {tc.get(colour)};")
        return note

    def _say(self, text: str, colour: str = "text_primary") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {tc.get(colour)};")

    def _on_progress(self, update: Progress) -> None:
        if not self._running:
            return
        self._say(progress_text(update), "text_secondary")
        if update.stage in ("table", "copying", "loading") and update.total:
            done = update.size if update.stage == "loading" else update.index
            self._bar.setRange(0, 1000)
            self._bar.setValue(min(1000, int(1000 * done / update.total)))
        else:
            self._bar.setRange(0, 0)  # busy

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._bar.setVisible(running)
        self._start_btn.setEnabled(not running)
        self._close_btn.setText("Cancel" if running else "Close")
        for widget in self._inputs():
            widget.setEnabled(not running)

    def _inputs(self) -> list[QWidget]:
        return []

    def _start(self) -> None:
        if self._running or not self._before():
            return
        self._cancel.clear()
        self._set_running(True)
        self._bar.setRange(0, 0)
        self._say("Starting…", "text_secondary")
        safe_task(self._run(), name="db_backup_work")

    async def _run(self) -> None:
        try:
            await self._prepare()
            result = await run_blocking(self._work)
        except BackupCancelled as exc:
            self._set_running(False)
            self._say(str(exc), "accent_warning")
            return
        except BackupError as exc:
            self._set_running(False)
            self._say(str(exc), "accent_error")
            return
        except Exception as exc:
            logger.exception("Backup/restore crashed")
            self._set_running(False)
            self._say(f"Unexpected error: {exc}", "accent_error")
            return
        self._set_running(False)
        self.finished_ok = True
        self._say(self._done(result), "accent_success_muted")
        self._ready()

    def _close_or_cancel(self) -> None:
        if self._running:
            self._cancel.set()
            self._say("Stopping…", "accent_warning")
        else:
            self.accept() if self.finished_ok else self.reject()

    def reject(self) -> None:
        if self._running:
            self._close_or_cancel()  # Esc and the window's ✕ ask it to stop, not vanish
            return
        super().reject()


class BackupDialog(_WorkDialog):
    TITLE = "Back up"
    START = "Back up"

    def _build_form(self) -> None:
        self._methods = methods_for(self._conn.db_type)
        self._method_combo = QComboBox()
        for method in self._methods:
            self._method_combo.addItem(method.label, method.key)
        self._method_combo.currentIndexChanged.connect(self._method_changed)
        self._form.addWidget(self._label("How"))
        self._form.addWidget(self._method_combo)
        self._summary = self._note()
        self._form.addWidget(self._summary)
        self._hint = self._note(install_hint(self._conn.db_type), "text_info_body")
        self._hint.setVisible(bool(self._hint.text()))
        self._form.addWidget(self._hint)

        self._form.addWidget(self._label("Save to"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self._path = QLineEdit()
        row.addWidget(self._path, stretch=1)
        self._browse = QPushButton("Browse…")
        self._browse.setFixedHeight(30)
        self._browse.clicked.connect(self._pick_file)
        row.addWidget(self._browse)
        self._form.addLayout(row)
        self._gzip = QCheckBox("Compress (.gz)")
        self._gzip.toggled.connect(self._gzip_toggled)
        self._path.textChanged.connect(self._path_changed)
        self._form.addWidget(self._gzip)

        if self._methods:
            self._path.setText(
                str(Path.home() / default_filename(self._conn.name, self._methods[0]))
            )
            self._method_changed()
        else:
            self._summary.setText("This kind of database can't be backed up here.")

    def _method(self):
        index = self._method_combo.currentIndex()
        return self._methods[index] if 0 <= index < len(self._methods) else None

    def _method_changed(self) -> None:
        method = self._method()
        if method is None:
            return
        self._summary.setText(method.summary)
        self._gzip.setVisible(method.compressible)
        if not method.compressible:
            self._gzip.setChecked(False)

    def _gzip_toggled(self, on: bool) -> None:
        text = self._path.text().strip()
        if on and text and not text.lower().endswith((".gz", ".dump", ".backup")):
            self._path.setText(text + ".gz")
        elif not on and text.lower().endswith(".gz"):
            self._path.setText(text[:-3])

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save backup as",
            self._path.text(),
            "Backups (*.sql *.sql.gz *.dump *.db);;All files (*)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self._path.setText(path)

    def _path_changed(self, text: str) -> None:
        # The name decides whether the file is compressed; the tick shows it.
        self._gzip.blockSignals(True)
        self._gzip.setChecked(text.strip().lower().endswith(".gz"))
        self._gzip.blockSignals(False)

    def _inputs(self) -> list[QWidget]:
        return [self._method_combo, self._path, self._browse, self._gzip]

    def _ready(self) -> None:
        self._start_btn.setEnabled(bool(self._methods))
        if self.finished_ok:
            self._start_btn.setText("Back up again")

    def _confirm_overwrite(self, path: Path) -> bool:
        answer = QMessageBox.question(
            self,
            "Replace file?",
            f"{path.name} already exists in {path.parent}.\n\nReplace it with this backup?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _before(self) -> bool:
        text = self._path.text().strip()
        if not text:
            self._say("Choose where to save the backup.", "accent_error")
            return False
        path = Path(text).expanduser()
        if path.is_dir():
            self._say("That is a folder. Add a file name.", "accent_error")
            return False
        if path.exists() and not self._confirm_overwrite(path):
            return False
        self._destination = path
        self._chosen = self._method().key
        return True

    def _work(self):
        return run_backup(
            self._source,
            self._destination,
            method=self._chosen,
            progress=self._progress.emit,
            cancel=self._cancel,
        )

    def _done(self, result) -> str:
        parts = []
        if result.tables is not None:
            parts.append(f"{result.tables:,} table{'' if result.tables == 1 else 's'}")
        if result.rows is not None:
            parts.append(f"{result.rows:,} row{'' if result.rows == 1 else 's'}")
        parts.append(human_size(result.size))
        text = f"Backup saved: {result.path}\n{', '.join(parts)} in {_duration(result.seconds)}."
        for warning in result.warnings:
            text += f"\nWarning: {warning}"
        return text


class RestoreDialog(_WorkDialog):
    TITLE = "Restore into"
    START = "Restore"

    def _build_form(self) -> None:
        self._plan = None
        self._form.addWidget(self._label("Backup file"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self._path = QLineEdit()
        self._path.setPlaceholderText("A file made by Back up, pg_dump or mysqldump")
        self._path.textChanged.connect(self._file_changed)
        row.addWidget(self._path, stretch=1)
        self._browse = QPushButton("Browse…")
        self._browse.setFixedHeight(30)
        self._browse.clicked.connect(self._pick_file)
        row.addWidget(self._browse)
        self._form.addLayout(row)
        self._what = self._note("", "text_secondary")
        self._form.addWidget(self._what)
        self._how = self._note()
        self._form.addWidget(self._how)

        self._read_only_note = self._note(
            "This connection is read-only, so nothing can be loaded into it. Edit the "
            "connection (the pencil in the Database panel) and untick Read-only first.",
            "accent_warning",
        )
        self._read_only_note.setVisible(self._conn.read_only)
        self._form.addWidget(self._read_only_note)

        self._agree = QCheckBox(f"Load this backup into {self._source.where or self._conn.name}")
        self._agree.setToolTip(
            "Nothing is dropped first: the load stops if the database already has "
            "tables with the same names."
        )
        self._agree.toggled.connect(lambda _on: self._ready())
        self._form.addWidget(self._agree)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a backup",
            self._path.text() or str(Path.home()),
            "Backups (*.sql *.sql.gz *.dump *.backup *.db *.sqlite *.sqlite3);;All files (*)",
        )
        if path:
            self._path.setText(path)

    def _file_changed(self) -> None:
        self._plan = None
        self._what.setText("")
        self._how.setText("")
        self._how.setStyleSheet(f"color: {tc.get('text_muted')};")
        text = self._path.text().strip()
        if text:
            try:
                file = inspect_file(text)
            except BackupError as exc:
                self._how.setText(str(exc))
                self._how.setStyleSheet(f"color: {tc.get('accent_error')};")
            else:
                plan = plan_restore(file, self._conn.db_type)
                self._what.setText(f"{file.description} ({human_size(file.size)})")
                if plan.how:
                    self._plan = plan
                    self._how.setText(plan.summary)
                else:
                    self._how.setText(plan.problem)
                    self._how.setStyleSheet(f"color: {tc.get('accent_error')};")
        self._ready()

    def _inputs(self) -> list[QWidget]:
        return [self._path, self._browse, self._agree]

    def _ready(self) -> None:
        self._start_btn.setEnabled(
            self._plan is not None
            and not self._conn.read_only
            and self._agree.isChecked()
            and not self.finished_ok
        )

    def _before(self) -> bool:
        self._file = self._path.text().strip()
        return self._plan is not None and not self._conn.read_only

    async def _prepare(self) -> None:
        # The panel's own handle on a SQLite file would see it replaced
        # underneath it; closed here, reopened by the panel afterwards.
        if self._conn.db_type == "sqlite" and self._conn.is_connected:
            await self._conn.disconnect()

    def _work(self):
        return run_restore(
            self._source, self._file, progress=self._progress.emit, cancel=self._cancel
        )

    def _done(self, result) -> str:
        text = f"Restored from {result.path.name} in {_duration(result.seconds)}."
        if result.statements is not None:
            text += f" {result.statements:,} statements run."
        if result.saved_previous is not None:
            text += f"\nThe database as it was before is saved as {result.saved_previous}"
        return text
