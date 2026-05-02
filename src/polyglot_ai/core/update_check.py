"""Background check against the GitHub releases API.

Designed to be cheap and respectful:

- Runs at most **once per 24 hours** per install (cached in
  ``~/.config/polyglot-ai/update_check.json``).
- Single anonymous HTTPS GET to ``api.github.com``; no auth, no
  payload.
- Hard 5-second timeout so a flaky network can't stall the GUI.
- Compares semver-ish strings; ignores tags that don't parse.
- Returns a structured result the UI can render however it likes
  (we never pop a modal from in here).

The settings page or the help menu can call :func:`check_for_update`
from a worker thread; the call returns synchronously with a
result, ``None`` for "no check this run", or raises on programmer
error.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from polyglot_ai.constants import DATA_DIR

logger = logging.getLogger(__name__)


# Public so tests / callers can override (e.g. point at a fork).
RELEASES_URL = "https://api.github.com/repos/sabiut/polyglot-ai/releases/latest"

# How long between successive checks. 24h matches the cadence most
# desktop apps use; users who *want* to check more often have the
# explicit "Check for updates" menu action which bypasses the cache.
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

_CACHE_PATH = DATA_DIR / "update_check.json"


@dataclass(frozen=True)
class UpdateInfo:
    """A newer release the user can download.

    ``current_version`` and ``latest_version`` are passed back so
    the UI can render the upgrade prompt without re-importing
    ``polyglot_ai.__version__``.
    """

    current_version: str
    latest_version: str
    release_url: str
    published_at: str  # ISO 8601 from the GitHub API


def check_for_update(*, current_version: str, force: bool = False) -> UpdateInfo | None:
    """Return ``UpdateInfo`` when a newer release is available.

    Returns ``None`` when:
    - We're already up to date.
    - The 24-hour cache says we checked recently (skip unless
      ``force=True``).
    - The HTTP call failed (logged at debug level — a transient
      network blip shouldn't burn a warning every launch).
    """
    if not force and _checked_recently():
        return None

    try:
        latest_tag, release_url, published_at = _fetch_latest()
    except Exception as exc:
        logger.debug("update_check: fetch failed: %s", exc)
        return None

    _save_cache_timestamp()

    if not _is_newer(latest_tag, current_version):
        return None
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_tag,
        release_url=release_url,
        published_at=published_at,
    )


# ── Internals ──────────────────────────────────────────────────────


def _fetch_latest() -> tuple[str, str, str]:
    """Hit the GitHub API and return ``(tag, html_url, published_at)``."""
    req = urllib.request.Request(
        RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "polyglot-ai-update-check",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "").lstrip("v")
    url = str(data.get("html_url") or "")
    published = str(data.get("published_at") or "")
    if not tag:
        raise ValueError("releases API returned no tag_name")
    return tag, url, published


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """Loose semver parse — returns ``None`` on garbage so we skip
    rather than crash on pre-release tags or oddball formats."""
    m = _VERSION_RE.match(text.strip().lstrip("v"))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` parses to a strictly higher tuple than ``current``."""
    a = _parse_version(latest)
    b = _parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def _checked_recently() -> bool:
    try:
        mtime = float(json.loads(_CACHE_PATH.read_text(encoding="utf-8")).get("mtime", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (time.time() - mtime) < _CHECK_INTERVAL_SECONDS


def _save_cache_timestamp() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps({"mtime": time.time()}), encoding="utf-8")
    except OSError:
        # Read-only filesystem (Flatpak, immutable distro) — skip
        # cache, will re-check next launch. Not a bug.
        logger.debug("update_check: couldn't persist cache timestamp", exc_info=True)


def is_updates_path(path: Path) -> bool:
    """Test helper — exposed for tests that want to clear the cache."""
    return path == _CACHE_PATH
