"""UI tests for the Bootstrap-mode toggle in the ChatPanel header.

Pins the chat-panel side of the bootstrap-mode feature: the button
must accurately reflect the ToolRegistry state, the countdown text
format, and the auto-revert when the monotonic deadline elapses.

These tests do NOT exercise actual shell_exec auto-approval — that
contract is pinned in ``tests/core/test_tool_policy.py::TestBootstrapMode``.
Here we only verify the button ↔ registry binding.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.ai.tools import ToolRegistry
from polyglot_ai.ui.panels.chat_panel import ChatPanel


@pytest.fixture
def chat_panel(qtbot):
    p = ChatPanel()
    qtbot.addWidget(p)
    p.show()
    return p


def test_default_button_label_shows_unlocked(chat_panel):
    assert chat_panel._bootstrap_btn.text() == "  Bootstrap"


def test_toggle_without_registry_is_noop(chat_panel):
    """Clicking before tool registry wiring should not crash."""
    assert chat_panel._tool_registry is None
    chat_panel._toggle_bootstrap_mode()
    # Still shows the default label
    assert chat_panel._bootstrap_btn.text() == "  Bootstrap"


def test_toggle_enables_and_shows_countdown(chat_panel):
    registry = ToolRegistry()
    chat_panel.set_tools([], registry)

    chat_panel._toggle_bootstrap_mode()

    assert registry.is_bootstrap_active() is True
    label = chat_panel._bootstrap_btn.text()
    assert label.startswith("  Bootstrap · ")
    # Format is M:SS — verify by parsing
    _, _, time_part = label.partition("· ")
    minutes, seconds = time_part.split(":")
    assert minutes.isdigit() and seconds.isdigit()
    assert int(minutes) <= 15
    assert 0 <= int(seconds) <= 59


def test_second_toggle_disables(chat_panel):
    registry = ToolRegistry()
    chat_panel.set_tools([], registry)

    chat_panel._toggle_bootstrap_mode()
    assert registry.is_bootstrap_active() is True

    chat_panel._toggle_bootstrap_mode()
    assert registry.is_bootstrap_active() is False
    assert chat_panel._bootstrap_btn.text() == "  Bootstrap"


def test_set_tools_refreshes_label_for_already_active_registry(qtbot):
    """If the registry is somehow already active when set_tools is
    called (theoretical edge case — registries are usually fresh),
    the label should immediately reflect that instead of stale 🔓."""
    registry = ToolRegistry()
    registry.enable_bootstrap_mode(duration_seconds=120)

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.set_tools([], registry)

    assert panel._bootstrap_btn.text().startswith("  Bootstrap · ")


def test_refresh_label_reverts_when_deadline_expires(chat_panel, monkeypatch):
    """Mocking time.monotonic so the registry reports inactive should
    cause _refresh_bootstrap_label to revert the button."""
    import time as _time

    base = _time.monotonic()
    monkeypatch.setattr(_time, "monotonic", lambda: base)

    registry = ToolRegistry()
    chat_panel.set_tools([], registry)
    chat_panel._toggle_bootstrap_mode()
    assert chat_panel._bootstrap_btn.text().startswith("  Bootstrap")

    # Fast-forward past the 15-min default window
    monkeypatch.setattr(_time, "monotonic", lambda: base + 16 * 60)
    chat_panel._refresh_bootstrap_label()

    assert registry.is_bootstrap_active() is False
    assert chat_panel._bootstrap_btn.text() == "  Bootstrap"
