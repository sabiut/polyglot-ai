"""Attachment state + preview bar for the chat panel.

Extracted from ``chat_panel.py``. Owns the list of pending attachments
and the preview chip bar. The panel is responsible for:

* providing the container widget (``attach_bar``) and its layout
* routing drag-drop / paste / file-dialog events into the manager
* exposing :attr:`pending` during send so the outgoing message can
  capture the attachments

Everything else — copying files into the attachment directory, assigning
unique filenames, rendering chips, wiring the per-chip remove button —
lives here. The panel no longer carries any attachment-specific state.
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

#: Where copied attachments live. Mirrors the original path from
#: ``chat_panel.py`` — keeping it identical means any on-disk files
#: from previous app versions are still found.
ATTACH_DIR = Path.home() / ".local" / "share" / "polyglot-ai" / "attachments"


class AttachmentManager:
    """Pending attachments + preview bar rendering.

    Lifecycle:

    1. Panel constructs the ``attach_bar`` ``QWidget`` and its
       ``QHBoxLayout`` (left empty; initially hidden).
    2. Panel creates ``AttachmentManager(attach_bar, attach_bar_layout)``.
    3. Panel routes ``ChatInput.file_dropped`` → :meth:`add_from_path`
       and ``ChatInput.image_pasted`` → :meth:`add_from_pixmap`.
    4. Panel's drop event calls :meth:`add_from_path` per URL.
    5. When sending, panel reads :attr:`pending` and, after dispatch,
       calls :meth:`clear` to reset UI state.
    """

    def __init__(self, attach_bar: QWidget, attach_bar_layout: QHBoxLayout) -> None:
        self._attach_bar = attach_bar
        self._attach_bar_layout = attach_bar_layout
        self._pending: list[dict] = []

    # ── Public state access ─────────────────────────────────────────

    @property
    def pending(self) -> list[dict]:
        """Return the live list of pending attachments.

        Mutating this list is allowed — mainly so the send flow can
        copy it into the outgoing ``Message``. After consuming,
        call :meth:`clear` to reset both the list and the preview bar.
        """
        return self._pending

    def has_pending(self) -> bool:
        return bool(self._pending)

    # ── Mutators ────────────────────────────────────────────────────

    def add_from_path(self, file_path: str) -> None:
        """Copy a file into the attachment dir and add it as pending."""
        p = Path(file_path)
        if not p.exists():
            return

        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "application/octet-stream"

        ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        dest = ATTACH_DIR / f"{uuid.uuid4().hex}_{p.name}"
        shutil.copy2(p, dest)

        self._pending.append(
            {
                "path": str(dest),
                "original": str(p),
                "filename": p.name,
                "mime_type": mime,
                "size": p.stat().st_size,
            }
        )
        self._refresh_bar()

    def add_from_pixmap(self, pixmap: QPixmap) -> None:
        """Save a pasted image into the attachment dir and add it as pending."""
        ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"pasted_{uuid.uuid4().hex[:8]}.png"
        dest = ATTACH_DIR / filename
        pixmap.save(str(dest), "PNG")

        self._pending.append(
            {
                "path": str(dest),
                "original": "clipboard",
                "filename": filename,
                "mime_type": "image/png",
                "size": dest.stat().st_size,
            }
        )
        self._refresh_bar()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._pending):
            self._pending.pop(index)
            self._refresh_bar()

    def clear(self) -> None:
        """Drop all pending attachments and hide the preview bar."""
        self._pending.clear()
        self._refresh_bar()

    # ── Rendering ───────────────────────────────────────────────────

    def _refresh_bar(self) -> None:
        """Rebuild the preview chips from the current pending list."""
        # Clear existing chips
        while self._attach_bar_layout.count():
            item = self._attach_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._pending:
            self._attach_bar.hide()
            return

        self._attach_bar.show()
        for i, attach in enumerate(self._pending):
            self._attach_bar_layout.addWidget(self._build_chip(i, attach))

        self._attach_bar_layout.addStretch()

    def _build_chip(self, index: int, attach: dict) -> QWidget:
        chip = QWidget()
        chip.setStyleSheet(
            f"background: {tc.get('bg_hover')}; border-radius: {tc.RADIUS_MD}px; padding: 2px;"
        )
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(8, 2, 4, 2)
        chip_layout.setSpacing(4)

        # Thumbnail for image attachments
        if attach["mime_type"].startswith("image/"):
            thumb = QLabel()
            pm = QPixmap(attach["path"]).scaled(
                24,
                24,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(pm)
            chip_layout.addWidget(thumb)

        name = QLabel(attach["filename"][:20])
        name.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        chip_layout.addWidget(name)

        size_kb = attach["size"] / 1024
        size_label = QLabel(f"({size_kb:.0f}KB)")
        size_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: 10px; background: transparent;"
        )
        chip_layout.addWidget(size_label)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(18, 18)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {tc.get('text_tertiary')}; "
            f"border: none; font-size: {tc.FONT_MD}px; }}"
            f"QPushButton:hover {{ color: #ff4444; }}"
        )
        # Capture index by default arg so the lambda binds to THIS chip,
        # not whatever ``index`` points to at the end of the loop.
        remove_btn.clicked.connect(lambda _checked=False, idx=index: self.remove(idx))
        chip_layout.addWidget(remove_btn)

        return chip
