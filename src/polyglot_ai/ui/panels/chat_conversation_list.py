"""Conversation sidebar actions — rename, delete, pin, export, search, context menu.

Extracted from ``chat_panel.py``. These are the right-click-menu actions
and the search-filter behaviour of the conversation list sidebar. They
all operate on the panel's ``_conv_list`` widget and its ``_db`` — the
panel owns the widget and the DB; this module owns the actions.

Lifecycle methods (populate, load, create-new, clear) are *not* here —
they're too entangled with the panel's message-rendering state
(`_message_layout`, `_welcome`, `_persisted_message_count`, etc.) to
extract cleanly without a bigger refactor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
)

from polyglot_ai.ui import theme_colors as tc

if TYPE_CHECKING:
    from polyglot_ai.ui.panels.chat_panel import ChatPanel

logger = logging.getLogger(__name__)


def show_context_menu(panel: "ChatPanel", position) -> None:
    """Right-click menu for a conversation row.

    Hooked up to ``_conv_list.customContextMenuRequested``. Does
    nothing if the click isn't over an item.
    """
    item = panel._conv_list.itemAt(position)
    if not item:
        return

    conv_id = item.data(Qt.ItemDataRole.UserRole)
    menu = QMenu(panel)
    menu.setStyleSheet(f"""
        QMenu {{
            background-color: {tc.get("bg_surface_overlay")};
            border: 1px solid {tc.get("border_menu")};
            padding: 4px 0;
            color: {tc.get("text_primary")};
            font-size: {tc.FONT_MD}px;
        }}
        QMenu::item {{ padding: 4px 20px; }}
        QMenu::item:selected {{ background-color: {tc.get("bg_active")}; }}
        QMenu::separator {{
            height: 1px;
            background: {tc.get("border_menu")};
            margin: 4px 8px;
        }}
    """)

    rename_act = menu.addAction("Rename...")
    rename_act.triggered.connect(lambda: rename(panel, item, conv_id))

    pin_act = menu.addAction("Pin / Unpin")
    pin_act.triggered.connect(lambda: pin(panel, conv_id))

    menu.addSeparator()

    export_act = menu.addAction("Export as text...")
    export_act.triggered.connect(lambda: export(panel, conv_id))

    menu.addSeparator()

    delete_act = menu.addAction("Delete")
    delete_act.triggered.connect(lambda: delete(panel, item, conv_id))

    menu.exec(panel._conv_list.viewport().mapToGlobal(position))


def rename(panel: "ChatPanel", item: QListWidgetItem, conv_id: int) -> None:
    """Prompt for a new title and persist it."""
    new_name, ok = QInputDialog.getText(panel, "Rename Conversation", "New name:", text=item.text())
    if ok and new_name:
        item.setText(new_name)
        if panel._db:
            from polyglot_ai.core.async_utils import safe_task

            safe_task(panel._db.rename_conversation(conv_id, new_name), name="db_rename")


def delete(panel: "ChatPanel", item: QListWidgetItem, conv_id: int) -> None:
    """Confirm and delete. If the deleted conversation is the active
    one, start a fresh conversation so the panel isn't left showing
    orphaned messages.

    Uses an explicit ``QMessageBox`` instance (not ``QMessageBox.question``)
    so we can: (a) give it enough vertical room that the buttons aren't
    clipped by the window-manager's default compact sizing, (b) default
    to ``No`` so an accidental Enter doesn't delete, and (c) truncate the
    conversation title to keep the message a sane single line.
    """
    # Keep the title readable in the dialog even for long titles
    display_title = item.text()
    if len(display_title) > 50:
        display_title = display_title[:47] + "…"

    box = QMessageBox(panel)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Delete Conversation")
    box.setText(f"Delete '{display_title}'?")
    box.setInformativeText("This cannot be undone.")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    # Give the dialog enough room that no theme / window manager clips
    # the button row. 420x160 is comfortable for two-line content plus
    # the standard button row.
    box.setMinimumWidth(420)

    if box.exec() == QMessageBox.StandardButton.Yes:
        row = panel._conv_list.row(item)
        panel._conv_list.takeItem(row)
        if panel._current_conversation and panel._current_conversation.id == conv_id:
            panel._new_conversation()
        if panel._db:
            from polyglot_ai.core.async_utils import safe_task

            safe_task(panel._db.delete_conversation(conv_id), name="db_delete")


def pin(panel: "ChatPanel", conv_id: int) -> None:
    """Toggle the pinned state of a conversation in the DB.

    The list is re-rendered by whichever UI path triggered the change
    (category filter, next populate call) — we don't force a refresh
    here to avoid fighting an in-flight list update.
    """
    if panel._db:
        from polyglot_ai.core.async_utils import safe_task

        safe_task(panel._db.pin_conversation(conv_id), name="db_pin")


def export(panel: "ChatPanel", conv_id: int) -> None:
    """Write the conversation's messages to a text file chosen via dialog."""
    path, _ = QFileDialog.getSaveFileName(
        panel, "Export Conversation", "conversation.txt", "Text Files (*.txt)"
    )
    if not path:
        return

    async def _do_export():
        if not panel._db:
            return
        messages = await panel._db.get_messages(conv_id)
        lines = []
        for msg in messages:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            lines.append(f"[{role}]\n{content}\n")
        text = "\n".join(lines)
        from polyglot_ai.core.async_utils import run_blocking

        await run_blocking(Path(path).write_text, text, "utf-8")

    from polyglot_ai.core.async_utils import safe_task

    safe_task(_do_export(), name="export_conversation")


def filter_by_search(conv_list: QListWidget, query: str) -> None:
    """Hide rows that don't match ``query`` (case-insensitive substring)."""
    q = query.lower().strip()
    for i in range(conv_list.count()):
        item = conv_list.item(i)
        if item:
            item.setHidden(bool(q) and q not in item.text().lower())
