"""Read serial output from a connected board, emit lines to Qt.

The Arduino panel's #1 missing feature was a way to read what the
board prints back via ``Serial.println`` (or MicroPython's
``print()``). Without that, every "did my code work?" question
required a separate terminal, an external Arduino IDE serial
monitor, or — worst case — guessing.

This module is the backend half of that feature. It owns a
``pyserial`` ``Serial`` connection, runs the blocking ``readline``
loop in a worker thread, and emits each chunk through a Qt signal
so the panel can append text without thread-safety footguns. The
panel-side widget (a ``QPlainTextEdit`` + connect/disconnect
buttons + baud dropdown) lives in ``arduino_panel.py``.

Design notes:

* **Bytes, not str.** We deliberately read raw bytes and decode
  with ``errors="replace"`` once we have a complete line. Arduino
  programs sometimes emit binary or partial UTF-8 (esp. when a
  user accidentally sends an int as a byte), and a noisy decoder
  shouldn't take down the monitor.

* **Line-buffered.** We read until newline rather than fixed-size
  chunks because Arduino ``Serial.println`` semantics expect
  line-at-a-time output. ``readline`` blocks until a newline or
  the timeout fires; the timeout (1s) is the worst-case latency
  between data appearing on the wire and the user seeing it.

* **Graceful errors.** ``serial.SerialException`` is the catch-
  all for "port busy / port unplugged / permission denied" and is
  surfaced as a single error signal rather than crashing the
  reader thread or letting the exception escape into Qt's
  exception handler.

* **No send-to-board (MVP).** The user can read but not write.
  Sending requires a separate input box and line-ending toggle
  that's worth a follow-up rather than cramming into the first
  cut.
"""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# Common baud rates for embedded boards. 115200 is the modern
# default (every Arduino starter sketch we ship uses it); 9600 is
# the historical default (older tutorials still call it out).
# Order is "most likely first" so the dropdown's default
# selection lands on the right one for most users.
COMMON_BAUDS: tuple[int, ...] = (115200, 9600, 57600, 38400, 19200, 4800, 2400, 230400, 460800)


class SerialMonitor(QObject):
    """Read-only serial monitor backed by ``pyserial``.

    Lifecycle::

        mon = SerialMonitor()
        mon.line_received.connect(text_widget.appendPlainText)
        mon.error.connect(handle_error)
        mon.connect_to("/dev/ttyACM0", 115200)
        # ... user reads output ...
        mon.disconnect()

    All public methods are safe to call from the GUI thread. The
    actual ``readline`` loop runs in a worker thread so the GUI
    stays responsive during long-running boards (e.g. a sensor
    streaming at 100 Hz).
    """

    # Emitted for each complete line (newline stripped). The
    # widget appends as-is — no further processing needed.
    line_received = pyqtSignal(str)
    # Connection lifecycle, for the panel to update button states.
    connected = pyqtSignal(str, int)  # port, baud
    disconnected = pyqtSignal()
    # Friendly error message — surfaced to the panel's status
    # feed. Errors that happen *during* a connect attempt arrive
    # here too, followed by a disconnected signal.
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._serial = None
        self._thread: threading.Thread | None = None
        # ``_running`` is the main loop predicate. Set to False
        # by ``disconnect`` to ask the thread to stop on its next
        # iteration; the thread itself drops the serial handle
        # before it exits so we never close mid-read.
        self._running = False
        self._port: str | None = None
        self._baud: int = 0

    # ── Public API ─────────────────────────────────────────────────

    def connect_to(self, port: str, baud: int = 115200) -> None:
        """Open ``port`` at ``baud`` and start streaming lines.

        Idempotent — re-calling while connected does nothing. Use
        ``disconnect`` first if you need to switch port or baud.
        """
        if self._running:
            return
        try:
            # Importing here so the module loads cleanly on
            # systems without pyserial (the panel falls back to a
            # "install pyserial" hint instead of failing import).
            import serial
        except ImportError:
            self.error.emit(
                "pyserial isn't installed. Run: pip install pyserial — "
                "the first-launch dependency dialog covers this too."
            )
            return

        try:
            # ``timeout=1`` makes ``readline`` return whatever it
            # has after one second even if no newline arrived. That
            # bounds the latency between the user pressing
            # Disconnect and the worker thread noticing.
            self._serial = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=1.0,
                # ``exclusive=True`` would prevent two monitors
                # from opening the same port; pyserial added it in
                # 3.3 and not all distros ship that yet, so we
                # leave it default-False and rely on the OS to
                # error out cleanly if a second open is attempted.
            )
        except Exception as exc:
            # Most common causes: another monitor already open,
            # the user not in dialout group, the board unplugged
            # between detection and click. The panel's status feed
            # turns this into a friendly message.
            self.error.emit(f"Couldn't open {port}: {exc}")
            self._serial = None
            return

        self._port = port
        self._baud = baud
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop,
            name=f"serial-monitor-{port}",
            daemon=True,
        )
        self._thread.start()
        self.connected.emit(port, baud)
        logger.info("Serial monitor connected: %s @ %d", port, baud)

    def disconnect(self) -> None:
        """Stop the read loop and close the port. Idempotent."""
        if not self._running and self._serial is None:
            return
        self._running = False
        # Don't join the thread here — disconnect is often called
        # from a Qt slot and we don't want to block the UI for up
        # to 1s waiting for the readline timeout. The worker
        # thread observes ``_running`` and exits cleanly on its
        # own, then closes the port. Cancellation latency = at
        # most one ``timeout`` interval (1s).
        logger.info(
            "Serial monitor disconnect requested for %s (worker will exit on next tick)",
            self._port,
        )

    @property
    def is_connected(self) -> bool:
        return self._running

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    # ── Worker thread ──────────────────────────────────────────────

    def _read_loop(self) -> None:
        """Read lines until ``_running`` flips False or the port dies."""
        import serial as _serial  # local rebinding for the except clause

        ser = self._serial
        if ser is None:
            return
        try:
            while self._running:
                try:
                    raw = ser.readline()
                except _serial.SerialException as exc:
                    # Port disappeared (board unplugged) or some
                    # other I/O failure. Surface and exit the loop;
                    # the disconnected signal at the end of the
                    # method gives the panel a chance to react.
                    self.error.emit(f"Serial read error: {exc}")
                    break
                except OSError as exc:
                    # Some Linux setups raise OSError directly on
                    # cable unplug instead of SerialException.
                    self.error.emit(f"Serial I/O error: {exc}")
                    break
                if not raw:
                    # Timeout with no data — perfectly normal when
                    # the board is silent. Loop and check
                    # ``_running`` again so disconnect is responsive.
                    continue
                # Strip the trailing CR/LF from ``readline``'s
                # output so the line-by-line widget can append
                # without double-spacing. Decode with replacement
                # so a stray non-UTF-8 byte (rare but possible) is
                # rendered as ``?`` rather than killing the line.
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self.line_received.emit(text)
        finally:
            # Owned cleanup — close the port from the worker
            # thread that opened it. ``Serial.close`` is safe to
            # call multiple times (no-op on already-closed) so a
            # parallel ``disconnect`` from the GUI won't conflict.
            try:
                ser.close()
            except Exception:
                pass
            self._serial = None
            self._running = False
            self.disconnected.emit()
            logger.info("Serial monitor thread exited (port=%s)", self._port)
            self._port = None
            self._baud = 0
