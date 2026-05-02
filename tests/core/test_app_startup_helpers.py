"""Tests for the helpers that guard application startup.

Pin three contracts the user-visible UX depends on:

- Stale-lock fallback removes a lock when the recorded PID is
  reused by an unrelated process (otherwise users get permanently
  locked out after a crash + PID reuse).
- "Already running" notification uses *every* available channel
  (stderr + notify-send + QMessageBox) so a Wayland modal hidden
  behind another window can't swallow the message silently.
- The GNOME-Wayland tray hint fires only when both env vars say
  we're on a session likely to need AppIndicator.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PyQt6")


class TestLockOwnerCheck:
    """``_lock_owner_is_unrelated`` is the stale-lock fallback core.

    We don't drive a real QLockFile — getLockInfo() is mocked. The
    interesting logic is the cmdline check; PID 1 (init/systemd)
    is a reliable "alive but not us" PID on every Linux test runner.
    """

    def _run_under_pid(self, pid: int):
        from polyglot_ai.startup import single_instance as app_mod

        # Build a stub QLockFile-like object that returns a fixed
        # ``getLockInfo`` payload — that's the only attribute the
        # function reads.
        class _Stub:
            def getLockInfo(self):  # noqa: N802 — mirrors Qt API
                return (True, pid, "", "polyglot-ai")

        return app_mod.lock_owner_is_unrelated(_Stub())

    def test_pid_belongs_to_unrelated_process(self):
        # PID 1 is init/systemd on every Linux box — never us.
        # On systems without /proc this falls back to "fail closed",
        # which is also the correct answer (we can't tell, so don't
        # break the user's existing lock).
        import sys

        result = self._run_under_pid(1)
        if sys.platform == "linux":
            assert result is True, "PID 1 (init) is never polyglot-ai"
        else:
            assert result is False

    def test_dead_pid_on_linux_is_stale(self):
        import sys

        if sys.platform != "linux":
            pytest.skip("requires /proc")
        # A high PID very unlikely to be allocated. POSIX caps at
        # 2**22; 4 million is safely above any system's pid_max.
        assert self._run_under_pid(4_000_000) is True

    def test_zero_or_negative_pid_returns_false(self):
        # Defensive: a malformed lock file with PID 0 / negative
        # shouldn't be auto-cleared — bail out and let the user
        # see the normal "already running" path instead.
        assert self._run_under_pid(0) is False
        assert self._run_under_pid(-1) is False


class TestNotifyAlreadyRunning:
    """The function must not raise even when every output channel
    is broken — silent crash is worse than silent miss."""

    def test_runs_when_all_channels_fail(self, monkeypatch, caplog):
        from polyglot_ai.startup import single_instance as app_mod

        # Force each fallback to fail.
        monkeypatch.setattr(app_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(
            app_mod.subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
        )
        # And no QApplication around — exercise the no-app path.
        with caplog.at_level(logging.DEBUG):
            app_mod.notify_already_running(None, "/tmp/lock")  # type: ignore[arg-type]
        # Doesn't raise — pin that in the test by reaching this point.

    def test_writes_to_stderr_unconditionally(self, monkeypatch, capsys):
        from polyglot_ai.startup import single_instance as app_mod

        monkeypatch.setattr(app_mod.shutil, "which", lambda _name: None)
        # Suppress any subprocess attempts — irrelevant to this assertion.
        monkeypatch.setattr(app_mod.subprocess, "run", lambda *a, **k: None)
        try:
            app_mod.notify_already_running(None, "/tmp/lock")  # type: ignore[arg-type]
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "already running" in captured.err.lower()
        assert "/tmp/lock" in captured.err


class TestGnomeWaylandTrayHint:
    """The hint must fire on GNOME+Wayland and stay quiet elsewhere."""

    def test_fires_on_gnome_wayland(self, monkeypatch, caplog):
        from polyglot_ai.startup import notifications_setup

        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        with caplog.at_level(logging.INFO, logger="polyglot_ai.startup.notifications_setup"):
            notifications_setup._maybe_warn_gnome_wayland_tray()
        assert any("AppIndicator" in r.message for r in caplog.records)

    def test_quiet_on_kde(self, monkeypatch, caplog):
        from polyglot_ai.startup import notifications_setup

        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="polyglot_ai.startup.notifications_setup"):
            notifications_setup._maybe_warn_gnome_wayland_tray()
        assert not any("AppIndicator" in r.message for r in caplog.records)

    def test_quiet_on_gnome_x11(self, monkeypatch, caplog):
        # GNOME-on-X11 still uses the same SNI host for tray, so
        # the hint shouldn't fire there.
        from polyglot_ai.startup import notifications_setup

        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="polyglot_ai.startup.notifications_setup"):
            notifications_setup._maybe_warn_gnome_wayland_tray()
        assert not any("AppIndicator" in r.message for r in caplog.records)

    def test_quiet_when_env_unset(self, monkeypatch, caplog):
        from polyglot_ai.startup import notifications_setup

        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="polyglot_ai.startup.notifications_setup"):
            notifications_setup._maybe_warn_gnome_wayland_tray()
        assert not any("AppIndicator" in r.message for r in caplog.records)
