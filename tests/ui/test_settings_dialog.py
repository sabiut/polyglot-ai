"""Tests for the settings dialog's newly exposed settings controls.

Audit findings: ``editor.word_wrap``, ``editor.show_line_numbers``,
``editor.ai_completions``, ``notifications.enabled`` and
``notifications.ai_long_response_seconds`` were all honored (or at
least defined) in code but had no UI. These tests pin down that the
dialog loads current values into the new controls, that saving writes
them through the SettingsManager, and that an EditorTab applies the
word-wrap / line-number settings once they're injected.
"""

from __future__ import annotations

import asyncio

import pytest
from PyQt6.Qsci import QsciScintilla

from polyglot_ai.core.database import Database
from polyglot_ai.core.settings import DEFAULTS, SettingsManager
from polyglot_ai.ui.dialogs.settings_dialog import SettingsDialog
from polyglot_ai.ui.panels.editor_tab import EditorTab


class _StubKeyring:
    """In-memory stand-in so tests never touch the system keyring."""

    backend_ok = True

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def get_key(self, provider: str) -> str | None:
        return self._keys.get(provider)

    def store_key(self, provider: str, key: str) -> None:
        self._keys[provider] = key

    def delete_key(self, provider: str) -> None:
        self._keys.pop(provider, None)


class _StubSettings:
    """Minimal SettingsManager stand-in for EditorTab injection tests."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key: str):
        if key in self._values:
            return self._values[key]
        return DEFAULTS.get(key)


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def settings(tmp_path, loop):
    """Real SettingsManager against a temp SQLite file."""
    db = Database(tmp_path / "settings-test.db")
    loop.run_until_complete(db.init())
    mgr = SettingsManager(db)
    loop.run_until_complete(mgr.load())
    yield mgr
    loop.run_until_complete(db.close())


@pytest.fixture
def dialog(qtbot, settings):
    dlg = SettingsDialog(settings, _StubKeyring())
    qtbot.addWidget(dlg)
    return dlg


class TestDialogLoadsValues:
    def test_defaults_reflected(self, dialog):
        assert dialog._word_wrap.isChecked() is False
        assert dialog._show_line_numbers.isChecked() is True
        assert dialog._ai_completions.isChecked() is True
        assert dialog._notifications_enabled.isChecked() is True
        assert dialog._notify_long_response_secs.value() == 8

    def test_stored_values_reflected(self, qtbot, settings, loop):
        loop.run_until_complete(settings.set("editor.word_wrap", True))
        loop.run_until_complete(settings.set("editor.show_line_numbers", False))
        loop.run_until_complete(settings.set("editor.ai_completions", False))
        loop.run_until_complete(settings.set("notifications.enabled", False))
        loop.run_until_complete(settings.set("notifications.ai_long_response_seconds", 42))

        dlg = SettingsDialog(settings, _StubKeyring())
        qtbot.addWidget(dlg)

        assert dlg._word_wrap.isChecked() is True
        assert dlg._show_line_numbers.isChecked() is False
        assert dlg._ai_completions.isChecked() is False
        assert dlg._notifications_enabled.isChecked() is False
        assert dlg._notify_long_response_secs.value() == 42

    def test_spinbox_range(self, dialog):
        assert dialog._notify_long_response_secs.minimum() == 5
        assert dialog._notify_long_response_secs.maximum() == 600


class TestDialogSavesValues:
    def test_save_writes_changed_values(self, dialog, settings, loop):
        dialog._word_wrap.setChecked(True)
        dialog._show_line_numbers.setChecked(False)
        dialog._ai_completions.setChecked(False)
        dialog._notifications_enabled.setChecked(False)
        dialog._notify_long_response_secs.setValue(30)

        loop.run_until_complete(dialog._save())

        assert settings.get("editor.word_wrap") is True
        assert settings.get("editor.show_line_numbers") is False
        assert settings.get("editor.ai_completions") is False
        assert settings.get("notifications.enabled") is False
        assert settings.get("notifications.ai_long_response_seconds") == 30

    def test_save_persists_to_disk(self, dialog, settings, loop):
        dialog._word_wrap.setChecked(True)
        dialog._notify_long_response_secs.setValue(120)
        loop.run_until_complete(dialog._save())

        # A fresh manager over the same DB sees the persisted values.
        fresh = SettingsManager(settings._db)
        loop.run_until_complete(fresh.load())
        assert fresh.get("editor.word_wrap") is True
        assert fresh.get("notifications.ai_long_response_seconds") == 120


class TestEditorTabHonorsSettings:
    def test_defaults_before_injection(self, qtbot):
        tab = EditorTab()
        qtbot.addWidget(tab)
        assert tab._editor.wrapMode() == QsciScintilla.WrapMode.WrapNone
        assert tab._editor.marginWidth(0) > 0

    def test_word_wrap_true_applied_on_injection(self, qtbot):
        tab = EditorTab()
        qtbot.addWidget(tab)
        tab.set_ai_services(None, _StubSettings({"editor.word_wrap": True}))
        assert tab._editor.wrapMode() == QsciScintilla.WrapMode.WrapWord

    def test_word_wrap_false_applied_on_injection(self, qtbot):
        tab = EditorTab()
        qtbot.addWidget(tab)
        tab.set_ai_services(None, _StubSettings({"editor.word_wrap": False}))
        assert tab._editor.wrapMode() == QsciScintilla.WrapMode.WrapNone

    def test_line_numbers_hidden_on_injection(self, qtbot):
        tab = EditorTab()
        qtbot.addWidget(tab)
        tab.set_ai_services(None, _StubSettings({"editor.show_line_numbers": False}))
        assert tab._editor.marginWidth(0) == 0
