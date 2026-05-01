"""Tests for the board catalog and hex parser.

These pin behaviour the panel relies on:

- Every catalog entry has a unique slug and FQBN (so the picker
  shows no duplicates and the FQBN map is unambiguous).
- USB ID lookup honours the catalog's declared (vid, pid) tuples
  and is case-insensitive at parse time.
- ``boards_for_language`` filters correctly so the language toggle
  in the UI doesn't show boards that can't run the chosen mode.
"""

from polyglot_ai.core.arduino import (
    BOARDS,
    Language,
    board_for_fqbn,
    board_for_usb,
    boards_for_language,
)
from polyglot_ai.core.arduino.service import _parse_hex


class TestBoardCatalog:
    def test_slugs_are_unique(self):
        slugs = [b.slug for b in BOARDS]
        assert len(slugs) == len(set(slugs)), "Duplicate slug in BOARDS"

    def test_fqbns_are_unique(self):
        fqbns = [b.fqbn for b in BOARDS]
        assert len(fqbns) == len(set(fqbns)), "Duplicate FQBN in BOARDS"

    def test_every_board_supports_at_least_one_language(self):
        for b in BOARDS:
            assert b.languages, f"{b.slug} has no languages"

    def test_cpp_supported_everywhere(self):
        # All currently-catalogued boards run C++ via arduino-cli.
        # If a Python-only board is added later this assertion is
        # the right place to relax it deliberately.
        for b in BOARDS:
            assert b.supports(Language.CPP), f"{b.slug} should support C++"

    def test_circuitpython_boards_have_drive_label(self):
        for b in BOARDS:
            if b.supports(Language.CIRCUITPYTHON):
                assert b.cp_drive_label, f"{b.slug} supports CircuitPython but has no drive label"


class TestBoardLookup:
    def test_board_for_fqbn_hits(self):
        b = board_for_fqbn("arduino:avr:uno")
        assert b is not None
        assert b.slug == "uno"

    def test_board_for_fqbn_miss(self):
        assert board_for_fqbn("nonsense:x:y") is None

    def test_board_for_usb_uno(self):
        # Genuine Uno VID/PID — must match the Uno entry, not a
        # generic CH340 clone.
        b = board_for_usb(0x2341, 0x0043)
        assert b is not None
        assert b.slug in {"uno", "nano"}  # Uno and Nano share this id

    def test_board_for_usb_pico(self):
        b = board_for_usb(0x2E8A, 0x000F)
        assert b is not None
        assert b.slug == "pico-w"

    def test_board_for_usb_unknown(self):
        # Random made-up IDs return None so the panel can show
        # "Unknown board" and let the user pick manually.
        assert board_for_usb(0xDEAD, 0xBEEF) is None


class TestLanguageFilter:
    def test_circuitpython_filter_excludes_cpp_only_boards(self):
        cp_boards = boards_for_language(Language.CIRCUITPYTHON)
        slugs = {b.slug for b in cp_boards}
        # Uno is C++ only — must not appear under CircuitPython.
        assert "uno" not in slugs
        # Pico runs all three — must appear.
        assert "pico" in slugs

    def test_micropython_filter(self):
        mp_boards = boards_for_language(Language.MICROPYTHON)
        slugs = {b.slug for b in mp_boards}
        # ESP32 is the canonical MicroPython target.
        assert "esp32" in slugs
        # Teensy is C++-only via Teensyduino.
        assert "teensy41" not in slugs

    def test_cpp_filter_returns_everyone(self):
        assert len(boards_for_language(Language.CPP)) == len(BOARDS)


class TestParseHex:
    def test_plain_hex(self):
        assert _parse_hex("2341") == 0x2341

    def test_with_prefix(self):
        assert _parse_hex("0x2341") == 0x2341

    def test_uppercase(self):
        assert _parse_hex("0X2E8A") == 0x2E8A

    def test_empty_returns_zero(self):
        assert _parse_hex("") == 0
        assert _parse_hex(None) == 0

    def test_garbage_returns_zero(self):
        assert _parse_hex("not-a-hex") == 0
