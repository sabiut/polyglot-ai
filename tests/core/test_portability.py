"""Portability groundwork: nothing Linux-only may break import or startup elsewhere."""

from __future__ import annotations

import sys
from pathlib import Path

from polyglot_ai import constants
from polyglot_ai.core import settings
from polyglot_ai.core.terminal import pty_process
from polyglot_ai.startup import platform as platform_setup
from polyglot_ai.startup import preflight


class TestDataDir:
    def test_override_wins_everywhere(self, tmp_path):
        env = {"POLYGLOT_AI_DATA_DIR": str(tmp_path / "x")}
        for plat in ("linux", "darwin", "win32"):
            assert constants.default_data_dir(plat, env) == tmp_path / "x"

    def test_linux_uses_xdg(self, tmp_path):
        assert constants.default_data_dir("linux", {}) == (
            Path.home() / ".local" / "share" / "polyglot-ai"
        )
        assert constants.default_data_dir("linux", {"XDG_DATA_HOME": str(tmp_path)}) == (
            tmp_path / "polyglot-ai"
        )

    def test_macos_and_windows_locations(self, tmp_path):
        assert constants.default_data_dir("darwin", {}) == (
            Path.home() / "Library" / "Application Support" / "polyglot-ai"
        )
        assert constants.default_data_dir("win32", {"LOCALAPPDATA": str(tmp_path)}) == (
            tmp_path / "polyglot-ai"
        )


class TestShell:
    def test_windows_uses_comspec(self):
        assert settings.default_shell("win32", {"COMSPEC": r"C:\W\cmd.exe"}) == r"C:\W\cmd.exe"
        assert settings.default_shell("win32", {}) == "cmd.exe"

    def test_posix_prefers_bash_then_shell_env(self):
        shell = settings.default_shell("linux", {"SHELL": "/usr/bin/fish"})
        assert shell == "/bin/bash" if Path("/bin/bash").exists() else shell == "/usr/bin/fish"


class TestPty:
    def test_available_matches_platform(self):
        assert pty_process.PTY_AVAILABLE is (sys.platform != "win32")

    def test_start_fails_cleanly_without_pty(self, monkeypatch):
        monkeypatch.setattr(pty_process, "PTY_AVAILABLE", False)
        proc = pty_process.PtyProcess(on_output=lambda b: None, on_exited=lambda: None)
        try:
            proc.start()
        except RuntimeError as exc:
            assert "pty" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError")


class TestStartupGuards:
    def test_platform_setup_writes_nothing_off_linux(self, monkeypatch, tmp_path):
        monkeypatch.setattr(platform_setup.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        platform_setup.setup_platform()
        assert not (tmp_path / ".local").exists()

    def test_preflight_skips_display_check_off_linux(self, monkeypatch):
        monkeypatch.setattr(preflight.sys, "platform", "darwin")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        preflight.run_preflight()  # must not exit
