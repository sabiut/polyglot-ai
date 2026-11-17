"""Sending a line to the board from the serial monitor.

Sending is done by a writer thread: ``send`` hands the line over and
returns, so a board that has stopped reading can't hold up the window.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PyQt6")

from polyglot_ai.core.arduino import serial_monitor as monitor_mod  # noqa: E402
from polyglot_ai.core.arduino.serial_monitor import LINE_ENDINGS, SerialMonitor  # noqa: E402


class Port:
    """A serial port that records what is written to it."""

    def __init__(self, fail: Exception | None = None):
        self.written = b""
        self.fail = fail
        self.release = threading.Event()
        self.release.set()
        self.writing = threading.Event()

    def write(self, data: bytes) -> None:
        self.writing.set()
        self.release.wait(10)
        if self.fail:
            raise self.fail
        self.written += data

    def flush(self) -> None:
        raise AssertionError("flush waits for the board with no time limit; it must not be used")


@pytest.fixture
def monitor(qapp):
    mon = SerialMonitor()
    mon.errors = []
    mon.error.connect(mon.errors.append)
    yield mon
    mon._running = False
    if mon._writer is not None:
        mon._writer.join(2)


def connected(monitor, port):
    monitor._serial, monitor._running, monitor._port = port, True, "/dev/ttyACM0"
    monitor._start_writer()
    return monitor


def test_a_line_is_sent_with_the_ending_asked_for(monitor, qtbot):
    port = Port()
    connected(monitor, port)
    assert monitor.send("on") is True
    assert monitor.send("off", "\r\n") is True
    assert monitor.send("x", "") is True
    qtbot.waitUntil(lambda: port.written == b"on\noff\r\nx", timeout=3000)
    assert monitor.errors == []


def test_text_is_sent_as_utf8(monitor, qtbot):
    port = Port()
    connected(monitor, port).send("température", "")
    qtbot.waitUntil(lambda: port.written == "température".encode(), timeout=3000)


def test_a_board_that_has_stopped_reading_does_not_hold_up_the_caller(monitor, qtbot):
    port = Port()
    port.release.clear()  # every write now blocks, as on a stalled board
    connected(monitor, port)
    began = time.monotonic()
    assert monitor.send("first") is True
    assert port.writing.wait(3)  # the writer thread is stuck in write()…
    assert monitor.send("second") is True
    assert time.monotonic() - began < 1.0  # …and the caller never waited for it
    assert port.written == b""

    port.release.set()
    qtbot.waitUntil(lambda: port.written == b"first\nsecond\n", timeout=3000)


def test_sending_says_so_when_the_board_takes_nothing(monitor, monkeypatch, qtbot):
    monkeypatch.setattr(monitor_mod, "SEND_QUEUE_LINES", 2)
    port = Port()
    port.release.clear()
    connected(monitor, port)
    assert monitor.send("in flight") is True
    assert port.writing.wait(3)
    assert monitor.send("waiting 1") is True
    assert monitor.send("waiting 2") is True
    assert monitor.send("one too many") is False
    assert "isn't taking what is sent" in monitor.errors[-1]
    port.release.set()
    qtbot.waitUntil(lambda: port.written.endswith(b"waiting 2\n"), timeout=3000)
    assert b"one too many" not in port.written


def test_sending_with_nothing_connected_says_so_and_sends_nothing(monitor):
    assert monitor.send("hello") is False
    assert monitor.errors and "Start the monitor first" in monitor.errors[0]


def test_nothing_is_sent_once_the_monitor_has_been_told_to_stop(monitor):
    # Stop is asked for here and carried out by the reading thread a
    # moment later; in between the port is still open.
    port = Port()
    connected(monitor, port)
    monitor._running = False
    assert monitor.send("late") is False
    monitor._writer.join(2)
    assert port.written == b""


def test_a_line_still_waiting_when_the_monitor_stops_is_not_sent(monitor):
    port = Port()
    port.release.clear()
    connected(monitor, port)
    monitor.send("in flight")
    assert port.writing.wait(3)
    monitor.send("never")
    monitor._running = False
    port.release.set()
    monitor._writer.join(3)
    assert not monitor._writer.is_alive()
    assert port.written == b"in flight\n"


def test_what_was_typed_for_one_board_is_not_sent_to_the_next(monitor, qtbot):
    first = Port()
    first.release.clear()
    connected(monitor, first)
    monitor.send("for the first")
    assert first.writing.wait(3)
    monitor.send("also for the first")
    old_writer = monitor._writer

    second = Port()
    connected(monitor, second)  # a new connection: its own queue and writer
    first.release.set()
    old_writer.join(3)
    assert not old_writer.is_alive()
    monitor.send("for the second")
    qtbot.waitUntil(lambda: second.written == b"for the second\n", timeout=3000)
    assert b"also for the first" not in first.written + second.written


def test_the_last_connections_writer_stops_when_a_new_one_starts(monitor):
    connected(monitor, Port())
    old_writer = monitor._writer
    connected(monitor, Port())  # nothing waiting, nothing stuck: just idle
    old_writer.join(3)
    assert not old_writer.is_alive()
    assert monitor._writer.is_alive()


def test_a_board_that_went_away_is_reported_not_raised(monitor, qtbot):
    connected(monitor, Port(fail=OSError("device disconnected")))
    assert monitor.send("hello") is True  # taken for sending; the failure comes after
    qtbot.waitUntil(lambda: bool(monitor.errors), timeout=3000)
    assert monitor.errors == ["Couldn't send to /dev/ttyACM0: device disconnected"]
    # The writer survives a failed write and reports the next one too.
    monitor.send("again")
    qtbot.waitUntil(lambda: len(monitor.errors) == 2, timeout=3000)


def test_new_line_is_the_first_ending_offered():
    assert LINE_ENDINGS[0] == ("New line", "\n")
    assert {ending for _, ending in LINE_ENDINGS} == {"\n", "\r", "\r\n", ""}


# ── stopping and starting again ────────────────────────────────────


class Line:
    """A fake ``serial.Serial``: reads what the test feeds it."""

    opened: list["Line"] = []

    def __init__(self, port, baudrate, timeout, write_timeout):
        self.port = port
        self.incoming: list[bytes] = []
        self.hold = threading.Event()
        self.hold.set()
        self.closed = False
        Line.opened.append(self)

    def readline(self) -> bytes:
        self.hold.wait(10)
        time.sleep(0.01)
        return self.incoming.pop(0) if self.incoming else b""

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_stop_then_start_straight_away_keeps_the_new_connection(monitor, qtbot, monkeypatch):
    import serial

    Line.opened = []
    monkeypatch.setattr(serial, "Serial", Line)
    events: list[str] = []
    monitor.connected.connect(lambda port, baud: events.append(f"connected {port}"))
    monitor.disconnected.connect(lambda: events.append("disconnected"))
    monitor.line_received.connect(lambda line: events.append(f"line {line}"))

    monitor.connect_to("/dev/ttyACM0", 115200)
    first = Line.opened[0]
    first.hold.clear()  # the reader is inside readline(), as it is most of the time
    time.sleep(0.05)
    monitor.disconnect()
    monitor.connect_to("/dev/ttyACM0", 9600)  # before the first reader has noticed
    second = Line.opened[1]
    assert monitor.is_connected and monitor.baud == 9600

    first.incoming.append(b"from the old connection\n")
    first.hold.set()
    qtbot.waitUntil(lambda: first.closed, timeout=3000)
    # The old reader has gone, and left the new connection alone.
    assert monitor.is_connected and monitor._serial is second and not second.closed
    assert monitor.port == "/dev/ttyACM0" and monitor.baud == 9600

    second.incoming.append(b"hello\r\n")
    qtbot.waitUntil(lambda: "line hello" in events, timeout=3000)
    assert "disconnected" not in events
    assert "line from the old connection" not in events

    monitor.disconnect()
    qtbot.waitUntil(lambda: second.closed and "disconnected" in events, timeout=3000)
    assert events.count("disconnected") == 1
    assert not monitor.is_connected and monitor.port is None
