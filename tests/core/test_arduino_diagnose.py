"""What a failed build or upload is said to mean.

The panel used to answer every failure with "Your code has a problem.
Click 'Ask AI for help'". These pin the cases it now tells apart, on
output in the shape the tools really print it.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.arduino import diagnose
from polyglot_ai.core.arduino.diagnose import (
    diagnose_compile,
    diagnose_python,
    diagnose_upload,
    library_candidates,
)

# Captured from arduino-cli 1.5.1 with no board packages installed.
NO_PLATFORM = (
    "Error during build: Platform 'arduino:avr' not found: platform not installed\n"
    "Try running `arduino-cli core install arduino:avr`"
)

MISSING_SEMICOLON = (
    "/home/me/blink/blink.ino: In function 'void loop()':\n"
    "/home/me/blink/blink.ino:9:3: error: expected ';' before 'delay'\n"
    "   delay(500);\n"
    "   ^~~~~\n"
    "\n"
    "Error during build: exit status 1"
)


def test_a_board_package_that_was_never_installed_is_not_a_mistake_in_the_code():
    problem = diagnose_compile(NO_PLATFORM, 1, board_name="Arduino Uno")
    assert problem.kind == diagnose.PLATFORM
    assert problem.platform == "arduino:avr"
    assert "Arduino Uno" in problem.message
    assert "hasn't been checked" in problem.message
    assert problem.where == ""


def test_the_package_is_found_from_the_install_hint_alone():
    problem = diagnose_compile(
        "Error: platform not installed\nTry running `arduino-cli core install esp32:esp32`", 1
    )
    assert (problem.kind, problem.platform) == (diagnose.PLATFORM, "esp32:esp32")


def test_a_compiler_error_says_which_file_and_line_in_plain_words():
    problem = diagnose_compile(MISSING_SEMICOLON, 1)
    assert problem.kind == diagnose.CODE
    assert (problem.file, problem.line, problem.column) == ("/home/me/blink/blink.ino", 9, 3)
    assert problem.where == "blink.ino, line 9"
    assert problem.message.startswith("There's a mistake in blink.ino, line 9.")
    assert "semicolon" in problem.message
    # The compiler's own words are kept for the card under the message.
    assert problem.detail == "expected ';' before 'delay'"


@pytest.mark.parametrize(
    ("said", "means"),
    [
        ("'ledPin' was not declared in this scope", "“ledPin” isn't known here"),
        ("expected '}' at end of input", "closing brace } is missing"),
        ("'Strng' does not name a type", "“Strng” isn't a type"),
        ("too few arguments to function 'void digitalWrite(uint8_t, uint8_t)'", "too few values"),
        ('missing terminating " character', "never closed"),
        ("redefinition of 'int count'", "“int count” is defined twice"),
    ],
)
def test_common_compiler_complaints_are_translated(said, means):
    problem = diagnose_compile(f"/s/s.ino:3:1: error: {said}", 1)
    assert means in problem.message


def test_a_complaint_with_no_translation_is_passed_on_as_it_is():
    problem = diagnose_compile("/s/s.ino:3:1: error: lvalue required as left operand", 1)
    assert problem.message == (
        "There's a mistake in s.ino, line 3. lvalue required as left operand"
    )


def test_the_first_error_is_the_one_reported():
    out = "/s/s.ino:3:1: error: 'a' was not declared in this scope\n/s/s.ino:8:1: error: other"
    assert diagnose_compile(out, 1).line == 3


def test_a_warning_is_not_mistaken_for_the_error():
    out = "/s/s.ino:2:1: warning: unused variable 'x'\n/s/s.ino:7:5: error: expected ')' before ';'"
    assert diagnose_compile(out, 1).line == 7


def test_a_missing_library_is_named_by_its_header():
    out = (
        "/home/me/sweep/sweep.ino:1:10: fatal error: Servo.h: No such file or directory\n"
        " #include <Servo.h>\n          ^~~~~~~~~\ncompilation terminated."
    )
    problem = diagnose_compile(out, 1)
    assert (problem.kind, problem.header, problem.line) == (diagnose.LIBRARY, "Servo.h", 1)
    assert "Servo.h" in problem.message


def test_a_build_stopped_for_time_is_not_blamed_on_the_code():
    problem = diagnose_compile("anything at all: error: x", 124)
    assert problem.kind == diagnose.TIMEOUT
    assert "Nothing is wrong with your code" in problem.message


def test_a_folder_that_is_not_a_sketch():
    problem = diagnose_compile("Error opening sketch: main file missing from sketch: x.ino", 1)
    assert problem.kind == diagnose.SKETCH
    assert "same name as the folder" in problem.message


def test_an_undefined_function_at_link_time():
    out = "sketch.ino.cpp.o: In function `loop':\nundefined reference to `blinkTwice()'"
    problem = diagnose_compile(out, 1)
    assert problem.kind == diagnose.CODE
    assert "blinkTwice()" in problem.message


def test_output_that_is_not_recognised_says_so_and_keeps_the_first_line():
    problem = diagnose_compile("\n  something odd happened\nmore", 1)
    assert (problem.kind, problem.detail) == (diagnose.UNKNOWN, "something odd happened")


@pytest.mark.parametrize(
    ("printed", "kind"),
    [
        ('avrdude: ser_open(): can\'t open device "/dev/ttyACM0": Permission denied', "permission"),
        ("could not open port /dev/ttyUSB0: [Errno 16] Device or resource busy", "port_busy"),
        ("avrdude: stk500_recv(): programmer is not responding", "no_answer"),
        ("avrdude: stk500_getsync() attempt 1 of 10: not in sync: resp=0x00", "no_answer"),
        (
            "A fatal error occurred: Failed to connect to ESP32: No serial data received.",
            "no_answer",
        ),
        ("mpremote: could not enter raw repl", "no_answer"),
        (
            'avrdude: ser_open(): can\'t open device "/dev/ttyACM0": No such file or directory',
            "unplugged",
        ),
        ("Error during Upload: exit status 74", "unknown"),
    ],
)
def test_upload_failures_are_told_apart(printed, kind):
    assert diagnose_upload(printed, 1, board_name="Arduino Uno").kind == kind


def test_the_board_that_did_not_answer_is_named():
    problem = diagnose_upload("programmer is not responding", 1, board_name="Arduino Nano")
    assert problem.message.startswith("Arduino Nano didn't answer.")
    assert problem.detail == "programmer is not responding"


def test_an_upload_stopped_for_time_and_one_with_no_board_package():
    assert diagnose_upload("", 124).kind == diagnose.TIMEOUT
    assert diagnose_upload(NO_PLATFORM, 1).platform == "arduino:avr"


def test_permission_wins_over_the_other_readings_of_the_same_line():
    # avrdude prints "can't open device … Permission denied"; the fix is
    # the dialout group, not checking the cable.
    out = 'can\'t open device "/dev/ttyACM0": Permission denied\nprogrammer is not responding'
    assert diagnose_upload(out, 1).kind == diagnose.PERMISSION


def test_a_python_file_with_a_syntax_mistake():
    problem = diagnose_python("import time\nwhile True\n    pass\n", "/proj/main.py")
    assert problem is not None and problem.kind == diagnose.CODE
    assert (problem.file, problem.line) == ("/proj/main.py", 2)
    assert problem.message.startswith("There's a mistake in main.py, line 2: ")


def test_python_that_reads_fine_has_no_problem_even_with_board_only_imports():
    # ``machine`` doesn't exist on this computer; that isn't a mistake.
    assert diagnose_python("import machine\nled = machine.Pin(25)\n") is None


def test_library_names_worth_trying_for_a_header():
    assert library_candidates("Servo.h") == ["Servo"]
    assert library_candidates("Adafruit_NeoPixel.h") == ["Adafruit_NeoPixel", "Adafruit NeoPixel"]
    # Known by another name in the Library Manager: that name first.
    assert library_candidates("DHT.h") == ["DHT sensor library", "DHT"]
    assert library_candidates("utility/Thing.hpp") == ["Thing"]


def test_one_closing_brace_too_many():
    problem = diagnose_compile("/s/s.ino:12:1: error: expected declaration before '}' token", 1)
    assert "one closing brace } too many" in problem.message


# ── output from Windows: drive letters, backslashes ────────────────


def test_a_compiler_error_in_a_windows_path():
    out = r"C:\Users\me\blink\blink.ino:9:3: error: expected ';' before 'delay'"
    problem = diagnose_compile(out, 1)
    assert (problem.kind, problem.line) == (diagnose.CODE, 9)
    assert problem.file == r"C:\Users\me\blink\blink.ino"
    assert problem.where == "blink.ino, line 9"
    assert problem.message.startswith("There's a mistake in blink.ino, line 9.")


def test_a_missing_library_in_a_windows_path():
    out = r"C:\Users\me\sweep\sweep.ino:1:10: fatal error: Servo.h: No such file or directory"
    problem = diagnose_compile(out, 1)
    assert (problem.kind, problem.header, problem.line) == (diagnose.LIBRARY, "Servo.h", 1)
    assert problem.where == "sweep.ino, line 1"
