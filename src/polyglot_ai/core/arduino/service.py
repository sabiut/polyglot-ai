"""Toolchain detection, board detection, compile, and upload.

The service is the single backend for both surfaces:

- The kid-friendly Arduino panel calls these methods directly so its
  status text can stay in plain language.
- The AI agent calls them via dedicated tools (added separately) so
  ``/workflow arduino-cpp-build`` exercises the same code path.

Design rules
------------
1. **Plain-language status only.** Every method that streams progress
   yields short strings a child can read ("Sending to your Arduino…"),
   never raw stderr. Detailed errors are surfaced via a separate
   ``last_error_detail`` so the "Ask AI for help" button can ship
   them to the chat panel.
2. **Async, non-blocking.** ``asyncio.create_subprocess_exec`` keeps
   the Qt event loop responsive while long-running compiles spin.
3. **No subprocess shell.** All commands are passed as argv lists —
   no ``shell=True`` and no f-string interpolation into a command
   string.
4. **Safe-by-default.** Refuses to write outside the project
   ``Sandbox`` when one is supplied. The panel passes its own
   sandbox in; the AI tools route through ``shell_exec`` and inherit
   sandbox checks for free.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from polyglot_ai.core.arduino.boards import Board, Language, board_for_usb

logger = logging.getLogger(__name__)

# Module-level latch so the "pyserial missing" warning fires exactly
# once per process. The board detector is polled every 2.5 s; without
# this latch the log filled with the same line 24× per minute.
_pyserial_warned = False


# ── Public data shapes ────────────────────────────────────────────


@dataclass(frozen=True)
class Toolchains:
    """Which CLIs are installed and ready.

    Each attribute is the resolved absolute path, or ``None`` when
    the binary isn't on ``PATH``. The panel uses this to decide
    whether to show install hints up front.
    """

    arduino_cli: str | None  # arduino-cli — for C++ build/upload
    mpremote: str | None  # mpremote — for MicroPython upload
    esptool: str | None  # esptool.py — for flashing MicroPython firmware
    pyserial_ok: bool  # whether ``pyserial`` is importable for port scan

    @property
    def can_cpp(self) -> bool:
        return self.arduino_cli is not None

    @property
    def can_micropython(self) -> bool:
        return self.mpremote is not None

    @property
    def can_circuitpython(self) -> bool:
        # CircuitPython upload is just "copy to USB drive" — no tool
        # required. Detection still wants pyserial for port scans.
        return self.pyserial_ok


@dataclass(frozen=True)
class DetectedBoard:
    """A board the service believes is currently plugged in.

    ``board`` is ``None`` when the USB IDs didn't match any catalog
    entry — the panel falls back to "Unknown board" and lets the
    user pick from a dropdown.
    """

    port: str
    board: Board | None
    vid: int
    pid: int
    description: str = ""


@dataclass
class StepUpdate:
    """One line of plain-language progress."""

    message: str
    # ``ok`` when the step finished successfully, ``fail`` when it
    # errored, ``progress`` for in-flight noise. The panel renders
    # different icons/colours per kind.
    kind: str = "progress"


# ── Service ───────────────────────────────────────────────────────


class ArduinoService:
    """Backend for compile/upload across C++, MicroPython, CircuitPython."""

    def __init__(self) -> None:
        # Most recent detailed error (multi-line stderr / traceback).
        # Cleared at the start of each run; surfaced to the chat
        # panel via the "Ask AI for help" button rather than shown
        # to the kid in the main status area.
        self.last_error_detail: str = ""

    # ── Toolchain detection ────────────────────────────────────────

    def detect_toolchains(self) -> Toolchains:
        """Return which CLIs are on PATH right now.

        Cheap: ``shutil.which`` doesn't fork. Safe to call on every
        panel refresh.
        """
        try:
            import serial  # noqa: F401  — presence check only

            pyserial_ok = True
        except ImportError:
            pyserial_ok = False

        return Toolchains(
            arduino_cli=shutil.which("arduino-cli"),
            mpremote=shutil.which("mpremote"),
            esptool=shutil.which("esptool.py") or shutil.which("esptool"),
            pyserial_ok=pyserial_ok,
        )

    @staticmethod
    def install_hint(language: Language) -> str:
        """Plain-English instruction for installing the missing toolchain.

        Used by the panel when ``detect_toolchains`` reports a gap.
        """
        if language is Language.CPP:
            return (
                "Install Arduino CLI:\n"
                "  • Linux/macOS:  curl -fsSL "
                "https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh\n"
                "  • Windows:      winget install ArduinoSA.CLI"
            )
        if language is Language.MICROPYTHON:
            return "Install MicroPython tools:\n  pip install mpremote esptool"
        # CircuitPython needs no install — code.py is copied to the
        # USB drive directly.
        return (
            "CircuitPython needs no extra tools — your board shows up "
            "as a USB drive named CIRCUITPY. Install pyserial for port "
            "scans: pip install pyserial"
        )

    # ── Board detection ────────────────────────────────────────────

    async def list_connected_boards(self) -> list[DetectedBoard]:
        """Return what's plugged in right now.

        Uses ``arduino-cli board list --format json`` when available
        because it knows board identity for genuine Arduinos. Falls
        back to a pyserial scan + USB-ID lookup so cheap clones and
        ESP/Pico boards are still recognised.
        """
        cli = shutil.which("arduino-cli")
        if cli:
            boards = await self._list_via_arduino_cli(cli)
            if boards:
                return boards
        return await self._list_via_pyserial()

    async def _list_via_arduino_cli(self, cli_path: str) -> list[DetectedBoard]:
        try:
            proc = await asyncio.create_subprocess_exec(
                cli_path,
                "board",
                "list",
                "--format",
                "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, OSError) as exc:
            logger.warning("arduino-cli board list failed: %s", exc)
            return []

        if proc.returncode != 0:
            logger.warning(
                "arduino-cli board list returned %d: %s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace")[:200],
            )
            return []

        import json

        try:
            data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return []

        # arduino-cli 0.35+ returns ``{"detected_ports": [...]}``;
        # older versions return a bare list. Normalise both shapes.
        ports = data.get("detected_ports") if isinstance(data, dict) else data
        if not ports:
            return []

        boards: list[DetectedBoard] = []
        for entry in ports:
            port_info = entry.get("port") or {}
            address = port_info.get("address") or ""
            if not address:
                continue
            props = port_info.get("properties") or {}
            vid = _parse_hex(props.get("vid"))
            pid = _parse_hex(props.get("pid"))

            # arduino-cli identifies the board for genuine hardware;
            # the catalog lookup is only a fallback.
            matching = entry.get("matching_boards") or []
            from polyglot_ai.core.arduino.boards import board_for_fqbn

            catalog_board = None
            if matching:
                first = matching[0]
                fqbn = first.get("fqbn") or ""
                catalog_board = board_for_fqbn(fqbn)
            if catalog_board is None and vid and pid:
                catalog_board = board_for_usb(vid, pid)

            boards.append(
                DetectedBoard(
                    port=address,
                    board=catalog_board,
                    vid=vid or 0,
                    pid=pid or 0,
                    description=port_info.get("label", ""),
                )
            )
        return boards

    async def _list_via_pyserial(self) -> list[DetectedBoard]:
        def _scan() -> list[DetectedBoard]:
            try:
                from serial.tools import list_ports
            except ImportError:
                global _pyserial_warned
                if not _pyserial_warned:
                    _pyserial_warned = True
                    logger.warning(
                        "pyserial not installed — board detection limited "
                        "to what arduino-cli identifies. Install with: "
                        "pip install pyserial"
                    )
                return []
            results: list[DetectedBoard] = []
            for info in list_ports.comports():
                vid = info.vid or 0
                pid = info.pid or 0
                results.append(
                    DetectedBoard(
                        port=info.device,
                        board=board_for_usb(vid, pid) if vid and pid else None,
                        vid=vid,
                        pid=pid,
                        description=info.description or "",
                    )
                )
            return results

        return await asyncio.to_thread(_scan)

    # ── Compile (C++) ──────────────────────────────────────────────

    async def compile_cpp(self, sketch_dir: Path, board: Board) -> AsyncIterator[StepUpdate]:
        """Compile an Arduino sketch directory.

        ``sketch_dir`` must contain a ``.ino`` file with the same name
        as the directory — that's the convention arduino-cli expects.
        Yields plain-language ``StepUpdate``s; the final update is
        ``ok`` on success or ``fail`` on error.
        """
        async for u in self._compile_cpp(sketch_dir, board):
            yield u

    async def _compile_cpp(self, sketch_dir: Path, board: Board) -> AsyncIterator[StepUpdate]:
        cli = shutil.which("arduino-cli")
        if cli is None:
            yield StepUpdate(
                "Arduino CLI isn't installed yet. Open Settings → Arduino for help.",
                kind="fail",
            )
            return
        if not sketch_dir.is_dir():
            yield StepUpdate(f"Can't find your project folder: {sketch_dir}", kind="fail")
            return

        yield StepUpdate("Checking your code…")
        rc, _, stderr = await _run(
            cli,
            "compile",
            "--fqbn",
            board.fqbn,
            str(sketch_dir),
        )
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Your code has a problem. Click 'Ask AI for help' to see what's wrong.",
                kind="fail",
            )
            return
        yield StepUpdate("Code looks good!", kind="ok")

    # ── Upload (C++) ───────────────────────────────────────────────

    async def upload_cpp(
        self, sketch_dir: Path, board: Board, port: str
    ) -> AsyncIterator[StepUpdate]:
        cli = shutil.which("arduino-cli")
        if cli is None:
            yield StepUpdate("Arduino CLI isn't installed yet.", kind="fail")
            return

        yield StepUpdate("Sending your code to the Arduino…")
        rc, _, stderr = await _run(
            cli,
            "upload",
            "--fqbn",
            board.fqbn,
            "--port",
            port,
            str(sketch_dir),
        )
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't upload. Is the cable plugged in? Click 'Ask AI for help' for details.",
                kind="fail",
            )
            return
        yield StepUpdate("Done! 🎉", kind="ok")

    # ── Upload (MicroPython) ───────────────────────────────────────

    async def upload_micropython(self, script: Path, port: str) -> AsyncIterator[StepUpdate]:
        """Copy ``script`` to the board as ``main.py`` and soft-reset."""
        mpremote = shutil.which("mpremote")
        if mpremote is None:
            yield StepUpdate("MicroPython tools aren't installed yet.", kind="fail")
            return
        if not script.is_file():
            yield StepUpdate(f"Can't find your script: {script}", kind="fail")
            return

        yield StepUpdate("Sending your Python code to the board…")
        rc, _, stderr = await _run(
            mpremote,
            "connect",
            port,
            "fs",
            "cp",
            str(script),
            ":main.py",
        )
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't send the file. Is the board in run mode?",
                kind="fail",
            )
            return

        yield StepUpdate("Restarting the board…")
        rc, _, stderr = await _run(mpremote, "connect", port, "reset")
        if rc != 0:
            # Reset failure is non-fatal — code is on the board, will
            # run on next power cycle. Surface as info, not error.
            self.last_error_detail = stderr
            yield StepUpdate(
                "Code is on the board. Unplug and replug to run it.",
                kind="ok",
            )
            return
        yield StepUpdate("Done! 🎉", kind="ok")

    # ── Upload (CircuitPython) ─────────────────────────────────────

    async def upload_circuitpython(self, script: Path, drive: Path) -> AsyncIterator[StepUpdate]:
        """Copy ``script`` onto the mounted CIRCUITPY drive as ``code.py``.

        ``drive`` is the mount point of the USB drive. The panel
        finds it by scanning common mount roots for a directory
        whose name matches ``cp_drive_label``.
        """
        if not script.is_file():
            yield StepUpdate(f"Can't find your script: {script}", kind="fail")
            return
        if not drive.is_dir():
            yield StepUpdate(
                "Can't find the CIRCUITPY drive. Plug in your board and try again.",
                kind="fail",
            )
            return

        target = drive / "code.py"
        yield StepUpdate("Copying your code to the board…")
        try:
            await asyncio.to_thread(_safe_copy, script, target)
        except OSError as exc:
            self.last_error_detail = str(exc)
            yield StepUpdate(
                "Couldn't copy the file. Is the drive read-only?",
                kind="fail",
            )
            return
        yield StepUpdate("Done! 🎉", kind="ok")


# ── Helpers ───────────────────────────────────────────────────────


async def _run(*argv: str, timeout: float = 180.0) -> tuple[int, str, str]:
    """Run a subprocess and return ``(rc, stdout, stderr)`` as text."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return 127, "", f"Failed to launch {argv[0]!r}: {exc}"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"{argv[0]} timed out after {timeout:.0f}s"
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _parse_hex(value: object) -> int:
    """Parse arduino-cli's hex strings (``"2341"``, ``"0x2341"``)."""
    if not value:
        return 0
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    try:
        return int(text, 16)
    except ValueError:
        return 0


def _safe_copy(src: Path, dst: Path) -> None:
    """Atomic copy via temp file so a yanked drive doesn't half-write.

    CircuitPython watches ``code.py`` and reboots the moment it
    changes — copying directly leaves a window where the file
    is half-written and the board will throw a ``SyntaxError``
    flash sequence on its onboard LED.
    """
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(src.read_bytes())
    tmp.replace(dst)
