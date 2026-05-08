"""Platform integration — desktop files, icons, Wayland app ID."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# Hicolor sizes we ship in ``resources/icons``. Installing every
# one means launchers picking the size that matches their context
# (16 px file picker, 24 px tray, 48 px menu, 256 px ALT-Tab on
# HiDPI) get a sharp render rather than a downscaled 256 → blurry.
_ICON_SIZES = (16, 32, 48, 128, 256, 512)


def _resolve_desktop_source() -> Path | None:
    """Locate ``polyglot-ai.desktop`` regardless of how we were installed.

    Three candidate locations are tried, in order:

    1. ``packaging/debian/polyglot-ai.desktop`` relative to this
       file — the dev / source-checkout layout.
    2. The same filename inside the package data dir
       (``resources/desktop/``) — the wheel/.deb/.rpm layout.
    3. ``$APPDIR/polyglot-ai.desktop`` — the AppImage layout, where
       AppRun sets ``APPDIR`` before launching us.

    Returns ``None`` if none of them exist; the caller then writes
    a synthetic minimal entry so first-launch icon registration
    isn't gated on us shipping a perfect packaging tree.
    """
    here = Path(__file__).parent

    # 1) source checkout
    src_repo = here.parent.parent.parent / "packaging" / "debian" / "polyglot-ai.desktop"
    if src_repo.is_file():
        return src_repo

    # 2) bundled inside the wheel (we don't currently ship one
    #    here, but if someone moves it the lookup keeps working)
    bundled = here.parent / "resources" / "desktop" / "polyglot-ai.desktop"
    if bundled.is_file():
        return bundled

    # 3) AppImage runtime — AppRun exports APPDIR
    appdir = os.environ.get("APPDIR")
    if appdir:
        appimage_desktop = Path(appdir) / "polyglot-ai.desktop"
        if appimage_desktop.is_file():
            return appimage_desktop

    return None


def _resolve_exec_path() -> str:
    """Pick the right ``Exec=`` value for the .desktop entry.

    AppImage launches use the ``APPIMAGE`` env var (set by
    appimagetool's AppRun helper) — running the inner Python
    directly bypasses the AppImage's mount and breaks every
    relative path inside it. Fall back to ``polyglot-ai`` on PATH
    (wheel installs), then to the running interpreter as a last
    resort so we always have a runnable command.
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        return appimage
    on_path = shutil.which("polyglot-ai")
    if on_path:
        return on_path
    return sys.executable


def _install_icons(icons_dir: Path, hicolor_root: Path) -> int:
    """Copy every ``polyglot-ai-<N>.png`` into the user's hicolor tree.

    Returns the number of sizes installed. The 256 px copy without
    a size suffix (``polyglot-ai.png``) is also copied to keep
    backward compat with anything reading the unversioned name.
    """
    installed = 0
    for size in _ICON_SIZES:
        src = icons_dir / f"polyglot-ai-{size}.png"
        if not src.is_file():
            continue
        dst = hicolor_root / f"{size}x{size}" / "apps" / "polyglot-ai.png"
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            installed += 1
        except OSError as e:
            logger.warning("Could not install %dx%d icon: %s", size, size, e)
    # Also install the SVG for vector-aware themes.
    svg_src = icons_dir / "polyglot-ai.svg"
    if svg_src.is_file():
        svg_dst = hicolor_root / "scalable" / "apps" / "polyglot-ai.svg"
        try:
            svg_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(svg_src, svg_dst)
        except OSError:
            pass
    return installed


def setup_platform() -> Path | None:
    """Install desktop files and icons for Wayland/GNOME integration.

    Idempotent — safe to call on every startup. Writes to the user's
    ``~/.local/share`` so it works without root, and on AppImage and
    pip-from-source installs alike. System packages (.deb/.rpm)
    install their own files into ``/usr/share`` and don't need this
    pass, but running it does no harm: copies into ``~/.local/share``
    just shadow the system ones for the calling user with identical
    content.

    Returns the icon source path (for ``QApplication.setWindowIcon``),
    or ``None`` if no icon is bundled in this build.
    """
    icons_dir = Path(__file__).parent.parent / "resources" / "icons"
    primary_icon = icons_dir / "polyglot-ai.png"

    hicolor_root = Path.home() / ".local" / "share" / "icons" / "hicolor"
    apps_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dst = apps_dir / "polyglot-ai.desktop"

    # 1. Icons — every available size into the hicolor tree.
    sizes_installed = _install_icons(icons_dir, hicolor_root)
    if sizes_installed:
        logger.info("setup_platform: installed %d icon size(s)", sizes_installed)

    # 2. Desktop entry — locate the template, swap Exec= for the
    #    real launch command on this install path, write to user's
    #    applications dir.
    desktop_src = _resolve_desktop_source()
    if desktop_src is not None:
        try:
            apps_dir.mkdir(parents=True, exist_ok=True)
            real_exec = _resolve_exec_path()
            content = desktop_src.read_text()
            content = content.replace("Exec=polyglot-ai", f"Exec={real_exec}")
            desktop_dst.write_text(content)
            logger.info("setup_platform: wrote %s (Exec=%s)", desktop_dst, real_exec)
        except OSError as e:
            logger.warning("setup_platform: could not write desktop entry: %s", e)

    # 3. Refresh caches so the new entry / icons appear in menus
    #    immediately, not after the next session restart. Both
    #    tools are best-effort — missing them just delays UI
    #    refresh, never breaks anything.
    for cmd in (
        ["update-desktop-database", str(apps_dir)],
        ["gtk-update-icon-cache", "-f", "-t", str(hicolor_root)],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except FileNotFoundError:
            pass
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug("setup_platform: %s failed: %s", cmd[0], e)

    # 4. Set Wayland app_id before QApplication so the compositor
    #    binds the running window to the .desktop file we just
    #    installed.
    os.environ.setdefault("QT_WAYLAND_APP_ID", "polyglot-ai")

    return primary_icon if primary_icon.exists() else None
