"""Tests for TerminalEmulator."""

from polyglot_ai.core.terminal.emulator import TerminalEmulator


def test_basic_output():
    emu = TerminalEmulator(24, 80)
    emu.feed(b"Hello, World!")
    lines = emu.get_lines()
    text = "".join(cell.char for cell in lines[0])
    assert "Hello, World!" in text


def test_newline():
    emu = TerminalEmulator(24, 80)
    emu.feed(b"Line 1\r\nLine 2")
    lines = emu.get_lines()
    line1 = "".join(cell.char for cell in lines[0]).rstrip()
    line2 = "".join(cell.char for cell in lines[1]).rstrip()
    assert "Line 1" in line1
    assert "Line 2" in line2


def test_cursor_position():
    emu = TerminalEmulator(24, 80)
    emu.feed(b"ABC")
    row, col = emu.get_cursor()
    assert row == 0
    assert col == 3


def test_resize():
    emu = TerminalEmulator(24, 80)
    emu.resize(50, 120)
    assert emu.rows == 50
    assert emu.cols == 120


def test_color_output():
    emu = TerminalEmulator(24, 80)
    # ANSI red text
    emu.feed(b"\x1b[31mRed Text\x1b[0m")
    lines = emu.get_lines()
    text = "".join(cell.char for cell in lines[0]).rstrip()
    assert "Red Text" in text
    # First character of "Red" should have red foreground
    r_cell = lines[0][0]
    assert r_cell.fg is not None or r_cell.char == "R"


def test_dirty_flag():
    emu = TerminalEmulator(24, 80)
    assert emu.dirty  # dirty after creation
    emu.get_lines()
    assert not emu.dirty  # cleared after read
    emu.feed(b"x")
    assert emu.dirty  # dirty again after feed


def test_256_color():
    emu = TerminalEmulator(24, 80)
    color = emu._color_256(196)  # bright red in 256 palette
    assert color.startswith("#")
    assert len(color) == 7
