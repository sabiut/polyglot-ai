"""Which serial lines are data for the plotter, and which are text."""

from __future__ import annotations

import pytest

from polyglot_ai.core.arduino.plot import MAX_SERIES, parse_values


@pytest.mark.parametrize(
    ("line", "values"),
    [
        ("21.5", [("", 21.5)]),
        ("  512\r", [("", 512.0)]),
        ("1 2 3", [("", 1.0), ("", 2.0), ("", 3.0)]),
        ("1,2\t3;4", [("", 1.0), ("", 2.0), ("", 3.0), ("", 4.0)]),
        ("temp:21.5,humidity:40", [("temp", 21.5), ("humidity", 40.0)]),
        ("temp:21.5, humidity:40", [("temp", 21.5), ("humidity", 40.0)]),
        ("x:-3.2e2", [("x", -320.0)]),
    ],
)
def test_lines_of_numbers_are_samples(line, values):
    assert parse_values(line) == values


@pytest.mark.parametrize(
    "line",
    ["", "   ", "Sensor ready", "temp: 21.5 C", "value = 3", ":5", "nan", "1,inf", "12 apples"],
)
def test_anything_else_is_text_and_stays_off_the_plot(line):
    assert parse_values(line) is None


def test_a_line_with_too_many_numbers_is_not_a_sample():
    assert len(parse_values(" ".join("1" * MAX_SERIES))) == MAX_SERIES
    assert parse_values(" ".join("1" * (MAX_SERIES + 1))) is None
