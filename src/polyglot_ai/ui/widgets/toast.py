"""Toast widget — non-blocking, auto-dismissing in-app notifications.

The :class:`ToastManager` owns a vertical stack of :class:`Toast`
widgets pinned to the top-right of a host window. Each toast slides
in, sits for a few seconds, then fades out and removes itself. The
manager handles z-order, repositioning on resize, and stacking
multiple toasts when they arrive in rapid succession.

The widget knows nothing about the EventBus or :class:`Notifier`;
it's just a sink. ``main_window`` wires
:py:meth:`Notifier.set_delivery` to :py:meth:`ToastManager.show` so
the two halves stay decoupled — that's what lets the notifier be
unit-tested without Qt.

Visual design: dark frame with a coloured 3-pixel left edge that
encodes the severity. Click to dismiss early. We pointedly do not
animate the slide-in or the fade — the cost in flicker risk on
older Linux compositors outweighs the polish gain. A simple
``show()/hide()`` is more robust.
"""

from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from polyglot_ai.core.notifications import Notification, NotificationLevel

# Auto-dismiss timeout. Errors stick around longer because the user is
# likely *not* watching when one fires (the whole point of the toast
# is to surface a missed event).
_DISMISS_MS = {
    NotificationLevel.INFO: 4_000,
    NotificationLevel.SUCCESS: 4_000,
    NotificationLevel.WARN: 6_000,
    NotificationLevel.ERROR: 8_000,
}

# Severity → left-edge accent colour. Kept in sync with the broader
# theme palette by hand; if the theme picker grows a "warn" colour
# we can swap to that here.
_ACCENT = {
    NotificationLevel.INFO: "#4d8fc4",
    NotificationLevel.SUCCESS: "#4caf50",
    NotificationLevel.WARN: "#e0a23a",
    NotificationLevel.ERROR: "#d9534f",
}

# Layout constants — gap between stacked toasts and offset from the
# host window's top-right corner. Pulled out as module-level so a
# future settings entry could override them without rewriting the
# widget.
_TOAST_WIDTH = 360
_TOAST_GAP = 8
_TOP_OFFSET = 16
_RIGHT_OFFSET = 16


class Toast(QFrame):
    """Single notification card. Owned by the manager; never freestanding."""

    def __init__(self, notification: Notification, parent: QWidget) -> None:
        super().__init__(parent)
        self._notification = notification
        self.setObjectName("Toast")
        # Frameless + on-top is what gives the float-over-other-widgets
        # behaviour. ``Qt.WindowType.SubWindow`` keeps the toast
        # logically inside the parent window so it doesn't appear in
        # the taskbar.
        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(_TOAST_WIDTH)

        accent = _ACCENT[notification.level]
        # Two-tone styling: a thick coloured left border plus the dark
        # body. Border-left-* in QSS is reliably honoured on PyQt6 so
        # we don't need a separate accent-strip widget.
        self.setStyleSheet(
            f"""
            QFrame#Toast {{
                background-color: #2b2b2d;
                border: 1px solid #444;
                border-left: 3px solid {accent};
                border-radius: 6px;
            }}
            QLabel#ToastTitle {{
                color: #ffffff;
                font-weight: 600;
                font-size: 11pt;
            }}
            QLabel#ToastBody {{
                color: #c8c8c8;
                font-size: 10pt;
            }}
            QToolButton#ToastClose {{
                color: #888;
                background: transparent;
                border: none;
                font-size: 14pt;
                padding: 0 4px;
            }}
            QToolButton#ToastClose:hover {{
                color: #fff;
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 8, 10)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        title = QLabel(notification.title)
        title.setObjectName("ToastTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        header.addWidget(title, stretch=1)

        close_btn = QToolButton()
        close_btn.setObjectName("ToastClose")
        close_btn.setText("×")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Dismiss")
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn)

        outer.addLayout(header)

        body = QLabel(notification.body)
        body.setObjectName("ToastBody")
        body.setWordWrap(True)
        # Cap the body to 4 lines visually — the widget already wraps,
        # but very long error strings would push the toast off-screen.
        body.setMaximumHeight(80)
        outer.addWidget(body)

        # Auto-dismiss timer. We hold a strong reference so the timer
        # isn't GC'd before it fires; ``deleteLater`` cleans both up
        # together.
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._dismiss)
        self._dismiss_timer.start(_DISMISS_MS[notification.level])

    # Click-anywhere-to-dismiss. We override ``mousePressEvent`` rather
    # than wrapping the body in a clickable widget — simpler and the
    # close button still fires its own ``clicked`` signal first.
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dismiss()
            event.accept()
            return
        super().mousePressEvent(event)

    def _dismiss(self) -> None:
        # Stop the timer first — otherwise it could re-fire ``_dismiss``
        # on a half-deleted widget if dismiss is triggered manually
        # right before the timeout.
        self._dismiss_timer.stop()
        # ``hide`` is immediate; ``deleteLater`` schedules cleanup once
        # the manager has had a chance to drop its reference and
        # reposition the rest of the stack.
        self.hide()
        manager = self.parent()
        if isinstance(manager, ToastManager):
            manager._on_toast_dismissed(self)
        self.deleteLater()


class ToastManager(QObject):
    """Stacks toasts vertically in the top-right of a host window.

    Lives as a child of the host window so it's automatically cleaned
    up on window close. Reposition logic runs on the host's resize
    event via an event filter — that's cheaper than connecting to a
    signal that doesn't exist on QMainWindow.
    """

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._toasts: list[Toast] = []
        host.installEventFilter(self)

    # Public API — what main_window passes to Notifier.set_delivery.
    def show(self, notification: Notification) -> None:
        """Append a toast. Safe to call from the GUI thread only."""
        toast = Toast(notification, self._host)
        self._toasts.append(toast)
        self._reposition()
        toast.show()
        toast.raise_()

    # ── Internals ───────────────────────────────────────────────────

    def _on_toast_dismissed(self, toast: Toast) -> None:
        # ``remove`` raises if the toast was already pulled from the
        # list (e.g. by a host-window resize during dismiss). Use a
        # forgiving filter instead.
        self._toasts = [t for t in self._toasts if t is not toast]
        self._reposition()

    def _reposition(self) -> None:
        # Toasts stack from the top down. Skip any that were dismissed
        # but haven't yet been GC'd to avoid painting a phantom row.
        y = _TOP_OFFSET
        try:
            host_w = self._host.width()
        except RuntimeError:
            # Host QWidget already destroyed (window-close race
            # during shutdown). Nothing to lay out against; bail
            # quietly. The manager itself is parented to the host
            # so it'll be cleaned up immediately afterwards.
            return
        for toast in self._iter_alive():
            try:
                tw = toast.width()
                th = toast.height()
            except RuntimeError:
                # Same race — C++ side gone between iter_alive's
                # liveness check and the move. Skip rather than
                # crash; it'll be pruned on the next pass.
                continue
            x = host_w - tw - _RIGHT_OFFSET
            try:
                toast.move(max(0, x), y)
            except RuntimeError:
                continue
            y += th + _TOAST_GAP

    def _iter_alive(self) -> Iterable[Toast]:
        # Some toasts may have had their underlying C++ widget
        # destroyed without going through ``_dismiss`` — the host
        # window closing mid-stream is the usual cause. Calling
        # any method on a dead wrapper raises ``RuntimeError:
        # wrapped C/C++ object of type Toast has been deleted``,
        # which used to take down the whole app on the next
        # resize event. Prune dead refs in-place and skip them
        # silently.
        survivors: list[Toast] = []
        had_dead = False
        for t in self._toasts:
            try:
                # ``isVisible`` would be cheaper but a freshly-
                # created toast hasn't been shown yet at the
                # moment we reposition. ``isHidden()`` is the
                # inverse and only true after ``hide()`` was
                # called.
                hidden = t.isHidden()
            except RuntimeError:
                had_dead = True
                continue
            survivors.append(t)
            if not hidden:
                yield t
        # Update the canonical list only when we actually pruned
        # something — avoids a needless list rebuild on every
        # resize tick in the steady state.
        if had_dead:
            self._toasts = survivors

    # Reposition on host resize so toasts stay glued to the top-right.
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            if obj is self._host and event.type() == QEvent.Type.Resize:
                self._reposition()
        except RuntimeError:
            # Host disappeared mid-event — Qt sometimes dispatches
            # one queued event after the C++ widget is gone. Treat
            # it as a no-op rather than a fatal crash.
            return False
        return super().eventFilter(obj, event)


def severity_to_qcolor(level: NotificationLevel) -> QColor:
    """Public helper for callers that want the accent colour directly."""
    return QColor(_ACCENT[level])
