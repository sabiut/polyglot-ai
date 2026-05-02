"""Single-instance lock helpers.

Lives in ``startup/`` rather than ``app.py`` so the unit tests can
exercise the lock-ownership / notification logic without dragging
the entire UI tree (MainWindow, every panel, every Qt widget) into
the import graph. The functions here are pure logic + a couple of
subprocess fallbacks; nothing that needs a running QApplication.

Two contracts:

* :func:`lock_owner_is_unrelated` decides whether a held lock
  belongs to *us* or to an unrelated process that happened to
  inherit the recorded PID. Used by ``app.main`` to recover from
  the PID-reuse trap (Linux recycles PIDs and Qt's stale check
  only verifies the PID is alive, not that it's still our app).

* :func:`notify_already_running` shows the "another instance is
  running" message through every channel we have access to —
  stderr + ``notify-send`` + QMessageBox + zenity / kdialog /
  xmessage. The redundancy is deliberate: a Wayland compositor
  with a misconfigured XDG portal can leave Qt modals hidden
  behind another window, so we never trust a single channel.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def lock_owner_is_unrelated(lock) -> bool:
    """Return True iff the live PID in the lock file isn't us.

    ``lock.getLockInfo()`` returns ``(ok, pid, hostname, appname)``.
    We trust the PID; the appname field is what *we* wrote, so any
    process — including one that recycled our PID — would carry it
    forward unmodified. The honest signal is ``/proc/<pid>/cmdline``
    (Linux) — if it doesn't mention our app name, the lock is stale.

    Fail closed: any unexpected exception returns False so the
    caller falls back to the normal "already running" path. That's
    safer than falsely clearing a legitimate lock.
    """
    try:
        ok, pid, hostname, _appname = lock.getLockInfo()
    except Exception:
        logger.debug("getLockInfo unavailable", exc_info=True)
        return False
    if not ok or pid <= 0:
        return False

    # Hostname mismatch (synced home dir between machines) — the
    # other host's PID is meaningless to us, treat as stale.
    try:
        if hostname and hostname != socket.gethostname():
            logger.info(
                "Lock recorded host=%s (we are %s) — treating as stale",
                hostname,
                socket.gethostname(),
            )
            return True
    except Exception:
        pass

    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.is_file():
        # PID isn't running, or no /proc (macOS / sandboxed
        # container). On Linux this means the PID is dead and the
        # lock is definitely stale; on platforms without /proc we
        # can't tell, so play safe.
        if sys.platform == "linux":
            return True
        return False
    try:
        cmdline = cmdline_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # cmdline is NUL-separated; substring search is fine. Match
    # either the entry-point script name or the module path.
    return "polyglot-ai" not in cmdline and "polyglot_ai" not in cmdline


def notify_already_running(app, lock_path: str) -> None:
    """Tell the user the app is already running through every channel.

    Multi-channel because any single output can fail silently:

    * ``stderr`` — terminal users see it; log aggregation captures it.
    * ``notify-send`` — desktop notification daemon; survives a
      hidden Qt modal.
    * QMessageBox — best UX when it works; most fragile on Wayland.
    * Pre-flight's zenity / kdialog / xmessage chain — final fallback
      for environments where Qt itself isn't ready.
    """
    text = (
        "Polyglot AI is already running.\n\n"
        "Look for an existing window — it may be minimised or on "
        "another desktop. If you're sure no other instance exists, "
        f"delete the lock file:\n  {lock_path}"
    )
    # 1. stderr — unconditional, can't be hidden by a window
    #    manager bug.
    print("Polyglot AI: " + text, file=sys.stderr)

    # 2. notify-send — desktop notification, survives a hidden modal.
    _try_desktop_notification(text)

    # 3. QMessageBox — the user-friendly path when it works. Only
    #    attempt this when we have a live QApplication: the Qt
    #    modal subsystem segfaults if you call QMessageBox.information
    #    without one, which would mean the lock-collision path
    #    crashes the whole interpreter instead of exiting cleanly.
    if app is not None:
        try:
            from PyQt6.QtWidgets import QMessageBox

            try:
                platform_name = app.platformName().lower()
            except Exception:
                platform_name = ""
            if platform_name == "wayland":
                logger.info("On Wayland; QMessageBox may not surface above other windows")
            QMessageBox.information(None, "Polyglot AI", text)
        except Exception:
            logger.debug("QMessageBox unavailable", exc_info=True)

    # 4. Pre-flight's graphical fallbacks (only useful when no
    #    QApplication is alive — cheap insurance).
    try:
        from polyglot_ai.startup.preflight import _kdialog_cmd, _xmessage_cmd, _zenity_cmd

        for cmd in (_zenity_cmd(text), _kdialog_cmd(text), _xmessage_cmd(text)):
            if cmd is None:
                continue
            try:
                subprocess.run(cmd, check=False, timeout=10)
                return
            except (OSError, subprocess.TimeoutExpired):
                continue
    except Exception:
        logger.debug("OS-native fallback failed", exc_info=True)


def _try_desktop_notification(text: str) -> None:
    """Best-effort notify-send call.

    Sends the message to the OS notification daemon so it surfaces
    even when a Qt modal can't get to the top. Failure is silent
    (debug-logged) — the fallback chain in :func:`notify_already_running`
    has more channels to try.
    """
    notifier = shutil.which("notify-send")
    if notifier is None:
        return
    try:
        subprocess.run(
            [
                notifier,
                "--app-name=Polyglot AI",
                "--icon=polyglot-ai",
                "Polyglot AI is already running",
                # notify-send body fields strip most formatting;
                # collapse the multi-line message to a single
                # readable line.
                text.replace("\n\n", " — ").replace("\n", " "),
            ],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("notify-send failed", exc_info=True)
