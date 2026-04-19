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
    dlg.setStyleSheet("QDialog { background: #1e1e1e; }")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(10)

    lbl = QLabel("Branch name:")
    lbl.setStyleSheet("color: #ccc; font-size: 12px; font-weight: 600; background: transparent;")
    layout.addWidget(lbl)

    field = QLineEdit("feat/")
    field.setStyleSheet(
        "QLineEdit { background: #252526; color: #e0e0e0; border: 1px solid #333; "
        "border-radius: 4px; padding: 7px 10px; font-size: 13px; }"
        "QLineEdit:focus { border-color: #0e639c; }"
    )
    layout.addWidget(field)

    hint = QLabel("Will run `git checkout -b <name>` in the project root.")
    hint.setStyleSheet("color: #777; font-size: 11px; background: transparent;")
    layout.addWidget(hint)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch()

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setStyleSheet(
        "QPushButton { background: #3c3c3c; color: #ddd; border: 1px solid #555; "
        "border-radius: 4px; padding: 6px 14px; font-size: 12px; }"
        "QPushButton:hover { background: #4a4a4a; }"
    )
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel_btn)

    create_btn = QPushButton("Create")
    create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    create_btn.setDefault(True)
    create_btn.setStyleSheet(
        "QPushButton { background: #0e639c; color: white; border: none; "
        "border-radius: 4px; padding: 6px 16px; font-size: 12px; font-weight: 600; }"
        "QPushButton:hover { background: #1a8ae8; }"
        "QPushButton:disabled { background: #355; color: #888; }"
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
    dlg.setStyleSheet("QDialog { background: #1e1e1e; }")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 18, 20, 14)
    layout.setSpacing(12)

    icon_map = {"info": "ℹ", "warn": "⚠", "error": "✕"}
    colour_map = {"info": "#4ec9b0", "warn": "#e5a00d", "error": "#f48771"}
    icon_char = icon_map.get(kind, "ℹ")
    icon_colour = colour_map.get(kind, "#4ec9b0")

    header = QHBoxLayout()
    header.setSpacing(10)
    icon_lbl = QLabel(icon_char)
    icon_lbl.setStyleSheet(
        f"color: {icon_colour}; font-size: 20px; font-weight: bold; background: transparent;"
    )
    icon_lbl.setFixedWidth(28)
    header.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignTop)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        "color: #e0e0e0; font-size: 14px; font-weight: bold; background: transparent;"
    )
    header.addWidget(title_lbl, stretch=1)
    layout.addLayout(header)

    body = QLabel(message)
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    body.setStyleSheet(
        "color: #c0c0c0; font-size: 12px; background: transparent; padding-left: 38px;"
    )
    layout.addWidget(body)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("OK")
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setDefault(True)
    ok_btn.setStyleSheet(
        "QPushButton { background: #0e639c; color: white; border: none; "
        "border-radius: 4px; padding: 6px 22px; font-size: 12px; font-weight: 600; }"
        "QPushButton:hover { background: #1a8ae8; }"
    )
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)
    layout.addLayout(btn_row)

    dlg.exec()
