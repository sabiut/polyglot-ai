"""Borderless icon-only button used in panel header toolbars.

Four panels (test, today, tasks, mcp_sidebar) each hand-rolled an
identical ``_icon_btn`` method — same QPushButton setup, same hover
overlay, differing only in an unused objectName string. One shared
factory instead.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QPushButton


def make_icon_button(icon: QIcon, tooltip: str, *, size: int = 22) -> QPushButton:
    """A transparent, borderless button for a single small icon.

    Used for header-row actions (refresh, new, pop-out, etc.) where the
    icon alone is the affordance — no visible button chrome until hover.
    """
    btn = QPushButton()
    btn.setIcon(icon)
    btn.setFixedSize(size, size)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton { background: transparent; border: none; }"
        "QPushButton:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
    )
    return btn
