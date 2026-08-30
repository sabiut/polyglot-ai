"""Wires the release update check into the UI.

``core/update_check.py`` does the actual GitHub-releases probe (rate
limited to once per 24 h); this module is the seam that surfaces its
result:

* A background check a few seconds after launch — a newer release
  produces a one-time toast. Silent when up to date or offline.
* A **Help → Check for Updates…** action that forces a check
  (bypassing the 24 h cache) and always reports back with a dialog,
  including an "Open release page" button.

Checks run on a plain daemon thread; results hop back to the GUI
thread by emitting on the shared :class:`EventBus`, whose Qt
marshaller (installed by ``QtBridgeAdapter``) delivers subscriber
callbacks on the main thread.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from polyglot_ai.constants import APP_VERSION
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.notifications import Notification, NotificationLevel
from polyglot_ai.core.update_check import UpdateInfo, check_for_update

if TYPE_CHECKING:  # pragma: no cover
    from polyglot_ai.ui.main_window import MainWindow

logger = logging.getLogger(__name__)

EVT_UPDATE_AVAILABLE = "update:available"
EVT_UPDATE_MANUAL_RESULT = "update:manual_result"

#: Launch-time delay before the automatic check. Long enough to stay
#: out of the way of provider registration and first paint; short
#: enough that the toast still lands while the user is settling in.
_AUTO_CHECK_DELAY_MS = 8_000


def install_update_check(window: "MainWindow", event_bus: EventBus) -> None:
    """Attach the update-check plumbing to ``window``.

    Must run after ``install_notifications`` (the toast path reads
    ``window._toast_manager``).
    """

    def _on_update_available(*, info: UpdateInfo | None = None, **_kw) -> None:
        if info is None:
            return
        toast_manager = getattr(window, "_toast_manager", None)
        if toast_manager is None:
            return
        toast_manager.show(
            Notification(
                title=f"Update available — v{info.latest_version}",
                body=(
                    f"You're on v{info.current_version}. "
                    "Get the new release via Help → Check for Updates…"
                ),
                level=NotificationLevel.INFO,
            )
        )

    def _on_manual_result(*, info: UpdateInfo | None = None, **_kw) -> None:
        if info is None:
            QMessageBox.information(
                window,
                "Check for Updates",
                f"You're up to date — Polyglot AI v{APP_VERSION} is the latest release.",
            )
            return
        box = QMessageBox(window)
        box.setWindowTitle("Update Available")
        box.setText(
            f"Polyglot AI v{info.latest_version} is available (you're on v{info.current_version})."
        )
        box.setInformativeText("The release page has .deb, .rpm, and AppImage downloads.")
        open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn and info.release_url:
            QDesktopServices.openUrl(QUrl(info.release_url))

    event_bus.subscribe(EVT_UPDATE_AVAILABLE, _on_update_available)
    event_bus.subscribe(EVT_UPDATE_MANUAL_RESULT, _on_manual_result)

    def _check_in_background(*, force: bool, result_event: str) -> None:
        def run() -> None:
            try:
                info = check_for_update(current_version=APP_VERSION, force=force)
            except Exception:
                logger.debug("update check failed", exc_info=True)
                info = None
            # Automatic checks stay silent on "no update"; the manual
            # path always reports so the click visibly did something.
            if info is None and result_event == EVT_UPDATE_AVAILABLE:
                return
            event_bus.emit(result_event, info=info)

        threading.Thread(target=run, name="update-check", daemon=True).start()

    window._action_check_updates.triggered.connect(
        lambda: _check_in_background(force=True, result_event=EVT_UPDATE_MANUAL_RESULT)
    )

    # Opt-out for the automatic phone-home: distro packagers who
    # handle updates through their own repos can set this, and the
    # test suite sets it so a stray timer can't fire a real network
    # call mid-run against torn-down windows. The manual Help-menu
    # action above still works either way.
    if os.environ.get("POLYGLOT_AI_DISABLE_UPDATE_CHECK"):
        return

    QTimer.singleShot(
        _AUTO_CHECK_DELAY_MS,
        lambda: _check_in_background(force=False, result_event=EVT_UPDATE_AVAILABLE),
    )
