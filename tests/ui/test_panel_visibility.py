"""Trimmed activity bar: ``ui.hidden_panels`` hides niche panels by default.

The bar had a dozen icons. Arduino and Database now start hidden, the
user can change the set under Settings → Panels, and hidden panels stay
reachable via the View menu / shortcuts / palette.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.database import Database  # noqa: E402
from polyglot_ai.core.settings import DEFAULTS, SettingsManager  # noqa: E402
from polyglot_ai.ui.widgets.activity_bar import HIDEABLE_PANELS, ActivityBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class TestDefaults:
    def test_arduino_and_database_hidden_by_default(self):
        assert set(DEFAULTS["ui.hidden_panels"]) == {"arduino", "database"}

    def test_defaults_are_hideable_and_core_panels_are_not(self):
        hideable = {k for k, _l, _s in HIDEABLE_PANELS}
        assert set(DEFAULTS["ui.hidden_panels"]) <= hideable
        assert not {"files", "search", "git", "terminal", "settings"} & hideable


class TestActivityBar:
    def test_hidden_panels_hide_buttons(self, qapp):
        bar = ActivityBar()
        bar.set_hidden_panels(["arduino", "database"])
        assert bar._buttons["arduino"].isHidden()
        assert bar._buttons["database"].isHidden()
        assert not bar._buttons["docker"].isHidden()
        bar.set_hidden_panels([])
        assert not bar._buttons["arduino"].isHidden()

    def test_core_panels_cannot_be_hidden(self, qapp):
        bar = ActivityBar()
        bar.set_hidden_panels(["files", "git", "settings", "terminal", "bogus"])
        for key in ("files", "git", "settings", "terminal"):
            assert not bar._buttons[key].isHidden()


class TestMainWindow:
    def test_hiding_the_current_view_falls_back_to_explorer(self, qapp):
        from polyglot_ai.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._on_activity_changed("database")
            assert window._last_sidebar_view == "database"
            window.apply_panel_visibility(["database"])
            assert window._activity_bar._buttons["database"].isHidden()
            assert window._last_sidebar_view == "files"
            assert window._sidebar_visible
            assert window._activity_bar._buttons["files"].active
            # Hidden panels are still reachable programmatically (View menu).
            window._on_activity_changed("database")
            assert window._last_sidebar_view == "database"
        finally:
            window.close()


class TestSettingsDialog:
    @pytest.fixture
    def loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def settings(self, tmp_path, loop):
        db = Database(tmp_path / "s.db")
        loop.run_until_complete(db.init())
        mgr = SettingsManager(db)
        loop.run_until_complete(mgr.load())
        yield mgr
        loop.run_until_complete(db.close())

    def test_checkboxes_reflect_and_save_hidden_panels(self, qapp, settings, loop):
        from polyglot_ai.ui.dialogs.settings_dialog import SettingsDialog

        class _Keyring:
            backend_ok = True

            def get_key(self, provider):
                return None

            def store_key(self, provider, key):
                pass

            def delete_key(self, provider):
                pass

        dlg = SettingsDialog(settings, _Keyring())
        assert dlg._panel_checks["arduino"].isChecked() is False
        assert dlg._panel_checks["docker"].isChecked() is True
        dlg._panel_checks["arduino"].setChecked(True)
        dlg._panel_checks["docker"].setChecked(False)
        loop.run_until_complete(dlg._save())
        assert settings.get("ui.hidden_panels") == ["database", "docker"]
