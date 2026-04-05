"""Platform integration — desktop files, icons, Wayland app ID."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_platform() -> Path | None:
    """Install desktop files and icons for Wayland/GNOME integration.

    Returns the icon source path (for QApplication.setWindowIcon),
    or None if not found.
    """
    desktop_src = (
        Path(__file__).parent.parent.parent.parent / "packaging" / "debian" / "polyglot-ai.desktop"
    )
    desktop_dst = Path.home() / ".local" / "share" / "applications" / "polyglot-ai.desktop"
    icon_src = Path(__file__).parent.parent / "resources" / "icons" / "polyglot-ai.png"
    icon_dst = (
        Path.home()
        / ".local"
        / "share"
        / "icons"
        / "hicolor"
        / "256x256"
        / "apps"
        / "polyglot-ai.png"
    )

    if icon_src.exists():
        icon_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_src, icon_dst)

    if desktop_src.exists():
        desktop_dst.parent.mkdir(parents=True, exist_ok=True)
        real_exec = shutil.which("polyglot-ai") or sys.executable
        desktop_content = desktop_src.read_text()
        desktop_content = desktop_content.replace("Exec=polyglot-ai", f"Exec={real_exec}")
        desktop_dst.write_text(desktop_content)
        for cmd in [
            ["update-desktop-database", str(desktop_dst.parent)],
            [
                "gtk-update-icon-cache",
                "-f",
                "-t",
                str(Path.home() / ".local" / "share" / "icons" / "hicolor"),
            ],
        ]:
            try:
                subprocess.run(cmd, capture_output=True, timeout=5)
            except FileNotFoundError:
                pass

    # Set Wayland app_id before QApplication so the compositor picks it up
    os.environ.setdefault("QT_WAYLAND_APP_ID", "polyglot-ai")

    return icon_src if icon_src.exists() else None
