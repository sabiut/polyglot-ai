"""Why a build or an upload failed, in words, from what the tools printed.

The panel used to answer every failure with "Your code has a problem.
Click 'Ask AI for help'": the compiler's own message — which file,
which line, what is wrong — was kept only to hand to the AI, and a
board package that was never installed, a missing library and a build
that ran out of time were all reported as a mistake in the code.

No Qt and no subprocesses here: text in, a :class:`Problem` out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: What went wrong, coarsely. The panel offers a different way out for each.
CODE = "code"  # the sketch doesn't compile
PLATFORM = "platform"  # the board's support package isn't installed
LIBRARY = "library"  # an #include names a library that isn't installed
TIMEOUT = "timeout"  # the tool was stopped for taking too long
SKETCH = "sketch"  # the folder isn't laid out as a sketch
PORT_BUSY = "port_busy"  # something else has the serial port open
PERMISSION = "permission"  # this user may not open the serial port
NO_ANSWER = "no_answer"  # the board didn't respond to the uploader
UNPLUGGED = "unplugged"  # the port is gone
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Problem:
    kind: str
    #: What happened and what to do, for the status feed.
    message: str
    #: Where, for a problem in the code: the file as the compiler named
    #: it, and 1-based line and column (0 when it didn't say).
    file: str = ""
    line: int = 0
    column: int = 0
    #: The tool's own words for it, one line.
    detail: str = ""
    #: ``arduino:avr`` when that package is what's missing.
    platform: str = ""
    #: ``Servo.h`` when that header is what's missing.
    header: str = ""

    @property
    def where(self) -> str:
        """``blink.ino, line 5`` — or '' when the tool gave no place."""
        if not self.file:
            return ""
        name = Path(self.file).name
        return f"{name}, line {self.line}" if self.line else name


_PLATFORM_MISSING = re.compile(
    r"[Pp]latform '?([\w.+-]+:[\w.+-]+)'? (?:not found|is not installed)"
)
_PLATFORM_HINT = re.compile(r"core install ([\w.+-]+:[\w.+-]+)")
_MISSING_HEADER = re.compile(
    r"^(?P<file>[^\n:]*?):(?P<line>\d+):(?P<col>\d+): fatal error: "
    r"(?P<header>[\w./+-]+\.h(?:pp)?): No such file or directory",
    re.MULTILINE,
)
_GCC_ERROR = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\n:]+?):(?P<line>\d+):(?:(?P<col>\d+):)? "
    r"(?:fatal )?error: (?P<text>.+)$",
    re.MULTILINE,
)
_LINKER_ERROR = re.compile(r"undefined reference to [`'](?P<name>[^`']+)'")
_SKETCH_LAYOUT = re.compile(r"main file missing from sketch|no valid sketch found", re.IGNORECASE)

#: The compiler's commonest complaints, said the way a person would.
#: ``{0}`` is whatever the pattern's first group caught.
_PLAIN: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"expected '[;,]' before"),
        "A semicolon ( ; ) is missing — usually at the end of the line just above.",
    ),
    (
        re.compile(r"expected '\}' at end of input"),
        "A closing brace } is missing: a { further up was never closed.",
    ),
    (
        re.compile(r"expected '\)' before"),
        "A closing bracket ) is missing.",
    ),
    (
        re.compile(r"expected (?:declaration|unqualified-id) before '\}' token"),
        "There is one closing brace } too many.",
    ),
    (
        re.compile(r"'(\w+)' was not declared in this scope"),
        "“{0}” isn't known here. Check the spelling (capital letters count), or create it first.",
    ),
    (
        re.compile(r"'(\w+)' does not name a type"),
        "“{0}” isn't a type the compiler knows. Check the spelling, or the #include it comes from.",
    ),
    (
        re.compile(r"too (few|many) arguments to function"),
        "A function is being given too {0} values between its brackets.",
    ),
    (
        re.compile(r"invalid conversion from '([^']+)' to '([^']+)'"),
        "A value of one kind ({0}) is being used where another kind is needed.",
    ),
    (
        re.compile(r"missing terminating (['\"]) character"),
        "A piece of text was opened with a quote mark and never closed.",
    ),
    (
        re.compile(r"redefinition of '([^']+)'"),
        "“{0}” is defined twice. Keep one and remove (or rename) the other.",
    ),
    (
        re.compile(r"stray '\\(\d+)' in program"),
        "There is a character the compiler can't read — often a curly quote or a "
        "special space pasted in from a web page. Retype that part of the line.",
    ),
)


def _plain(text: str) -> str:
    for pattern, said in _PLAIN:
        found = pattern.search(text)
        if found:
            # Not ``str.format``: the sentences talk about { and }.
            return said.replace("{0}", found.group(1) if found.groups() else "")
    return ""


def _platform_problem(output: str, board_name: str) -> Problem | None:
    found = _PLATFORM_MISSING.search(output) or (
        _PLATFORM_HINT.search(output) if "not installed" in output else None
    )
    if not found:
        return None
    what = f"support for {board_name}" if board_name else "this board's support package"
    return Problem(
        PLATFORM,
        f"Your code hasn't been checked yet: {what} isn't installed on this computer. "
        "It is a one-time download.",
        platform=found.group(1),
        detail=found.group(0),
    )


def _timeout_problem(doing: str) -> Problem:
    return Problem(
        TIMEOUT,
        f"{doing} took too long and was stopped. Nothing is wrong with your code as far "
        "as we can tell — try again; the first build for a board is the slow one.",
    )


def diagnose_compile(output: str, rc: int, *, board_name: str = "") -> Problem:
    """What a failed ``arduino-cli compile`` means.

    ``output`` is everything the tool printed (stderr, or both streams);
    ``rc`` its exit status, with 124 for one we stopped for running out
    of time.
    """
    output = output or ""
    if rc == 124:
        return _timeout_problem("Building your code")
    missing = _platform_problem(output, board_name)
    if missing:
        return missing
    if _SKETCH_LAYOUT.search(output):
        return Problem(
            SKETCH,
            "This folder isn't set up as a sketch: the .ino file has to have the same "
            "name as the folder it is in (blink/blink.ino).",
            detail=_first_line(output),
        )
    header = _MISSING_HEADER.search(output)
    if header:
        name = header.group("header")
        return Problem(
            LIBRARY,
            f"Your code uses a library that isn't installed: {name}.",
            file=header.group("file"),
            line=int(header.group("line")),
            column=int(header.group("col")),
            detail=f"{name}: No such file or directory",
            header=name,
        )
    error = _GCC_ERROR.search(output)
    if error:
        text = error.group("text").strip()
        plain = _plain(text)
        place = Path(error.group("file")).name
        return Problem(
            CODE,
            f"There's a mistake in {place}, line {error.group('line')}. {plain or text}".strip(),
            file=error.group("file"),
            line=int(error.group("line")),
            column=int(error.group("col") or 0),
            detail=text,
        )
    linker = _LINKER_ERROR.search(output)
    if linker:
        name = linker.group("name")
        return Problem(
            CODE,
            f"Your code uses “{name}”, but nothing defines it. Check the spelling, or "
            "write the function it calls.",
            detail=linker.group(0),
        )
    return Problem(
        UNKNOWN,
        "The build failed, and not in a way we recognise. The details are below.",
        detail=_first_line(output),
    )


_PERMISSION = re.compile(r"permission denied|access is denied", re.IGNORECASE)
_BUSY = re.compile(
    r"resource busy|port (?:is )?busy|it may be in use|in use by another|could not open port.*busy",
    re.IGNORECASE,
)
_GONE = re.compile(
    r"no such file or directory|no device found|port not found|cannot find the file|"
    r"no upload port|device not configured",
    re.IGNORECASE,
)
_SILENT = re.compile(
    r"not in sync|programmer is not responding|stk500\w*\(\): |timed out waiting for packet|"
    r"failed to connect to esp|no serial data received|could not enter raw repl|"
    r"can't connect to|unable to (?:open|find).*bootloader",
    re.IGNORECASE,
)


def diagnose_upload(output: str, rc: int, *, board_name: str = "") -> Problem:
    """What a failed upload (``arduino-cli upload`` or ``mpremote``) means."""
    output = output or ""
    board = board_name or "the board"
    if rc == 124:
        return _timeout_problem("Sending your code")
    missing = _platform_problem(output, board_name)
    if missing:
        return missing
    if _PERMISSION.search(output):
        return Problem(
            PERMISSION,
            "This computer isn't allowed to talk to the board yet. On Linux your user "
            "has to be in the “dialout” group — the command for that is shown above "
            "the first time a board is found — and you need to log out and back in.",
            detail=_matching_line(output, _PERMISSION),
        )
    if _BUSY.search(output):
        return Problem(
            PORT_BUSY,
            "Something else is using the board's connection — another serial monitor, "
            "the Arduino IDE, or a second copy of this window. Close it and try again.",
            detail=_matching_line(output, _BUSY),
        )
    if _SILENT.search(output):
        return Problem(
            NO_ANSWER,
            f"{board} didn't answer. Check that the right board is chosen (clones "
            "and older Nanos often need a different one under Advanced), that the cable "
            "carries data and not only power, and for boards with a BOOT button, hold it "
            "while the upload starts.",
            detail=_matching_line(output, _SILENT),
        )
    if _GONE.search(output):
        return Problem(
            UNPLUGGED,
            "The board isn't there any more. Check the USB cable, wait for it to be "
            "found again, then press Upload.",
            detail=_matching_line(output, _GONE),
        )
    return Problem(
        UNKNOWN,
        "The upload failed, and not in a way we recognise. Is the cable plugged in? "
        "The details are below.",
        detail=_first_line(output),
    )


def diagnose_python(source: str, filename: str = "main.py") -> Problem | None:
    """A syntax mistake in a MicroPython or CircuitPython file, or None.

    Both are Python 3 as far as its grammar goes, so this computer's
    Python can read the file even though it can't run it.
    """
    try:
        compile(source, filename, "exec", dont_inherit=True)
    except SyntaxError as exc:
        text = exc.msg or "invalid syntax"
        line = exc.lineno or 0
        return Problem(
            CODE,
            f"There's a mistake in {Path(filename).name}"
            + (f", line {line}" if line else "")
            + f": {text}.",
            file=filename,
            line=line,
            column=exc.offset or 0,
            detail=text,
        )
    except ValueError as exc:  # a null byte in the file
        return Problem(CODE, f"{Path(filename).name} can't be read as Python: {exc}.", filename)
    return None


def _first_line(output: str) -> str:
    return next((line.strip() for line in output.splitlines() if line.strip()), "")


def _matching_line(output: str, pattern: re.Pattern[str]) -> str:
    return next((line.strip() for line in output.splitlines() if pattern.search(line)), "")


#: Headers whose library goes by another name in the Library Manager.
_LIBRARY_NAMES = {
    "DHT.h": "DHT sensor library",
    "Adafruit_Sensor.h": "Adafruit Unified Sensor",
    "Adafruit_GFX.h": "Adafruit GFX Library",
    "Adafruit_BME280.h": "Adafruit BME280 Library",
    "LiquidCrystal_I2C.h": "LiquidCrystal I2C",
    "U8g2lib.h": "U8g2",
    "TinyGPS++.h": "TinyGPSPlus",
    "TinyGPSPlus.h": "TinyGPSPlus",
}


def library_candidates(header: str) -> list[str]:
    """Library Manager names worth trying for a missing ``#include``.

    Most libraries are named after their header (``Servo.h`` → Servo),
    many with spaces where the header has underscores
    (``Adafruit_NeoPixel.h`` → Adafruit NeoPixel).
    """
    name = Path(header).name
    stem = re.sub(r"\.h(?:pp)?$", "", name)
    found = [_LIBRARY_NAMES[name]] if name in _LIBRARY_NAMES else []
    for guess in (stem, stem.replace("_", " ")):
        if guess and guess not in found:
            found.append(guess)
    return found
