"""Resizing the emulator must not throw away what the shell printed.

A terminal that starts hidden is sized 24×80 (the widget has no
geometry yet); showing it shrinks the screen to fit, and pyte's
default resize deleted the top lines — including the prompt — so the
terminal looked dead. Lines must scroll into history instead, and
come back when the screen grows.
"""

from __future__ import annotations

from polyglot_ai.core.terminal.emulator import TerminalEmulator


def _visible(e: TerminalEmulator) -> list[str]:
    return [line.rstrip() for line in e._screen.display if line.strip()]


def test_shrink_keeps_recent_lines_and_moves_the_rest_to_history():
    e = TerminalEmulator(24, 80)
    e.feed(b"line one\r\nline two\r\nline three\r\nuser@host:~$ ")
    assert _visible(e) == ["line one", "line two", "line three", "user@host:~$"]

    e.resize(3, 60)
    assert e.rows == 3 and e.cols == 60
    # The most recent three lines stay on screen; the first scrolled off
    assert _visible(e) == ["line two", "line three", "user@host:~$"]
    assert e.history_length == 1
    assert e.get_all_text() == "line one\nline two\nline three\nuser@host:~$"
    # Cursor still sits on the prompt line
    assert e._screen.cursor.y == 2


def test_shrink_with_room_to_spare_keeps_everything_on_screen():
    e = TerminalEmulator(24, 80)
    e.feed(b"hello\r\nuser@host:~$ ")
    e.resize(9, 57)
    assert _visible(e) == ["hello", "user@host:~$"]
    assert e.history_length == 0
    assert e._screen.cursor.y == 1


def test_grow_pulls_lines_back_from_history():
    e = TerminalEmulator(24, 80)
    e.feed(b"a\r\nb\r\nc\r\nd\r\n$ ")
    e.resize(2, 80)
    assert _visible(e) == ["d", "$"]
    assert e.history_length == 3

    e.resize(4, 80)
    assert _visible(e) == ["b", "c", "d", "$"]
    assert e.history_length == 1
    assert e._screen.cursor.y == 3


def test_narrowing_truncates_columns_without_losing_rows():
    e = TerminalEmulator(5, 80)
    e.feed(b"0123456789abcdef\r\n$ ")
    e.resize(5, 10)
    assert _visible(e) == ["0123456789", "$"]


def test_same_size_is_a_noop():
    e = TerminalEmulator(24, 80)
    e.feed(b"x\r\n$ ")
    e.resize(24, 80)
    assert _visible(e) == ["x", "$"]


def test_typing_still_works_after_resize():
    e = TerminalEmulator(24, 80)
    e.feed(b"$ ")
    e.resize(9, 57)
    e.feed(b"ls -la\r\ntotal 0\r\n$ ")
    assert _visible(e) == ["$ ls -la", "total 0", "$"]
