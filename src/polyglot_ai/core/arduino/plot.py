"""Numbers out of the lines a board prints, for the serial plotter.

The convention is the Arduino IDE's: a line of numbers separated by
spaces, commas or tabs is one sample of each series, and a number may
carry a name — ``temp:21.5,humidity:40``. A line with anything else in
it ("Sensor ready") is text, not data, and is left out of the plot.
"""

from __future__ import annotations

import math
import re

_SEPARATORS = re.compile(r"[,;\t ]+")
#: How many series one line may carry. A board printing a hex dump
#: shouldn't turn into forty lines on the plot.
MAX_SERIES = 8


def parse_values(line: str) -> list[tuple[str, float]] | None:
    """``[(name, value), …]`` for a line of numbers, else None.

    Unnamed values get '' as their name; the plot numbers them by
    position.
    """
    parts = [part for part in _SEPARATORS.split(line.strip()) if part]
    if not parts or len(parts) > MAX_SERIES:
        return None
    values: list[tuple[str, float]] = []
    for part in parts:
        name, sep, number = part.rpartition(":")
        if not sep:
            name, number = "", part
        elif not name:
            return None  # ":5" — not something a sketch prints as data
        try:
            value = float(number)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None  # "nan" and "inf" parse as floats but can't be drawn
        values.append((name, value))
    return values
