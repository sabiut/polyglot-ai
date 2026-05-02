"""Test for the Arduino dialout-group detection.

The Linux serial-port permission gotcha is the #1 cause of failed
Arduino uploads. ``user_in_dialout_group`` lets the panel surface
a one-shot hint (with the exact ``usermod`` command) instead of
letting the user discover the problem the hard way.
"""

from __future__ import annotations

import sys

import pytest

from polyglot_ai.core.arduino.service import ArduinoService


@pytest.mark.skipif(sys.platform != "linux", reason="dialout group is Linux-specific")
class TestUserInDialoutGroup:
    def test_returns_bool(self):
        # The real groups for the test runner — we don't assert
        # True or False (depends on the CI sandbox). We pin the
        # contract: returns a plain bool, never None or raises.
        result = ArduinoService.user_in_dialout_group()
        assert isinstance(result, bool)

    def test_fail_open_when_grp_unavailable(self, monkeypatch):
        # If grp throws OSError (NIS down, weird container), we
        # must not block the user from uploading — return True
        # so the hint isn't shown for a setup that may actually
        # work.
        import grp

        def _explode(*_a, **_k):
            raise OSError("simulated NIS failure")

        monkeypatch.setattr(grp, "getgrall", _explode)
        assert ArduinoService.user_in_dialout_group() is True


class TestNonLinuxAlwaysTrue:
    def test_macos_returns_true(self, monkeypatch):
        # On macOS / Windows the dialout group concept doesn't
        # apply — uploads work without group membership. Pin the
        # short-circuit so the hint is never shown there.
        monkeypatch.setattr(sys, "platform", "darwin")
        assert ArduinoService.user_in_dialout_group() is True
