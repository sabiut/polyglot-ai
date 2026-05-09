"""One-time migration from Codex Desktop to Polyglot AI.

Moves data directories and keyring entries from the old naming scheme
to the new one. Idempotent — safe to call on every startup.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate_legacy_data() -> None:
    """Migrate data from Codex Desktop (old name) to Polyglot AI (new name).

    Performs four migrations:
    1. Data directory: ~/.local/share/codex-desktop → ~/.local/share/polyglot-ai
    2. Config directory: ~/.config/codex-desktop → ~/.config/polyglot-ai
    3. Keyring entries: service "codex-desktop" → "polyglot-ai"
    4. Remove the orphaned ``claude-web-profile`` directory left
       behind when the embedded ``claude.ai`` panel was removed.
       Used to live at ~/.local/share/polyglot-ai/claude-web-profile;
       can be tens of MB of Chromium cookies, IndexedDB, and cache
       no part of the app touches anymore.

    All operations are idempotent and exception-safe.
    Migration failures are logged but never block startup.
    """
    try:
        _migrate_directory(
            Path.home() / ".local" / "share" / "codex-desktop",
            Path.home() / ".local" / "share" / "polyglot-ai",
            "data",
        )
        _migrate_directory(
            Path.home() / ".config" / "codex-desktop",
            Path.home() / ".config" / "polyglot-ai",
            "config",
        )
        _migrate_keyring()
        _remove_orphaned_claude_web_profile()
    except Exception:
        logger.exception("Migration failed (non-fatal, continuing startup)")


def _remove_orphaned_claude_web_profile() -> None:
    """Delete the leftover Chromium profile from the removed web view.

    The embedded ``claude.ai`` panel was removed in favor of the
    native API path. Its persistent profile lived at
    ``~/.local/share/polyglot-ai/claude-web-profile`` and held
    cookies, IndexedDB, LevelDB, cache, and service-worker data —
    can easily be 30–80 MB of dead bytes. Idempotent: a no-op
    after the first successful removal.
    """
    profile_dir = Path.home() / ".local" / "share" / "polyglot-ai" / "claude-web-profile"
    if not profile_dir.exists():
        return
    try:
        # Compute size before deleting so the log line is informative.
        size_bytes = sum(p.stat().st_size for p in profile_dir.rglob("*") if p.is_file())
    except OSError:
        size_bytes = 0
    try:
        shutil.rmtree(profile_dir)
        logger.info(
            "Removed orphaned Chromium profile %s (%.1f MB freed)",
            profile_dir,
            size_bytes / 1_000_000,
        )
    except OSError as e:
        logger.warning("Could not remove orphaned profile %s: %s", profile_dir, e)


def _migrate_directory(old: Path, new: Path, label: str) -> None:
    """Move a directory from old path to new path if applicable."""
    if not old.exists():
        return  # Nothing to migrate
    if new.exists():
        logger.info("Both old and new %s dirs exist; skipping directory migration", label)
        return

    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        logger.info("Migrated %s directory: %s → %s", label, old, new)
    except OSError:
        # rename() fails across filesystems; fall back to copy + remove
        try:
            shutil.copytree(old, new)
            shutil.rmtree(old)
            logger.info("Migrated %s directory (copy): %s → %s", label, old, new)
        except Exception:
            logger.exception("Failed to migrate %s directory: %s → %s", label, old, new)


def _migrate_keyring() -> None:
    """Copy keyring entries from old service name to new service name."""
    try:
        import keyring
    except ImportError:
        return

    OLD_SERVICE = "codex-desktop"
    NEW_SERVICE = "polyglot-ai"
    PROVIDER_KEYS = ["openai", "anthropic", "google", "deepseek"]

    for key_name in PROVIDER_KEYS:
        try:
            old_value = keyring.get_password(OLD_SERVICE, key_name)
            if not old_value:
                continue

            # Only migrate if the new entry doesn't already exist
            existing = keyring.get_password(NEW_SERVICE, key_name)
            if existing:
                continue

            keyring.set_password(NEW_SERVICE, key_name, old_value)
            keyring.delete_password(OLD_SERVICE, key_name)
            logger.info(
                "Migrated keyring entry: %s/%s → %s/%s",
                OLD_SERVICE,
                key_name,
                NEW_SERVICE,
                key_name,
            )
        except Exception:
            logger.warning("Failed to migrate keyring entry: %s", key_name, exc_info=True)
