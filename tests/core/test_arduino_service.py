"""Tests for the Arduino service.

We don't actually fork ``arduino-cli`` or talk to a board here —
those are integration tests that need real hardware on CI to be
meaningful. Instead these tests pin:

- Toolchain detection reads ``shutil.which`` and the pyserial import
  state without crashing when nothing is installed.
- Install hints are present and non-empty for every language so the
  panel always has something to render.
- ``upload_circuitpython`` writes to a temp directory atomically,
  so a yanked drive can't leave a half-written ``code.py``.
- The plain-language status messages stay free of stack traces or
  shell paths — the kid-friendly contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from polyglot_ai.core.arduino.boards import Language
from polyglot_ai.core.arduino.service import (
    ArduinoService,
    StepUpdate,
    _safe_copy,
)


def _drain(agen) -> list[StepUpdate]:
    """Collect every update an async generator yields."""

    async def _go() -> list[StepUpdate]:
        return [u async for u in agen]

    return asyncio.run(_go())


class TestToolchainDetection:
    def test_detect_returns_a_dataclass(self):
        svc = ArduinoService()
        tc = svc.detect_toolchains()
        # Each binary slot is either None or a string path. The
        # boolean ``can_*`` properties are derived; the panel reads
        # them to decide which sections to enable.
        assert tc.arduino_cli is None or isinstance(tc.arduino_cli, str)
        assert tc.mpremote is None or isinstance(tc.mpremote, str)
        assert tc.esptool is None or isinstance(tc.esptool, str)
        assert isinstance(tc.pyserial_ok, bool)
        assert tc.can_cpp == (tc.arduino_cli is not None)
        assert tc.can_micropython == (tc.mpremote is not None)
        assert tc.can_circuitpython == tc.pyserial_ok


class TestInstallHints:
    @pytest.mark.parametrize("lang", [Language.CPP, Language.MICROPYTHON, Language.CIRCUITPYTHON])
    def test_every_language_has_a_hint(self, lang: Language):
        hint = ArduinoService.install_hint(lang)
        assert hint and len(hint) > 10

    def test_cpp_hint_mentions_arduino_cli(self):
        assert "arduino-cli" in ArduinoService.install_hint(Language.CPP).lower()

    def test_micropython_hint_mentions_mpremote(self):
        assert "mpremote" in ArduinoService.install_hint(Language.MICROPYTHON).lower()


class TestKidFriendlyMessaging:
    def test_compile_when_cli_missing_says_what_to_do(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        svc = ArduinoService()
        from polyglot_ai.core.arduino import boards as _b

        updates = _drain(svc.compile_cpp(Path("/tmp/sketch"), _b.BOARDS[0]))
        assert updates, "expected at least one update"
        msg = updates[-1].message.lower()
        # Plain language only — no shell paths, no exec verbs.
        assert "isn't installed" in msg
        assert "/usr/bin" not in msg
        assert "exit" not in msg
        assert updates[-1].kind == "fail"

    def test_upload_micropython_when_tool_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        svc = ArduinoService()
        updates = _drain(svc.upload_micropython(Path("/tmp/x.py"), "/dev/ttyUSB0"))
        assert updates[-1].kind == "fail"
        # Either contraction is fine ("isn't" / "aren't"); we care
        # that the message tells the kid the tool is missing.
        msg = updates[-1].message.lower()
        assert "installed" in msg and "micropython" in msg

    def test_upload_circuitpython_missing_drive(self, tmp_path: Path):
        svc = ArduinoService()
        script = tmp_path / "code.py"
        script.write_text("print('hi')\n")
        # ``drive`` points at a path that exists but isn't a dir —
        # mimics "drive not mounted".
        missing = tmp_path / "no_drive"
        updates = _drain(svc.upload_circuitpython(script, missing))
        assert updates[-1].kind == "fail"
        assert "circuitpy" in updates[-1].message.lower()


class TestSafeCopy:
    def test_writes_atomically(self, tmp_path: Path):
        src = tmp_path / "code.py"
        src.write_text("print('hi')\n")
        dst = tmp_path / "drive" / "code.py"
        dst.parent.mkdir()
        _safe_copy(src, dst)
        assert dst.read_text() == "print('hi')\n"
        # The temp file should not linger after replace().
        assert not (dst.parent / "code.py.tmp").exists()

    def test_overwrites_existing(self, tmp_path: Path):
        src = tmp_path / "new.py"
        src.write_text("new content\n")
        dst = tmp_path / "code.py"
        dst.write_text("old content\n")
        _safe_copy(src, dst)
        assert dst.read_text() == "new content\n"


class TestUploadCircuitPythonHappyPath:
    def test_copies_to_code_py_on_drive(self, tmp_path: Path):
        svc = ArduinoService()
        script = tmp_path / "blink.py"
        script.write_text("import board\n")
        drive = tmp_path / "CIRCUITPY"
        drive.mkdir()

        updates = _drain(svc.upload_circuitpython(script, drive))
        assert updates[-1].kind == "ok"
        assert (drive / "code.py").read_text() == "import board\n"
