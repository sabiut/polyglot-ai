"""Changeset panel — review, approve, and rollback AI-proposed file changes.

Collects all pending file changes from the AI agent and presents them
in a unified review interface. Users can:
  - See all changed files in a list
  - View diffs per file
  - Apply or reject individual files
  - Apply all / reject all
  - Rollback previously applied changes
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


@dataclass
class FileChange:
    """A single proposed file change."""
    path: str                    # Relative to project root
    original: str = ""           # Original content (empty for new files)
    proposed: str = ""           # Proposed new content
    status: str = "pending"      # pending | applied | rejected | rolled_back
    backup_path: str | None = None  # Path to backup file


class ChangesetPanel(QWidget):
    """Panel for reviewing and managing AI-proposed file changes."""

    change_applied = pyqtSignal(str)    # file path
    change_rejected = pyqtSignal(str)   # file path
    change_rolledback = pyqtSignal(str) # file path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._changes: dict[str, FileChange] = {}
        self._project_root: Path | None = None

        self._build_ui()

    def set_project_root(self, root: str | Path) -> None:
        self._project_root = Path(root) if isinstance(root, str) else root

    @property
    def project_root(self) -> Path | None:
        """Current project root, or None if no project is open."""
        return self._project_root

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setFixedHeight(40)
        header.setStyleSheet(f"background-color: {tc.get('bg_base')}; border-bottom: 1px solid {tc.get('border_secondary')};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("PENDING CHANGES")
        title.setStyleSheet(f"font-size: {tc.FONT_SM}px; font-weight: bold; color: {tc.get('text_tertiary')}; letter-spacing: 1px;")
        header_layout.addWidget(title)

        self._count_label = QLabel("0 files")
        self._count_label.setStyleSheet(f"font-size: {tc.FONT_SM}px; color: #666;")
        header_layout.addWidget(self._count_label)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(f"font-size: 10px; color: {tc.get('accent_info')};")
        header_layout.addWidget(self._summary_label)

        header_layout.addStretch()

        # Review All navigation buttons
        self._prev_btn = QPushButton("◀ Prev")
        self._prev_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {tc.get('text_tertiary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 3px; padding: 2px 8px; font-size: 10px; }}"
            f"QPushButton:hover {{ color: {tc.get('text_heading')}; border-color: #666; }}"
        )
        self._prev_btn.clicked.connect(self._review_prev)
        self._prev_btn.hide()
        header_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("Next ▶")
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {tc.get('text_tertiary')}; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 3px; padding: 2px 8px; font-size: 10px; }}"
            f"QPushButton:hover {{ color: {tc.get('text_heading')}; border-color: #666; }}"
        )
        self._next_btn.clicked.connect(self._review_next)
        self._next_btn.hide()
        header_layout.addWidget(self._next_btn)

        # Apply all button
        self._apply_all_btn = QPushButton("✓ Apply All")
        self._apply_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {tc.get('accent_success')}; color: {tc.get('text_on_accent')}; font-weight: 600;
                padding: 4px 14px; border: none; border-radius: {tc.RADIUS_SM}px; font-size: {tc.FONT_SM}px;
            }}
            QPushButton:hover {{ background-color: {tc.get('accent_success_hover')}; }}
            QPushButton:disabled {{ background-color: {tc.get('border_secondary')}; color: #666; }}
        """)
        self._apply_all_btn.clicked.connect(self._apply_all)
        self._apply_all_btn.setEnabled(False)
        header_layout.addWidget(self._apply_all_btn)

        # Reject all button
        self._reject_all_btn = QPushButton("✗ Reject All")
        self._reject_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {tc.get('accent_error')};
                padding: 4px 14px; border: 1px solid {tc.get('border_input')}; border-radius: {tc.RADIUS_SM}px; font-size: {tc.FONT_SM}px;
            }}
            QPushButton:hover {{ background-color: {tc.get('bg_feedback_neg')}; }}
            QPushButton:disabled {{ background-color: transparent; color: {tc.get('border_card')}; border-color: {tc.get('border_secondary')}; }}
        """)
        self._reject_all_btn.clicked.connect(self._reject_all)
        self._reject_all_btn.setEnabled(False)
        header_layout.addWidget(self._reject_all_btn)

        layout.addWidget(header)

        # Splitter: file list | diff view
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{ background-color: {tc.get('border_secondary')}; width: 1px; }}
        """)

        # File list
        self._file_list = QListWidget()
        self._file_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {tc.get('bg_base')}; border: none; outline: none;
                font-size: {tc.FONT_MD}px;
            }}
            QListWidget::item {{
                padding: 8px 12px; color: {tc.get('text_primary')}; border-bottom: 1px solid #2a2a2a;
            }}
            QListWidget::item:selected {{
                background-color: {tc.get('bg_surface_overlay')}; color: {tc.get('text_on_accent')};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {tc.get('bg_surface')};
            }}
        """)
        self._file_list.currentRowChanged.connect(self._on_file_selected)
        splitter.addWidget(self._file_list)

        # Right side: diff + actions
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # File action bar
        action_bar = QWidget()
        action_bar.setFixedHeight(36)
        action_bar.setStyleSheet(f"background-color: {tc.get('bg_surface')}; border-bottom: 1px solid {tc.get('border_secondary')};")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(12, 0, 12, 0)

        self._file_path_label = QLabel("")
        self._file_path_label.setStyleSheet(f"font-size: {tc.FONT_MD}px; color: {tc.get('text_heading')}; font-weight: bold;")
        action_layout.addWidget(self._file_path_label)

        self._file_status_label = QLabel("")
        self._file_status_label.setStyleSheet(f"font-size: {tc.FONT_SM}px; color: {tc.get('text_tertiary')};")
        action_layout.addWidget(self._file_status_label)

        action_layout.addStretch()

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {tc.get('accent_success')}; color: {tc.get('text_on_accent')}; font-weight: 600;
                padding: 3px 12px; border: none; border-radius: {tc.RADIUS_SM}px; font-size: {tc.FONT_SM}px;
            }}
            QPushButton:hover {{ background-color: {tc.get('accent_success_hover')}; }}
        """)
        self._apply_btn.clicked.connect(self._apply_selected)
        action_layout.addWidget(self._apply_btn)

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {tc.get('accent_error')};
                padding: 3px 12px; border: 1px solid {tc.get('border_input')}; border-radius: {tc.RADIUS_SM}px; font-size: {tc.FONT_SM}px;
            }}
            QPushButton:hover {{ background-color: {tc.get('bg_feedback_neg')}; }}
        """)
        self._reject_btn.clicked.connect(self._reject_selected)
        action_layout.addWidget(self._reject_btn)

        self._rollback_btn = QPushButton("↩ Rollback")
        self._rollback_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {tc.get('accent_warning')};
                padding: 3px 12px; border: 1px solid {tc.get('border_input')}; border-radius: {tc.RADIUS_SM}px; font-size: {tc.FONT_SM}px;
            }}
            QPushButton:hover {{ background-color: #2a2a1a; }}
        """)
        self._rollback_btn.clicked.connect(self._rollback_selected)
        self._rollback_btn.setVisible(False)
        action_layout.addWidget(self._rollback_btn)

        right_layout.addWidget(action_bar)

        # Diff viewer
        self._diff_view = QTextEdit()
        self._diff_view.setReadOnly(True)
        self._diff_view.setFont(QFont("Consolas, Monaco, Courier New", 12))
        self._diff_view.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1a1a1a; color: {tc.get('text_primary')}; border: none;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: {tc.FONT_MD}px; line-height: 150%;
            }}
        """)
        right_layout.addWidget(self._diff_view)

        splitter.addWidget(right)
        splitter.setSizes([220, 580])

        layout.addWidget(splitter)

        # Welcome state
        self._show_empty_state()

    def _show_empty_state(self) -> None:
        self._diff_view.setHtml(
            f'<div style="color:#666; padding:40px; text-align:center; font-size:{tc.FONT_BASE}px;">'
            'No pending changes.<br><br>'
            'When the AI proposes file modifications, they will appear here<br>'
            'for review before being applied to your project.'
            '</div>'
        )

    # ── Public API ───────────────────────────────────────────────

    def add_change(self, path: str, original: str, proposed: str) -> None:
        """Add a proposed file change for review."""
        change = FileChange(path=path, original=original, proposed=proposed)
        self._changes[path] = change
        self._refresh_list()

    def update_change(self, path: str, proposed: str) -> None:
        """Update the proposed content of an existing tracked change.

        If the path is not already tracked, adds it as a new change
        (reading the original content from disk if the project root is set).
        """
        if path in self._changes:
            self._changes[path].proposed = proposed
            self._refresh_list()
        elif self._project_root:
            # New file change — read original from disk
            original = ""
            target = self._project_root / path
            if target.exists():
                try:
                    original = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            self.add_change(path, original, proposed)

    def is_tracked(self, path: str) -> bool:
        """Check if a file path is tracked in the changeset."""
        return path in self._changes

    def clear(self) -> None:
        """Remove all changes."""
        self._changes.clear()
        self._refresh_list()
        self._show_empty_state()

    @property
    def pending_count(self) -> int:
        return sum(1 for c in self._changes.values() if c.status == "pending")

    # ── Internal ─────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        self._file_list.clear()
        pending = 0
        for path, change in self._changes.items():
            status_icons = {
                "pending": "●",
                "applied": "✓",
                "rejected": "✗",
                "rolled_back": "↩",
            }
            status_colors = {
                "pending": tc.get("cs_pending"),
                "applied": tc.get("cs_applied"),
                "rejected": tc.get("cs_rejected"),
                "rolled_back": tc.get("cs_rolledback"),
            }
            icon = status_icons.get(change.status, "?")
            color = status_colors.get(change.status, tc.get("cs_rolledback"))

            item = QListWidgetItem(f"{icon}  {path}")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._file_list.addItem(item)

            if change.status == "pending":
                pending += 1

        # Summary stats
        total_added = 0
        total_removed = 0
        for change in self._changes.values():
            orig_lines = len(change.original.splitlines())
            prop_lines = len(change.proposed.splitlines())
            if prop_lines > orig_lines:
                total_added += prop_lines - orig_lines
            else:
                total_removed += orig_lines - prop_lines

        self._count_label.setText(f"{len(self._changes)} files · {pending} pending")
        if total_added or total_removed:
            self._summary_label.setText(f"+{total_added} / -{total_removed} lines")
        else:
            self._summary_label.setText("")
        self._apply_all_btn.setEnabled(pending > 0)
        self._reject_all_btn.setEnabled(pending > 0)
        self._update_nav_buttons()

    def _on_file_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self._file_list.item(row)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        change = self._changes.get(path)
        if not change:
            return

        self._file_path_label.setText(path)

        status_text = {
            "pending": "Pending review",
            "applied": "Applied ✓",
            "rejected": "Rejected",
            "rolled_back": "Rolled back",
        }
        self._file_status_label.setText(status_text.get(change.status, ""))

        # Show/hide buttons based on status
        is_pending = change.status == "pending"
        is_applied = change.status == "applied"
        self._apply_btn.setVisible(is_pending)
        self._reject_btn.setVisible(is_pending)
        self._rollback_btn.setVisible(is_applied)

        # Generate diff
        self._show_diff(change)

    def _show_diff(self, change: FileChange) -> None:
        """Show a colored unified diff."""
        original_lines = change.original.splitlines(keepends=True)
        proposed_lines = change.proposed.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines, proposed_lines,
            fromfile=f"a/{change.path}",
            tofile=f"b/{change.path}",
            lineterm="",
        )

        html_parts = [f'<pre style="font-family:monospace; font-size:{tc.FONT_MD}px; line-height:160%; margin:8px;">']

        for line in diff:
            line = line.rstrip("\n")
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            if line.startswith("+++") or line.startswith("---"):
                html_parts.append(f'<span style="color:{tc.get("diff_meta_fg")};">{escaped}</span>\n')
            elif line.startswith("@@"):
                html_parts.append(f'<span style="color:{tc.get("diff_hunk_fg")};">{escaped}</span>\n')
            elif line.startswith("+"):
                html_parts.append(
                    f'<span style="background:{tc.get("bg_diff_add")}; color:{tc.get("diff_add_fg")}; display:block; '
                    f'padding:0 4px;">{escaped}</span>'
                )
            elif line.startswith("-"):
                html_parts.append(
                    f'<span style="background:{tc.get("bg_diff_del")}; color:{tc.get("diff_del_fg")}; display:block; '
                    f'padding:0 4px;">{escaped}</span>'
                )
            else:
                html_parts.append(f'<span style="color:{tc.get("diff_meta_fg")};">{escaped}</span>\n')

        html_parts.append("</pre>")

        if len(html_parts) == 2:
            # No diff (new file)
            html_parts = [
                f'<pre style="font-family:monospace; font-size:{tc.FONT_MD}px; margin:8px;">',
                f'<span style="color:{tc.get("diff_add_fg")};">New file — full content shown below:</span>\n\n',
            ]
            escaped = change.proposed.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_parts.append(f'<span style="color:{tc.get("text_primary")};">{escaped}</span>')
            html_parts.append("</pre>")

        self._diff_view.setHtml("".join(html_parts))

    def _get_selected_path(self) -> str | None:
        item = self._file_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _apply_selected(self) -> None:
        path = self._get_selected_path()
        if not path:
            return
        change = self._changes.get(path)
        if not change or change.status != "pending":
            return

        if self._project_root:
            target = self._project_root / path
            # Backup using centralized backup location
            if target.exists():
                from polyglot_ai.core.ai.code_applier import _create_backup
                backup_path = _create_backup(target)
                if backup_path:
                    change.backup_path = str(backup_path)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.proposed, encoding="utf-8")
            logger.info("Applied change to %s", path)

        change.status = "applied"
        self._refresh_list()
        self._on_file_selected(self._file_list.currentRow())
        self.change_applied.emit(path)

    def _reject_selected(self) -> None:
        path = self._get_selected_path()
        if not path:
            return
        change = self._changes.get(path)
        if not change or change.status != "pending":
            return

        change.status = "rejected"
        self._refresh_list()
        self._on_file_selected(self._file_list.currentRow())
        self.change_rejected.emit(path)

    def _rollback_selected(self) -> None:
        path = self._get_selected_path()
        if not path:
            return
        change = self._changes.get(path)
        if not change or change.status != "applied":
            return

        if self._project_root and change.backup_path:
            target = self._project_root / path
            backup = Path(change.backup_path)
            if backup.exists():
                target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
                backup.unlink()
                logger.info("Rolled back %s", path)

        change.status = "rolled_back"
        self._refresh_list()
        self._on_file_selected(self._file_list.currentRow())
        self.change_rolledback.emit(path)

    def _apply_all(self) -> None:
        paths = [p for p, c in self._changes.items() if c.status == "pending"]
        for path in paths:
            self._file_list.setCurrentRow(
                next(i for i in range(self._file_list.count())
                     if self._file_list.item(i).data(Qt.ItemDataRole.UserRole) == path)
            )
            self._apply_selected()

    def _reject_all(self) -> None:
        for path, change in self._changes.items():
            if change.status == "pending":
                change.status = "rejected"
                self.change_rejected.emit(path)
        self._refresh_list()

    # ── Review All navigation ────────────────────────────────────

    def _review_prev(self) -> None:
        row = self._file_list.currentRow()
        if row > 0:
            self._file_list.setCurrentRow(row - 1)
        self._update_nav_buttons()

    def _review_next(self) -> None:
        row = self._file_list.currentRow()
        if row < self._file_list.count() - 1:
            self._file_list.setCurrentRow(row + 1)
        self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        count = self._file_list.count()
        show = count > 1
        self._prev_btn.setVisible(show)
        self._next_btn.setVisible(show)
        if show:
            row = self._file_list.currentRow()
            self._prev_btn.setEnabled(row > 0)
            self._next_btn.setEnabled(row < count - 1)
