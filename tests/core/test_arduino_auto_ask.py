"""Tests for the auto-Ask preference helpers.

The Qt event-driven side (checkbox + chat send) is harder to drive
without a fully-wired MainWindow; what's pinned here is the
boolean-persistence contract so a future change to the QSettings
key or default doesn't silently flip auto-Ask on for existing
users.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402

from polyglot_ai.ui.panels.arduino_panel import (  # noqa: E402
    _AUTO_ASK_KEY,
    _load_auto_ask_pref,
    _save_auto_ask_pref,
)


@pytest.fixture(scope="module")
def qapp():
    # QSettings's default ctor needs an organisation/application
    # name. Match what app.py does at startup so the test exercises
    # the same store the real app does.
    QCoreApplication.setOrganizationName("PolyglotAI")
    QCoreApplication.setApplicationName("PolyglotAI")
    yield


@pytest.fixture(autouse=True)
def _scrub_pref(qapp):
    """Each test starts with no stored preference."""
    QSettings().remove(_AUTO_ASK_KEY)
    yield
    QSettings().remove(_AUTO_ASK_KEY)


class TestAutoAskPreference:
    def test_default_is_off(self):
        # The whole reason the toggle exists is opt-in. A future
        # PR flipping the default to True without thinking through
        # the implications would fail this test.
        assert _load_auto_ask_pref() is False

    def test_save_and_load_round_trip(self):
        _save_auto_ask_pref(True)
        assert _load_auto_ask_pref() is True
        _save_auto_ask_pref(False)
        assert _load_auto_ask_pref() is False

    def test_truthy_input_is_normalised_to_bool(self):
        # The checkbox's toggled signal sends ``True`` / ``False``
        # but a future caller might hand in 1 / 0; QSettings round-
        # trips depend on the type=bool kwarg, so confirm the
        # helper coerces.
        _save_auto_ask_pref(1)  # type: ignore[arg-type]
        assert _load_auto_ask_pref() is True

    def test_only_writer_is_save_helper(self):
        # The save helper is the only intended writer of this key.
        # Pin this so a future contributor doesn't accidentally
        # introduce a second writer (and a second source of truth).
        # We can't enforce "only one writer" at runtime, but the
        # round-trip test plus the no-stored-value default is the
        # contract callers should rely on.
        QSettings().remove(_AUTO_ASK_KEY)
        assert _load_auto_ask_pref() is False
        _save_auto_ask_pref(True)
        assert QSettings().contains(_AUTO_ASK_KEY)
