"""Dark-themed dialog helpers for the git panel.

Extracted from ``git_panel.py``. These replace the native
``QInputDialog`` and ``QMessageBox`` variants which pick up the OS
theme (GNOME/KDE) and look glaringly out of place against the dark
IDE chrome. All three are module-level functions with no panel state.

* :func:`prompt_branch_name` — modal text-input dialog, returns the
  entered string (or empty string on cancel). Pre-fills ``"feat/"``
  which covers the common convention without forcing the user to
  delete anything.
* :func:`validate_branch_name` — pure-function check implementing a
  subset of ``git check-ref-format``. Returns a human-readable reason
  on failure, ``None`` when the name is acceptable.
* :func:`show_message` — dark-themed info/warn/error dialog.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc


def prompt_branch_name(parent: QWidget) -> str:
    """Show a custom-styled input dialog for a new branch name.

    Replaces the native QInputDialog which picks up the OS GTK theme
    (red/green emoji icons on some KDE/GNOME setups) and looks out of
    place against the dark IDE theme.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("New branch")
    dlg.setModal(True)
    dlg.setMinimumWidth(360)
    dlg.setStyleSheet(f"QDialog {{ background: {tc.get('bg_base')}; }}")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(10)

    lbl = QLabel("Branch name:")
    lbl.setStyleSheet(
        f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; "
        "font-weight: 600; background: transparent;"
    )
    layout.addWidget(lbl)

    field = QLineEdit("feat/")
    field.setStyleSheet(
        f"QLineEdit {{ background: {tc.get('bg_surface')}; color: {tc.get('text_heading')}; "
        f"border: 1px solid {tc.get('border_secondary')}; "
        f"border-radius: 4px; padding: 7px 10px; font-size: {tc.FONT_BASE}px; }}"
        f"QLineEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
    )
    layout.addWidget(field)

    hint = QLabel("Will run `git checkout -b <name>` in the project root.")
    hint.setStyleSheet(
        f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; background: transparent;"
    )
    layout.addWidget(hint)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch()

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setStyleSheet(
        f"QPushButton {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
        f"border: 1px solid {tc.get('border_input')}; "
        f"border-radius: 4px; padding: 6px 14px; font-size: {tc.FONT_MD}px; }}"
        f"QPushButton:hover {{ background: {tc.get('bg_hover')}; "
        f"border-color: {tc.get('accent_primary')}; }}"
    )
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel_btn)

    create_btn = QPushButton("Create")
    create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    create_btn.setDefault(True)
    create_btn.setStyleSheet(
        f"QPushButton {{ background: {tc.get('accent_primary')}; color: white; border: none; "
        f"border-radius: 4px; padding: 6px 16px; font-size: {tc.FONT_MD}px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        f"QPushButton:disabled {{ background: {tc.get('bg_hover')}; "
        f"color: {tc.get('text_tertiary')}; }}"
    )
    create_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(create_btn)

    layout.addLayout(btn_row)

    # Submit on Enter
    field.returnPressed.connect(dlg.accept)
    field.setFocus()
    field.selectAll()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    return field.text().strip()


def validate_branch_name(name: str) -> str | None:
    """Return None if ``name`` is a valid git ref, else a human-readable reason.

    Implements a subset of the rules from `git check-ref-format`:
    https://git-scm.com/docs/git-check-ref-format
    """
    if not name:
        return "Branch name cannot be empty."
    if " " in name:
        return (
            "Branch names cannot contain spaces. "
            "Use hyphens or slashes instead (e.g. feat/my-thing)."
        )
    if any(c in name for c in "~^:?*[\\"):
        return "Branch names cannot contain any of: ~ ^ : ? * [ \\"
    if name.startswith("-") or name.startswith("/") or name.startswith("."):
        return "Branch names cannot start with -, /, or ."
    if name.endswith("/") or name.endswith(".") or name.endswith(".lock"):
        return "Branch names cannot end with /, ., or .lock"
    if ".." in name or "@{" in name or "//" in name:
        return "Branch names cannot contain .., @{, or //"
    if name == "@":
        return "Branch name cannot be just '@'."
    return None


def show_message(
    parent: QWidget,
    title: str,
    message: str,
    kind: str = "info",
) -> None:
    """Show a dark-themed message dialog matching the rest of the IDE.

    Replaces QMessageBox.{information,warning,critical}, which pick up
    the OS native theme and look out of place against the dark UI.

    ``kind`` is one of ``"info" | "warn" | "error"``.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(380)
    dlg.setStyleSheet(f"QDialog {{ background: {tc.get('bg_base')}; }}")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 18, 20, 14)
    layout.setSpacing(12)

    icon_map = {"info": "ℹ", "warn": "⚠", "error": "✕"}
    colour_map = {
        "info": tc.get("accent_success_muted"),
        "warn": tc.get("accent_warning"),
        "error": tc.get("accent_error"),
    }
    icon_char = icon_map.get(kind, "ℹ")
    icon_colour = colour_map.get(kind, tc.get("accent_success_muted"))

    header = QHBoxLayout()
    header.setSpacing(10)
    icon_lbl = QLabel(icon_char)
    icon_lbl.setStyleSheet(
        f"color: {icon_colour}; font-size: {tc.FONT_2XL}px; font-weight: bold; background: transparent;"
    )
    icon_lbl.setFixedWidth(28)
    header.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignTop)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        f"color: {tc.get('text_heading')}; font-size: {tc.FONT_LG}px; "
        "font-weight: bold; background: transparent;"
    )
    header.addWidget(title_lbl, stretch=1)
    layout.addLayout(header)

    body = QLabel(message)
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    body.setStyleSheet(
        f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; "
        "background: transparent; padding-left: 38px;"
    )
    layout.addWidget(body)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("OK")
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setDefault(True)
    ok_btn.setStyleSheet(
        f"QPushButton {{ background: {tc.get('accent_primary')}; color: white; border: none; "
        f"border-radius: 4px; padding: 6px 22px; font-size: {tc.FONT_MD}px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
    )
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)
    layout.addLayout(btn_row)

    dlg.exec()
