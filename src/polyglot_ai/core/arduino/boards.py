"""Catalog of supported MCU boards.

Each entry pins:

- ``slug``: short stable id used in starter ``meta.yml`` files and
  panel state. Never shown to a kid.
- ``display_name``: what the panel renders ("Arduino Uno", not
  "arduino:avr:uno").
- ``fqbn``: Fully-Qualified Board Name passed to ``arduino-cli``.
- ``core``: arduino-cli core that must be installed before compile
  works (e.g. ``arduino:avr``).
- ``languages``: which language modes are valid for the board. C++
  is always present; Python entries appear only when the board has
  an upstream MicroPython or CircuitPython port.
- ``usb_ids``: ``(vid, pid)`` tuples used for fallback detection
  when arduino-cli can't identify the board (older Nanos with the
  CH340 chip, generic ESP32 dev boards, etc.). Hex ints; matched
  case-insensitive against ``pyserial`` output.
- ``cp_drive_label``: USB mass-storage label that CircuitPython
  exposes when the board is in run mode (e.g. ``CIRCUITPY``). Empty
  for boards that don't support CircuitPython.

The catalog is intentionally hand-curated rather than scraped at
runtime: kids picking a board from a dropdown should see real
product names, not the 200+ FQBNs ``arduino-cli board listall``
returns. New boards are a one-line addition here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Language(str, Enum):
    """Programming language a board can run.

    Stored as ``str`` so values round-trip through YAML / JSON without
    extra serialisation glue.
    """

    CPP = "cpp"
    MICROPYTHON = "micropython"
    CIRCUITPYTHON = "circuitpython"


@dataclass(frozen=True)
class Board:
    slug: str
    display_name: str
    fqbn: str
    core: str
    languages: tuple[Language, ...]
    usb_ids: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    # CircuitPython mass-storage label (empty if not supported).
    cp_drive_label: str = ""
    # Brief one-line description shown under the name in the picker.
    blurb: str = ""

    def supports(self, language: Language) -> bool:
        return language in self.languages


# ── Catalog ────────────────────────────────────────────────────────
#
# Ordered to put the most common school/hobby kits first so the
# default picker view is friendly. Anything exotic still shows up,
# just lower in the list.

BOARDS: tuple[Board, ...] = (
    # Classic Arduinos — C++ only.
    Board(
        slug="uno",
        display_name="Arduino Uno",
        fqbn="arduino:avr:uno",
        core="arduino:avr",
        languages=(Language.CPP,),
        usb_ids=((0x2341, 0x0043), (0x2341, 0x0001), (0x2A03, 0x0043)),
        blurb="The classic. Great first board.",
    ),
    Board(
        slug="nano",
        display_name="Arduino Nano",
        fqbn="arduino:avr:nano",
        core="arduino:avr",
        languages=(Language.CPP,),
        # Many cheap clones ship with a CH340 USB-serial chip.
        usb_ids=((0x2341, 0x0043), (0x1A86, 0x7523), (0x1A86, 0x55D4)),
        blurb="Smaller Uno. Same brain.",
    ),
    Board(
        slug="mega",
        display_name="Arduino Mega 2560",
        fqbn="arduino:avr:mega",
        core="arduino:avr",
        languages=(Language.CPP,),
        usb_ids=((0x2341, 0x0010), (0x2341, 0x0042)),
        blurb="Lots of pins for big projects.",
    ),
    Board(
        slug="leonardo",
        display_name="Arduino Leonardo",
        fqbn="arduino:avr:leonardo",
        core="arduino:avr",
        languages=(Language.CPP,),
        usb_ids=((0x2341, 0x0036), (0x2341, 0x8036)),
        blurb="Can pretend to be a keyboard or mouse.",
    ),
    Board(
        slug="micro",
        display_name="Arduino Micro",
        fqbn="arduino:avr:micro",
        core="arduino:avr",
        languages=(Language.CPP,),
        usb_ids=((0x2341, 0x0037), (0x2341, 0x8037)),
        blurb="Tiny, with USB built in.",
    ),
    # ESP family — C++ via the ESP32/ESP8266 cores, Python via
    # MicroPython firmware. ESP boards generally don't support
    # CircuitPython (Adafruit dropped support).
    Board(
        slug="esp32",
        display_name="ESP32 Dev Module",
        fqbn="esp32:esp32:esp32",
        core="esp32:esp32",
        languages=(Language.CPP, Language.MICROPYTHON),
        usb_ids=((0x10C4, 0xEA60), (0x1A86, 0x7523), (0x1A86, 0x55D4)),
        blurb="WiFi + Bluetooth. Powerful and cheap.",
    ),
    Board(
        slug="esp32-s3",
        display_name="ESP32-S3",
        fqbn="esp32:esp32:esp32s3",
        core="esp32:esp32",
        languages=(Language.CPP, Language.MICROPYTHON),
        usb_ids=((0x303A, 0x1001),),
        blurb="Newer ESP32 with native USB.",
    ),
    Board(
        slug="esp8266",
        display_name="ESP8266 / NodeMCU",
        fqbn="esp8266:esp8266:nodemcuv2",
        core="esp8266:esp8266",
        languages=(Language.CPP, Language.MICROPYTHON),
        usb_ids=((0x10C4, 0xEA60), (0x1A86, 0x7523)),
        blurb="WiFi on a budget.",
    ),
    # Raspberry Pi Pico — equally happy in C++, MicroPython and
    # CircuitPython. The bootloader exposes a USB drive named
    # RPI-RP2 which the panel watches for.
    Board(
        slug="pico",
        display_name="Raspberry Pi Pico",
        fqbn="rp2040:rp2040:rpipico",
        core="rp2040:rp2040",
        languages=(Language.CPP, Language.MICROPYTHON, Language.CIRCUITPYTHON),
        usb_ids=((0x2E8A, 0x0003), (0x2E8A, 0x000A), (0x2E8A, 0x0005)),
        cp_drive_label="CIRCUITPY",
        blurb="Fast, cheap, and friendly to Python.",
    ),
    Board(
        slug="pico-w",
        display_name="Raspberry Pi Pico W",
        fqbn="rp2040:rp2040:rpipicow",
        core="rp2040:rp2040",
        languages=(Language.CPP, Language.MICROPYTHON, Language.CIRCUITPYTHON),
        usb_ids=((0x2E8A, 0x000F),),
        cp_drive_label="CIRCUITPY",
        blurb="Pico with WiFi.",
    ),
    # Adafruit's CircuitPython-first boards.
    Board(
        slug="circuitplayground-express",
        display_name="Circuit Playground Express",
        fqbn="adafruit:samd:adafruit_circuitplayground_m0",
        core="adafruit:samd",
        languages=(Language.CPP, Language.CIRCUITPYTHON),
        usb_ids=((0x239A, 0x8018), (0x239A, 0x0018)),
        cp_drive_label="CIRCUITPY",
        blurb="Round board with lights, buttons and sensors built in.",
    ),
    Board(
        slug="feather-m4",
        display_name="Adafruit Feather M4 Express",
        fqbn="adafruit:samd:adafruit_feather_m4",
        core="adafruit:samd",
        languages=(Language.CPP, Language.CIRCUITPYTHON),
        usb_ids=((0x239A, 0x8022), (0x239A, 0x0022)),
        cp_drive_label="CIRCUITPY",
        blurb="Tiny and fast. Good for wearables.",
    ),
    # Teensy — popular for audio / MIDI / fast loops. C++ only via
    # the Teensyduino plugin; python ports exist but are partial.
    Board(
        slug="teensy41",
        display_name="Teensy 4.1",
        fqbn="teensy:avr:teensy41",
        core="teensy:avr",
        languages=(Language.CPP,),
        usb_ids=((0x16C0, 0x0483), (0x16C0, 0x0478)),
        blurb="Extremely fast. For audio and serious projects.",
    ),
)


_BY_FQBN: dict[str, Board] = {b.fqbn: b for b in BOARDS}


def board_for_fqbn(fqbn: str) -> Board | None:
    """Return the catalog entry matching ``fqbn``, or ``None``."""
    return _BY_FQBN.get(fqbn)


def board_for_usb(vid: int, pid: int) -> Board | None:
    """Return the first catalog entry that lists ``(vid, pid)``.

    Several boards share USB-serial chips (the CH340 is on countless
    cheap clones), so a match here is a hint, not a guarantee. The
    panel falls back to "Other / pick manually" when no entry matches.
    """
    for board in BOARDS:
        for v, p in board.usb_ids:
            if v == vid and p == pid:
                return board
    return None


def boards_for_language(language: Language) -> tuple[Board, ...]:
    """Return catalog entries that support ``language``.

    Useful for the panel's board picker once the kid has chosen the
    language toggle.
    """
    return tuple(b for b in BOARDS if b.supports(language))
