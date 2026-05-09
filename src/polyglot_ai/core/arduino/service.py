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
import importlib.util
import logging
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from polyglot_ai.core.arduino.boards import Board, Language, board_for_usb
from polyglot_ai.core.dependency_check import find_executable


def _resolve_mpremote_argv() -> list[str] | None:
    """Return the argv prefix needed to invoke mpremote, or None.

    Resolution order:

    1. ``mpremote`` console script on PATH or in a known userland
       bin dir — fastest to launch and what most installs provide.
    2. ``python -m mpremote`` against the *running* interpreter, if
       the package is importable from our process. This is the
       common case for users who installed Polyglot AI via the
       wheel: mpremote is a hard dep so the venv has it, but the
       venv's bin dir may not be exported into the subprocess
       PATH on every launcher (AppImage, .desktop entries with
       custom Exec lines, etc.).

    Returns ``None`` only when neither resolves — that's the case
    we tell the user to install mpremote.
    """
    cli = find_executable("mpremote")
    if cli:
        return [cli]
    if importlib.util.find_spec("mpremote") is not None:
        return [sys.executable, "-m", "mpremote"]
    return None


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
        # In-flight subprocess so the panel can cancel a long
        # compile or upload mid-flight. ``cancel_current_upload``
        # terminates whatever's stored here. Set by ``_run`` while
        # a subprocess is alive, cleared in the ``finally``.
        self._current_proc = None  # type: ignore[var-annotated]
        # Set by ``cancel_current_upload`` to distinguish a clean
        # exit from a user-initiated cancel. ``_run`` checks it
        # after the subprocess returns and translates a non-zero
        # rc into a ``-1`` "cancelled" signal so the panel doesn't
        # surface a misleading "Compile failed" status.
        self._cancel_requested = False

    def cancel_current_upload(self) -> bool:
        """Terminate the in-flight compile / upload subprocess.

        Returns True iff a subprocess was actually running (so the
        panel can decide whether to show a "Cancelled" status line
        or a "nothing to cancel" hint). Idempotent — calling when
        nothing is running is a quiet no-op.

        Sends SIGTERM rather than SIGKILL so the child gets a
        chance to finish writing partial output and clean up its
        own temp files. ``_run`` falls through to ``proc.kill``
        if the SIGTERM doesn't take effect within a couple of
        seconds.
        """
        proc = self._current_proc
        if proc is None:
            return False
        self._cancel_requested = True
        try:
            proc.terminate()
        except Exception:
            pass
        return True

    # ── Toolchain detection ────────────────────────────────────────

    def detect_toolchains(self) -> Toolchains:
        """Return which CLIs are on PATH (or in known userland bin dirs).

        Cheap: ``find_executable`` is a thin wrapper around
        ``shutil.which`` plus a handful of ``Path.is_file`` checks
        on directories that don't change during a session. Safe to
        call on every panel refresh.

        Uses ``find_executable`` rather than raw ``shutil.which`` so
        binaries installed under ``~/.local/bin`` (the default for
        ``pip install --user`` and the upstream ``arduino-cli``
        installer) are found even when ``$PATH`` doesn't include
        that directory — same fix as the optional-features dialog.

        ``mpremote`` is also satisfied by the bundled wheel (it's a
        hard dep in pyproject.toml), so ``_resolve_mpremote_argv``
        is consulted as a second signal — if mpremote is importable
        in our interpreter we report it as available even when the
        console-script wrapper isn't on PATH.
        """
        try:
            import serial  # noqa: F401  — presence check only

            pyserial_ok = True
        except ImportError:
            pyserial_ok = False

        mpremote_argv = _resolve_mpremote_argv()
        # Store the joined argv as a display string. Truthiness is
        # all the ``can_micropython`` property cares about; the
        # actual invocation path lives in ``_resolve_mpremote_argv``.
        mpremote_display = " ".join(mpremote_argv) if mpremote_argv else None

        return Toolchains(
            arduino_cli=find_executable("arduino-cli"),
            mpremote=mpremote_display,
            esptool=find_executable("esptool.py") or find_executable("esptool"),
            pyserial_ok=pyserial_ok,
        )

    @staticmethod
    def find_circuitpython_drive(label: str = "CIRCUITPY") -> Path | None:
        """Auto-discover a mounted CircuitPython USB drive.

        CircuitPython boards expose themselves as a USB mass-
        storage device labelled ``CIRCUITPY`` (the default — some
        boards customise this via ``boot.py``). Linux mounts these
        under ``/media/$USER/CIRCUITPY``, ``/run/media/$USER/CIRCUITPY``,
        or — on systems with udisksd — ``/run/media/<uid>/CIRCUITPY``.
        macOS uses ``/Volumes/CIRCUITPY``.

        Without this helper, the panel told the user to "open
        Advanced and pick the CIRCUITPY drive" — most kids don't
        know what a drive label is, so this scan turns the typical
        case into "press Upload, it just works."

        Returns ``None`` when nothing matches; the panel falls
        back to the manual picker.
        """
        import os
        from pathlib import Path as _Path

        # Roots in priority order. Modern udisksd uses
        # ``/run/media``; older stacks still use ``/media``.
        roots: list[_Path] = []
        try:
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        except Exception:
            user = ""
        if user:
            roots.extend([_Path(f"/run/media/{user}"), _Path(f"/media/{user}")])
        # uid-named subdir variant (some Linux distros)
        try:
            uid = os.getuid()
            roots.append(_Path(f"/run/media/{uid}"))
        except Exception:
            pass
        # macOS / generic fallbacks
        roots.extend([_Path("/Volumes"), _Path("/media"), _Path("/mnt")])

        seen: set[str] = set()
        for root in roots:
            key = str(root)
            if key in seen:
                continue
            seen.add(key)
            try:
                if not root.is_dir():
                    continue
            except OSError:
                continue
            try:
                for entry in root.iterdir():
                    if entry.name == label and entry.is_dir():
                        return entry
            except (OSError, PermissionError):
                # Some mount roots reject listing for non-owners
                # (e.g. another user's /run/media subdir). Skip.
                continue
        return None

    @staticmethod
    def user_in_dialout_group() -> bool:
        """Return True iff the current user can read/write USB serial ports.

        On Linux, uploading to a microcontroller over /dev/ttyUSB* or
        /dev/ttyACM* requires the user be in the ``dialout`` group
        (or ``uucp`` on some distros). Without it, ``arduino-cli
        upload`` fails with a permission-denied error that the panel
        can't usefully act on. Detecting up front means we can
        surface a friendly hint *before* the user clicks Upload.

        Returns True on non-Linux platforms (the constraint is
        Linux-specific) and on platforms where the lookup fails so
        we never falsely block a working setup.
        """
        import os
        import sys

        if sys.platform != "linux":
            return True
        try:
            import grp

            user_groups = {g.gr_name for g in grp.getgrall() if os.getuid() in g.gr_mem}
            # The login-group GID isn't enumerated by getgrall().
            try:
                user_groups.add(grp.getgrgid(os.getgid()).gr_name)
            except (KeyError, OSError):
                pass
            # ``dialout`` on Debian/Ubuntu/Fedora; ``uucp`` on Arch
            # and a few other distros.
            return bool(user_groups & {"dialout", "uucp"})
        except (ImportError, OSError):
            # Missing grp / weird filesystem — fail open rather than
            # warn against a setup that may actually work.
            return True

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
        cli = find_executable("arduino-cli")
        if cli:
            boards = await self._list_via_arduino_cli(cli)
            if boards:
                return boards
        return await self._list_via_pyserial()

    async def _list_via_arduino_cli(self, cli_path: str) -> list[DetectedBoard]:
        # arduino-cli's ``board list`` is a short-running subprocess
        # (typically 100–800 ms). We deliberately avoid
        # ``asyncio.create_subprocess_exec`` here because it calls
        # ``events.get_running_loop()`` internally and that raises
        # ``RuntimeError: no running event loop`` whenever this
        # coroutine is driven by a Qt timer tick through a qasync-
        # backed loop — the same compat quirk already worked around
        # in :meth:`_list_via_pyserial`. With board detection
        # polling on a 2.5 s cadence, even one bad tick floods the
        # log; a thread-executor + blocking ``subprocess.run`` is
        # short, safe, and never touches the broken loop state.
        import subprocess as _subprocess

        def _run_sync() -> tuple[int, bytes, bytes]:
            try:
                result = _subprocess.run(
                    [cli_path, "board", "list", "--format", "json"],
                    capture_output=True,
                    timeout=10,
                )
            except (OSError, _subprocess.TimeoutExpired) as exc:
                logger.warning("arduino-cli board list failed: %s", exc)
                return (1, b"", b"")
            return (result.returncode, result.stdout, result.stderr)

        # Push the blocking call into a thread so the UI stays
        # responsive. If the loop itself isn't reachable (the same
        # qasync edge case the pyserial path guards against), fall
        # back to a direct sync call — the subprocess is fast enough
        # that one frame of jank is preferable to the alternative
        # of skipping detection entirely.
        try:
            loop = asyncio.get_running_loop()
            rc, stdout, stderr = await loop.run_in_executor(None, _run_sync)
        except RuntimeError:
            rc, stdout, stderr = _run_sync()

        if rc != 0:
            logger.warning(
                "arduino-cli board list returned %d: %s",
                rc,
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

        # Try to push the scan onto the default executor so the qasync
        # event loop stays responsive — but ``asyncio.to_thread`` calls
        # ``events.get_running_loop()`` internally, which has been
        # observed to raise ``RuntimeError: no running event loop`` when
        # this coroutine is driven from a Qt timer through a qasync-
        # backed loop. ``pyserial.tools.list_ports.comports()`` is
        # millisecond-fast, so falling back to a synchronous call is
        # safe and removes the only failure mode the panel hit in the
        # wild (board detection error spam every 2.5 s polling tick).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return _scan()
        try:
            return await loop.run_in_executor(None, _scan)
        except RuntimeError:
            return _scan()

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
        cli = find_executable("arduino-cli")
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
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
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
        cli = find_executable("arduino-cli")
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
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
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
        mpremote_argv = _resolve_mpremote_argv()
        if mpremote_argv is None:
            yield StepUpdate("MicroPython tools aren't installed yet.", kind="fail")
            return
        if not script.is_file():
            yield StepUpdate(f"Can't find your script: {script}", kind="fail")
            return

        yield StepUpdate("Sending your Python code to the board…")
        rc, _, stderr = await _run(
            *mpremote_argv,
            "connect",
            port,
            "fs",
            "cp",
            str(script),
            ":main.py",
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't send the file. Is the board in run mode?",
                kind="fail",
            )
            return

        yield StepUpdate("Restarting the board…")
        rc, _, stderr = await _run(*mpremote_argv, "connect", port, "reset", service=self)
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
            # ``asyncio.to_thread`` raises "no running event loop"
            # under qasync from a Qt-click chain (same issue the
            # board detector hit). ``run_blocking`` falls back to
            # a real thread + Qt event pump if the standard path
            # is unavailable.
            from polyglot_ai.core.async_utils import run_blocking

            await run_blocking(_safe_copy, script, target)
        except OSError as exc:
            self.last_error_detail = str(exc)
            yield StepUpdate(
                "Couldn't copy the file. Is the drive read-only?",
                kind="fail",
            )
            return
        yield StepUpdate("Done! 🎉", kind="ok")

    # ── Erase user code ─────────────────────────────────────────────
    #
    # Per language, "erase" means different things — but the user-
    # visible promise is the same: "your code is gone, the board is
    # blank again." Crucially, none of these touch firmware (i.e.
    # the MicroPython interpreter, the bootloader, the CircuitPython
    # runtime) — only the user-written entry file. That's a
    # deliberate safety choice: a kid can recover from any of these
    # by running Upload again. ``esptool erase_flash`` would also
    # wipe firmware and brick the board until the user knows how to
    # re-flash it; that's an "advanced user with backup firmware
    # ready" feature, not a panel button.

    async def wipe_micropython(self, port: str) -> AsyncIterator[StepUpdate]:
        """Delete ``main.py`` from a MicroPython board's filesystem."""
        mpremote_argv = _resolve_mpremote_argv()
        if mpremote_argv is None:
            yield StepUpdate("MicroPython tools aren't installed yet.", kind="fail")
            return
        yield StepUpdate("Erasing your code (main.py)…")
        rc, _, stderr = await _run(
            *mpremote_argv,
            "connect",
            port,
            "fs",
            "rm",
            ":main.py",
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
        if rc != 0:
            # mpremote rm of a non-existent file returns non-zero —
            # treat that as "already empty" rather than an error,
            # since the user-facing outcome is the same.
            if "no such file" in (stderr or "").lower() or "OSError" in (stderr or ""):
                yield StepUpdate(
                    "There was no main.py on the board — it's already blank.",
                    kind="ok",
                )
                return
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't erase. Is the board in run mode and the cable plugged in?",
                kind="fail",
            )
            return

        # Soft-reset so the board stops running whatever was loaded.
        yield StepUpdate("Restarting the board…")
        await _run(*mpremote_argv, "connect", port, "reset", service=self)
        yield StepUpdate("Erased — your board is blank again.", kind="ok")

    async def wipe_circuitpython(self, drive: Path) -> AsyncIterator[StepUpdate]:
        """Delete ``code.py`` from the mounted CIRCUITPY drive.

        Pure file delete, no subprocess. The board notices the
        write to its mass-storage filesystem and auto-reloads
        within a second; the user sees the LED stop blinking (or
        whatever the previous code was doing) and the board
        becomes a blank slate.
        """
        if not drive.is_dir():
            yield StepUpdate(
                "Can't find the CIRCUITPY drive. Plug in your board and try again.",
                kind="fail",
            )
            return
        target = drive / "code.py"
        if not target.exists():
            yield StepUpdate(
                "There's no code.py on the drive — your board is already blank.",
                kind="ok",
            )
            return
        yield StepUpdate("Erasing your code (code.py)…")
        try:
            from polyglot_ai.core.async_utils import run_blocking

            def _delete() -> None:
                target.unlink()

            await run_blocking(_delete)
        except OSError as exc:
            self.last_error_detail = str(exc)
            yield StepUpdate(
                "Couldn't delete the file. Is the drive read-only?",
                kind="fail",
            )
            return
        yield StepUpdate("Erased — your board is blank again.", kind="ok")

    async def wipe_cpp(
        self, sketch_dir: Path, board: Board, port: str
    ) -> AsyncIterator[StepUpdate]:
        """Flash a minimal empty sketch to a C++ Arduino board.

        Arduino C++ has no notion of "erase user code" — flash is
        a single contiguous binary, the entire sketch is written
        on every upload. The closest equivalent to "blank slate"
        is uploading an empty sketch (``setup`` + ``loop`` with no
        body) so the previous program stops running. We materialise
        that empty sketch into a sibling ``.polyglot-wipe`` folder
        next to the user's project so the build artefacts don't
        contaminate the user's source tree.
        """
        cli = find_executable("arduino-cli")
        if cli is None:
            yield StepUpdate("Arduino CLI isn't installed yet.", kind="fail")
            return

        # Build the empty-sketch staging dir next to the user's
        # project. Self-cleaning would be nice but ``arduino-cli``
        # caches build artefacts inside the sketch dir, so leaving
        # them around makes a re-wipe instant.
        staging = sketch_dir.parent / ".polyglot-wipe"
        try:
            staging.mkdir(exist_ok=True)
            (staging / "polyglot-wipe.ino").write_text(
                "void setup() {\n  // Erased by Polyglot AI\n}\nvoid loop() {\n}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.last_error_detail = str(exc)
            yield StepUpdate(
                f"Couldn't prepare the empty sketch: {exc}",
                kind="fail",
            )
            return

        yield StepUpdate("Compiling an empty sketch…")
        rc, _, stderr = await _run(
            cli,
            "compile",
            "--fqbn",
            board.fqbn,
            str(staging),
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't compile the empty sketch — that's surprising. "
                "Click 'Ask AI for help' for details.",
                kind="fail",
            )
            return

        yield StepUpdate("Uploading the empty sketch…")
        rc, _, stderr = await _run(
            cli,
            "upload",
            "--fqbn",
            board.fqbn,
            "--port",
            port,
            str(staging),
            service=self,
        )
        if rc == -1:
            yield StepUpdate("Cancelled.", kind="fail")
            return
        if rc != 0:
            self.last_error_detail = stderr
            yield StepUpdate(
                "Couldn't upload the empty sketch. Is the cable plugged in?",
                kind="fail",
            )
            return
        yield StepUpdate("Erased — your board is running an empty sketch.", kind="ok")


# ── Helpers ───────────────────────────────────────────────────────


async def _run(
    *argv: str,
    timeout: float = 180.0,
    service: "ArduinoService | None" = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return ``(rc, stdout, stderr)`` as text.

    Implementation note — uses :mod:`subprocess.Popen` in a thread
    executor rather than :func:`asyncio.create_subprocess_exec`.
    The latter calls ``events.get_running_loop()`` internally and
    that fails with ``RuntimeError: no running event loop`` when
    this coroutine is driven by a Qt timer tick on a qasync-backed
    loop. Compile and upload are user-initiated (one click → one
    subprocess), so the loss of asyncio's child-watcher integration
    is irrelevant; a thread executor keeps the UI responsive
    without exposing the qasync compat quirk.

    Cancellation: when ``service`` is supplied, the running ``Popen``
    handle is registered as ``service._current_proc`` so a parallel
    ``service.cancel_current_upload()`` can SIGTERM it. A successful
    cancel returns rc ``-1`` ("cancelled"), distinguishable from a
    normal failure (positive rc) and a launch failure (127).
    """
    import subprocess as _subprocess

    def _run_sync() -> tuple[int, str, str]:
        # Reset cancel flag at the start so a stale True from the
        # last run doesn't immediately terminate this one.
        if service is not None:
            service._cancel_requested = False
        try:
            proc = _subprocess.Popen(
                list(argv),
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return 127, "", f"Failed to launch {argv[0]!r}: {exc}"
        except OSError as exc:
            return 127, "", f"Failed to launch {argv[0]!r}: {exc}"

        # Register the Popen so cancel_current_upload can reach it.
        # Cleared in finally regardless of how communicate exits.
        if service is not None:
            service._current_proc = proc

        try:
            try:
                stdout_b, stderr_b = proc.communicate(timeout=timeout)
            except _subprocess.TimeoutExpired as exc:
                # Soft-kill first, then hard-kill if the child
                # ignores SIGTERM. Same defensive pattern works
                # for both the timeout case and the user-cancel
                # case (cancel sets _cancel_requested then sends
                # SIGTERM via cancel_current_upload).
                try:
                    proc.kill()
                    extra_stdout, extra_stderr = proc.communicate(timeout=2)
                except Exception:
                    extra_stdout, extra_stderr = b"", b""
                stdout_b = (exc.stdout or b"") + (extra_stdout or b"")
                stderr_b = (exc.stderr or b"") + (extra_stderr or b"")
                if service is not None and service._cancel_requested:
                    return (
                        -1,
                        stdout_b.decode("utf-8", errors="replace"),
                        stderr_b.decode("utf-8", errors="replace") + "\nCancelled by user.",
                    )
                return (
                    124,
                    stdout_b.decode("utf-8", errors="replace"),
                    stderr_b.decode("utf-8", errors="replace")
                    + f"\n{argv[0]} timed out after {timeout:.0f}s",
                )
            rc = proc.returncode or 0
            # cancel_current_upload calls proc.terminate which
            # makes communicate return normally with a non-zero
            # rc. Translate that into the rc=-1 "cancelled"
            # signal so the panel doesn't surface a misleading
            # failure message.
            if service is not None and service._cancel_requested:
                return (
                    -1,
                    stdout_b.decode("utf-8", errors="replace"),
                    (stderr_b.decode("utf-8", errors="replace") + "\nCancelled by user."),
                )
            return (
                rc,
                stdout_b.decode("utf-8", errors="replace"),
                stderr_b.decode("utf-8", errors="replace"),
            )
        finally:
            # Clear the registration regardless — leaving a stale
            # handle would let a later cancel try to kill an
            # already-exited process.
            if service is not None:
                service._current_proc = None
                # Don't reset _cancel_requested here — the caller
                # (the async generators) needs to see it to skip
                # subsequent steps in the pipeline. ``_run`` resets
                # it at the top of the *next* call.

    # Prefer the loop's executor so the UI stays responsive. If the
    # loop isn't reachable (a known qasync edge case during certain
    # timer-driven flows), fall through to a direct sync call — the
    # caller is already a coroutine, so there's nothing else useful
    # we could do.
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_sync)
    except RuntimeError:
        return _run_sync()


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
