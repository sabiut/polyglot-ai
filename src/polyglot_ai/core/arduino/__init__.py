"""Arduino & MCU support — board catalog, toolchain service, starters.

This package is the backend for the kid-friendly Arduino panel and the
AI-driven workflows. Both surfaces share one service so behaviour is
identical regardless of whether a child clicks "Upload" or asks the
agent to "make my Arduino blink".

Modules
-------
``boards``
    Static catalog of known boards keyed by a stable slug. Includes
    the kid-friendly display name, FQBN, supported languages, and the
    USB vendor/product IDs used for fallback detection when
    ``arduino-cli`` itself can't identify a board.

``service``
    Detects which toolchains (``arduino-cli``, ``mpremote``,
    ``circup``) are installed, lists connected boards, and runs
    compile/upload via subprocess. The service is async and emits
    plain-language status strings so the UI doesn't have to translate
    error spew into something a child can read.
"""

from polyglot_ai.core.arduino.boards import (
    Board,
    Language,
    BOARDS,
    board_for_fqbn,
    board_for_usb,
    boards_for_language,
)

__all__ = [
    "Board",
    "Language",
    "BOARDS",
    "board_for_fqbn",
    "board_for_usb",
    "boards_for_language",
]
