"""PTY process management — fork, read, write, resize."""

from __future__ import annotations

import fcntl
import logging
import os
import pty
import select
import signal
import struct
import termios
import threading
from pathlib import Path

from polyglot_ai.constants import EVT_TERMINAL_EXITED, EVT_TERMINAL_OUTPUT
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


class PtyProcess:
    """Manages a PTY subprocess for terminal emulation."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._master_fd: int | None = None
        self._pid: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False

    def start(
        self,
        shell: str = "/bin/bash",
        cwd: Path | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        """Fork a new PTY process."""
        if self._running:
            return

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        pid, fd = pty.fork()

        if pid == 0:
            # Child process. Any exception here must end in _exit(), not
            # raise — raising would let the Python runtime try to run
            # cleanup that's meant to run in the parent. Print a clear
            # error first so the user sees why the terminal is empty.
            try:
                if cwd:
                    os.chdir(str(cwd))
                os.execvpe(shell, [shell], env)
            except FileNotFoundError:
                os.write(
                    2,
                    f"\x1b[31mShell not found: {shell}\x1b[0m\r\n"
                    "Check Settings → Terminal, or install the shell.\r\n".encode(),
                )
                os._exit(127)
            except OSError as exc:
                os.write(
                    2,
                    f"\x1b[31mFailed to start shell '{shell}': {exc}\x1b[0m\r\n".encode(),
                )
                os._exit(126)
        else:
            # Parent process
            self._master_fd = fd
            self._pid = pid
            self._running = True

            # Set initial size
            self.resize(rows, cols)

            # Set non-blocking
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Start reader thread
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            logger.info("PTY started: pid=%d, shell=%s", pid, shell)

    def _read_loop(self) -> None:
        """Background thread reading PTY output."""
        while self._running:
            fd = self._master_fd
            if fd is None:
                break
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(fd, 65536)
                        if data:
                            self._event_bus.emit(EVT_TERMINAL_OUTPUT, data=data)
                        else:
                            # EOF — process exited
                            break
                    except (OSError, TypeError):
                        break
            except (ValueError, OSError, TypeError):
                break

        self._running = False
        self._event_bus.emit(EVT_TERMINAL_EXITED)
        logger.info("PTY reader loop ended")

    def write(self, data: bytes) -> None:
        """Write data to the PTY."""
        if self._master_fd is not None and self._running:
            try:
                os.write(self._master_fd, data)
            except OSError:
                logger.exception("Failed to write to PTY")

    def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY window."""
        if self._master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def terminate(self) -> None:
        """Terminate the PTY process."""
        self._running = False
        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)

        self._pid = None
        logger.info("PTY terminated")

    @property
    def is_running(self) -> bool:
        return self._running
