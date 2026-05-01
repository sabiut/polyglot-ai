"""Wires the :class:`Notifier` to the UI layer.

This module is the seam between the pure-Python notification policy
in ``core/notifications.py`` and the Qt-bound delivery surface
(toast widget + system tray). Keeping the seam in its own file means
the ``Notifier`` itself stays unit-testable without Qt fixtures and
the wiring can be skipped on platforms or test runs that don't load
a window.

The single entry point is :func:`install_notifications`. It's called
once from ``app.py`` after the main window exists and before the
Qt event loop starts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSystemTrayIcon

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.notifications import Notification, Notifier
from polyglot_ai.core.settings import SettingsManager
from polyglot_ai.ui.widgets.toast import ToastManager

if TYPE_CHECKING:  # pragma: no cover
    from polyglot_ai.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def install_notifications(
    window: "MainWindow", event_bus: EventBus, settings: SettingsManager
) -> Notifier:
    """Attach a Notifier + delivery surface to ``window``.

    Returns the constructed :class:`Notifier` so callers (mostly the
    app bootstrap, occasionally a test harness) can prod it directly.
    The notifier is also stashed on ``window._notifier`` so panels
    can reach it later if they want to fire ad-hoc notifications
    without re-resolving the dependency graph.
    """
    notifier = Notifier(event_bus, settings)
    toast_manager = ToastManager(window)

    # Try to instantiate a system-tray icon. Many Linux desktop
    # environments support it (KDE, GNOME, XFCE, Cinnamon), but a
    # headless display or a minimal WM may not — ``isSystemTrayAvailable``
    # is the official check. We treat tray as a strict enhancement;
    # toasts always work as long as Qt is up.
    tray: QSystemTrayIcon | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        try:
            tray = QSystemTrayIcon(window)
            # Reuse the main window icon so the tray entry has a
            # consistent identity. windowIcon() returns an empty icon
            # when none is set, which the tray quietly accepts.
            icon = window.windowIcon()
            if not icon.isNull():
                tray.setIcon(icon)
            else:
                # Fall back to a generic Qt-supplied icon so the tray
                # entry is at least visible. Using a missing icon on
                # GNOME hides the tray item entirely.
                tray.setIcon(QIcon.fromTheme("dialog-information"))
            tray.setToolTip("Polyglot AI")
            tray.show()
        except Exception:
            logger.exception("System tray init failed — falling back to toasts only")
            tray = None

    def deliver(notification: Notification) -> None:
        # The toast is always shown; it costs nothing and gives the
        # user a record they can scroll back to. The OS-level tray
        # message is opportunistic — it only fires when the policy
        # asked for it (window unfocused, error severity, etc.).
        toast_manager.show(notification)
        if tray is not None and notification.prefer_os:
            try:
                tray.showMessage(
                    notification.title,
                    notification.body,
                    QSystemTrayIcon.MessageIcon.Information,
                    5_000,
                )
            except Exception:
                # Tray showMessage can throw if the underlying D-Bus
                # session evaporates (e.g. the user logged out and
                # back in). Toast already fired; swallow.
                logger.debug("Tray showMessage failed", exc_info=True)

    notifier.set_delivery(deliver)
    notifier.set_window_focused_check(lambda: bool(window.isActiveWindow()))
    notifier.start()

    # Stash on the window for panels that want to fire ad-hoc
    # notifications. Names prefixed with ``_`` to keep typed-attribute
    # surface clean — there's no MainWindow.notifier slot.
    window._notifier = notifier  # type: ignore[attr-defined]
    window._toast_manager = toast_manager  # type: ignore[attr-defined]
    if tray is not None:
        window._tray = tray  # type: ignore[attr-defined]

    return notifier
