"""Pre-flight checks that run before Qt is initialised.

The goal is to turn a fatal "Could not load the Qt platform plugin
xcb" into a clear, actionable message — ideally a graphical dialog
even when Qt itself failed to load. We use a tiny escalation chain:

1. Verify ``PyQt6`` imports cleanly (catches a busted wheel).
2. Verify a display server is reachable (catches headless / SSH).
3. If anything's wrong, route the message to:
   - ``zenity`` / ``kdialog`` / ``xmessage`` if available
     (graphical fallback for double-click users), or
   - stderr otherwise (terminal users see the message inline).

Returns nothing on success; calls ``sys.exit(N)`` with a clear
exit code on failure so wrapper scripts can act on it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def run_preflight() -> None:
    """Run pre-flight checks; exit cleanly with a friendly message on failure.

    Called from ``app.main`` *before* QApplication is created, so a
    failure here can't be masked by Qt's own qFatal dump.
    """
    # 1) PyQt6 must import. Without this the wheel is broken — usually
    #    because the .deb / .rpm bundle was built against a Python
    #    minor version that doesn't match the host's. ``ImportError``
    #    surfaces a real Python message; we wrap it for the user.
    try:
        import PyQt6.QtCore  # noqa: F401
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError as exc:
        _fatal(
            "Polyglot AI couldn't load PyQt6.",
            (
                "The Python Qt bindings failed to import. This usually "
                "means the install is broken or your Python version is "
                "different from the one the wheel was built for "
                "(needs Python 3.11+).\n\n"
                f"Technical detail: {exc}\n\n"
                "Try reinstalling Polyglot AI from "
                "https://github.com/sabiut/polyglot-ai/releases "
                "(see packaging/INSTALL.md for distro hints)."
            ),
            code=11,
        )

    # 2) Headless detection. Without DISPLAY *and* WAYLAND_DISPLAY,
    #    QApplication will refuse to start (or print "could not connect
    #    to display"). Catching here lets us recommend Xvfb / SSH -X
    #    / a real desktop session before the user sees a Qt traceback.
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # Honour the offscreen platform — that's how CI runs us.
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            _fatal(
                "No display server detected.",
                (
                    "Polyglot AI is a desktop app and needs an X11 or "
                    "Wayland session to run. If you're on SSH, try "
                    "reconnecting with `ssh -X`. If you're inside a "
                    "container, run with X11 socket forwarded or set "
                    "QT_QPA_PLATFORM=offscreen for headless smoke "
                    "tests."
                ),
                code=12,
            )


def _fatal(title: str, body: str, *, code: int) -> None:
    """Render the failure as helpfully as possible, then exit.

    Tries graphical fallbacks in priority order (zenity > kdialog
    > xmessage > stderr) so a user who launched from a desktop icon
    still sees the error, not a silently-failed double-click.
    """
    # Always log + stderr — even if a graphical dialog succeeds, we
    # want the message in the log file for bug reports.
    logger.error("Pre-flight failure: %s — %s", title, body)
    full = f"{title}\n\n{body}"
    print("Polyglot AI: " + full, file=sys.stderr)

    # Try each graphical fallback in turn. Any error is non-fatal —
    # the next one or the stderr message above still informs the user.
    for cmd in (_zenity_cmd(full), _kdialog_cmd(full), _xmessage_cmd(full)):
        if cmd is None:
            continue
        try:
            subprocess.run(cmd, check=False, timeout=15)
            break
        except (OSError, subprocess.TimeoutExpired):
            continue

    sys.exit(code)


def _zenity_cmd(message: str) -> list[str] | None:
    if shutil.which("zenity") is None:
        return None
    return [
        "zenity",
        "--error",
        "--title=Polyglot AI",
        "--width=520",
        f"--text={message}",
    ]


def _kdialog_cmd(message: str) -> list[str] | None:
    if shutil.which("kdialog") is None:
        return None
    return ["kdialog", "--title", "Polyglot AI", "--error", message]


def _xmessage_cmd(message: str) -> list[str] | None:
    if shutil.which("xmessage") is None:
        return None
    return ["xmessage", "-center", "-title", "Polyglot AI", message]
