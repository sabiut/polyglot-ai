"""Claude (subscription) panel — embedded ``claude.ai`` web view.

Anthropic's public ``/v1/messages`` endpoint rejects OAuth bearer
tokens, so a logged-in Claude Pro / Max / Team subscription can't
chat through our normal API path. Users who don't have an API key
hit a 401 and are stranded. This panel sidesteps the API entirely:
it embeds ``claude.ai`` in a ``QWebEngineView`` with a persistent
profile, so the user logs in once with their subscription
credentials and chats through the web app's cookie session — the
same path Firefox would use.

QtWebEngine is **not** a hard dependency. The panel imports it
lazily and, if the import fails (typical on AppImage builds and on
distros where ``python3-pyqt6.qtwebengine`` isn't installed),
falls back to a single-screen widget that opens ``claude.ai`` in
the user's default browser. That keeps the install lean for users
who don't want subscription chat and avoids forcing a ~150 MB
Chromium runtime into the AppImage payload.

Trade-offs versus the API-key Claude provider:
- No tool calls, MCP, workflows, or panel-state context.
- The web-app's own UI is what the user sees — Plan/Changes/Review
  tabs don't see anything here.
The API-key provider stays primary; this is purely additive for
subscription users.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CLAUDE_URL = "https://claude.ai/new"

# Per-distro hint shown when QtWebEngine isn't importable. Picked
# lazily so we don't try to import ``platform`` on every panel
# construction.
_INSTALL_HINTS = {
    "debian": "sudo apt install python3-pyqt6.qtwebengine",
    "rpm": "sudo dnf install python3-qt6-webengine  # or python3-pyqt6-webengine",
    "appimage": (
        "AppImage builds don't bundle QtWebEngine — use the "
        "browser button below, or switch to the .deb / .rpm package."
    ),
}


def _is_webengine_available() -> bool:
    """Probe whether ``QWebEngineView`` can be imported.

    Done as a function (not at module load) so the rest of the
    application can import this module on systems without
    QtWebEngine — the fallback widget is what runs there. The
    import itself is the whole probe; ``QtWebEngineWidgets`` is
    pure Python on top of a C++ extension, so its presence
    determines availability.
    """
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PyQt6.QtWebEngineCore import QWebEngineProfile  # noqa: F401

        return True
    except ImportError:
        return False


def _detect_install_hint() -> str:
    """Best-effort guess at the right package-manager command."""
    # AppImage bundles its own Python under ``$APPDIR``; users can't
    # ``apt install`` more wheels into it, so route them straight to
    # the browser fallback message.
    import os

    if os.environ.get("APPIMAGE"):
        return _INSTALL_HINTS["appimage"]

    # apt-based distros ship ``/usr/bin/dpkg``; rpm-based ship
    # ``/usr/bin/rpm``. Cheap probes that don't require shelling
    # out to a package manager.
    if Path("/usr/bin/dpkg").exists():
        return _INSTALL_HINTS["debian"]
    if Path("/usr/bin/rpm").exists():
        return _INSTALL_HINTS["rpm"]
    # Unknown distro — show both so the user can pick.
    return f"{_INSTALL_HINTS['debian']}\n{_INSTALL_HINTS['rpm']}"


class _BrowserFallback(QWidget):
    """Shown when QtWebEngine isn't available.

    Single-screen panel with an "Open in browser" button and a
    one-line install hint so the feature is still useful — the
    user can chat with their subscription, just not in-window.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from polyglot_ai.ui import theme_colors as tc

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Claude (subscription)")
        title.setStyleSheet(
            f"color: {tc.get('text_heading')}; font-size: {tc.FONT_LG}px; font-weight: 600;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        explanation = QLabel(
            "Chat with your Claude Pro / Max / Team subscription. "
            "Anthropic's public API doesn't accept subscription "
            "tokens, so this panel uses the claude.ai web app "
            "instead."
        )
        explanation.setStyleSheet(f"color: {tc.get('text_secondary')}; font-size: {tc.FONT_MD}px;")
        explanation.setWordWrap(True)
        explanation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explanation.setMaximumWidth(520)
        layout.addWidget(explanation, alignment=Qt.AlignmentFlag.AlignCenter)

        open_btn = QPushButton("Open Claude in browser")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {tc.get('accent_primary')};"
            f"  color: white;"
            f"  border: none;"
            f"  border-radius: 4px;"
            f"  padding: 10px 20px;"
            f"  font-size: {tc.FONT_MD}px;"
            f"  font-weight: 600;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {tc.get('accent_primary_hover')};"
            f"}}"
        )
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CLAUDE_URL)))
        layout.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {tc.get('border_subtle')};")
        divider.setFixedWidth(360)
        layout.addWidget(divider, alignment=Qt.AlignmentFlag.AlignCenter)

        hint_title = QLabel("Want it in-window?")
        hint_title.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; font-weight: 600;"
        )
        hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint_title)

        hint_body = QLabel(
            f"Install Qt WebEngine and reopen this panel:\n\n{_detect_install_hint()}"
        )
        hint_body.setStyleSheet(
            f"color: {tc.get('text_secondary')}; font-family: monospace; font-size: {tc.FONT_SM}px;"
        )
        hint_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint_body, alignment=Qt.AlignmentFlag.AlignCenter)


class ClaudeWebPanel(QWidget):
    """Embedded ``claude.ai`` web view, or a browser-fallback panel.

    The constructor picks the implementation at runtime: if
    QtWebEngine is importable, a real ``QWebEngineView`` is
    embedded and the user's session is persisted to
    ``~/.local/share/polyglot-ai/claude-web-profile/`` so the
    next launch lands them already-logged-in. Otherwise the
    fallback widget is shown.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if not _is_webengine_available():
            layout.addWidget(_BrowserFallback(self))
            self._webengine = False
            return

        self._webengine = True
        self._build_webview(layout)

    def _build_webview(self, layout: QVBoxLayout) -> None:
        """Construct the embedded web view and reload toolbar.

        Imports happen here (not at module load) so the fallback
        path doesn't pay the QtWebEngine import cost.
        """
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        from polyglot_ai.ui import theme_colors as tc

        # Persistent profile — cookies + localStorage live in our
        # XDG_DATA dir so the user stays logged in across launches.
        # Naming the profile keeps us out of the default profile
        # which is shared with anonymous web views and would lose
        # state on Qt updates.
        profile_dir = Path.home() / ".local" / "share" / "polyglot-ai" / "claude-web-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        self._profile = QWebEngineProfile("claude-subscription", self)
        self._profile.setPersistentStoragePath(str(profile_dir))
        self._profile.setCachePath(str(profile_dir / "cache"))
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

        # Toolbar — minimal: reload + open-in-browser. Back/forward
        # would be confusing inside a single-page app like claude.ai.
        toolbar = QWidget()
        toolbar.setFixedHeight(32)
        toolbar.setStyleSheet(
            f"background-color: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_subtle')};"
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(8)

        reload_btn = QPushButton("⟳ Reload")
        reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reload_btn.setFlat(True)
        reload_btn.setStyleSheet(
            f"QPushButton {{ color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {tc.get('text_primary')}; }}"
        )

        open_browser_btn = QPushButton("Open in browser")
        open_browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_browser_btn.setFlat(True)
        open_browser_btn.setStyleSheet(reload_btn.styleSheet())
        open_browser_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CLAUDE_URL)))

        toolbar_layout.addWidget(reload_btn)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(open_browser_btn)
        layout.addWidget(toolbar)

        # The view itself.
        from PyQt6.QtWebEngineCore import QWebEnginePage

        self._view = QWebEngineView()
        self._page = QWebEnginePage(self._profile, self._view)
        self._view.setPage(self._page)
        self._view.setUrl(QUrl(CLAUDE_URL))
        layout.addWidget(self._view, stretch=1)

        reload_btn.clicked.connect(self._view.reload)

    @property
    def webengine_available(self) -> bool:
        """Whether the embedded view is active (vs the fallback)."""
        return self._webengine


class ClaudeWebWindow(QWidget):
    """Standalone top-level window hosting :class:`ClaudeWebPanel`.

    Mirrors the ``ArduinoWindow`` pattern in this codebase: the
    panel is too large for the 200 px sidebar (the ``claude.ai``
    web app expects desktop dimensions), so the activity-bar entry
    pops it as its own window. The owning ``MainWindow`` keeps a
    strong reference so closing the window doesn't garbage-collect
    the panel and lose the user's logged-in session.
    """

    def __init__(
        self,
        panel: ClaudeWebPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Claude (subscription) — Polyglot AI")
        self.resize(1000, 760)
        if parent is not None:
            icon = parent.windowIcon()
            if not icon.isNull():
                self.setWindowIcon(icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel)
        self._panel = panel

    @property
    def panel(self) -> ClaudeWebPanel:
        return self._panel

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
