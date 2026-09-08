"""Startup phase timing.

``app.main()`` drops a mark after each startup phase; with
``POLYGLOT_AI_STARTUP_TIMING=1`` the report is logged (and printed)
once the window is up and post-show initialisation has finished.
``POLYGLOT_AI_EXIT_AFTER_STARTUP=1`` additionally quits right after
the report, and skips the modal first-run dialogs, so a cold start can
be measured from a script::

    POLYGLOT_AI_DATA_DIR=/tmp/pg-bench POLYGLOT_AI_STARTUP_TIMING=1 \\
    POLYGLOT_AI_EXIT_AFTER_STARTUP=1 QT_QPA_PLATFORM=offscreen polyglot-ai

Marks are always recorded (they cost a perf_counter call each) so the
report can also be pulled from a running app for bug reports.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Captured when this module is first imported, which app.py does
# before anything else so PyQt import cost lands in the first phase.
_IMPORT_T0 = time.perf_counter()


def timing_enabled() -> bool:
    return os.environ.get("POLYGLOT_AI_STARTUP_TIMING", "") not in ("", "0")


def exit_after_startup() -> bool:
    return os.environ.get("POLYGLOT_AI_EXIT_AFTER_STARTUP", "") not in ("", "0")


def process_age_seconds() -> float | None:
    """Seconds since the interpreter process started (Linux ``/proc`` only).

    Covers what ``perf_counter`` marks can't: interpreter boot and the
    imports that ran before this module. None where /proc is missing.
    """
    try:
        with open("/proc/self/stat", encoding="ascii") as fh:
            stat = fh.read()
        with open("/proc/uptime", encoding="ascii") as fh:
            uptime = float(fh.read().split()[0])
        # Field 22 (1-based) is starttime in clock ticks; the command
        # name in parens may contain spaces, so split after the ')'.
        fields = stat[stat.rindex(")") + 2 :].split()
        start_ticks = int(fields[19])
        return uptime - start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class StartupTimer:
    def __init__(self) -> None:
        self._t0 = _IMPORT_T0
        self._marks: list[tuple[str, float]] = []

    def mark(self, phase: str) -> None:
        self._marks.append((phase, time.perf_counter()))

    @property
    def marks(self) -> list[tuple[str, float]]:
        return list(self._marks)

    def phases(self) -> list[tuple[str, float]]:
        """(phase, seconds spent in it) — each phase ends at its mark."""
        out = []
        prev = self._t0
        for name, t in self._marks:
            out.append((name, t - prev))
            prev = t
        return out

    def total(self) -> float:
        return (self._marks[-1][1] - self._t0) if self._marks else 0.0

    def report(self) -> str:
        lines = ["Startup timing:"]
        for name, secs in self.phases():
            lines.append(f"  {secs * 1000:7.1f} ms  {name}")
        lines.append(f"  {self.total() * 1000:7.1f} ms  total since app import")
        age = process_age_seconds()
        if age is not None:
            lines.append(f"  {age * 1000:7.1f} ms  total since process start")
        return "\n".join(lines)

    def emit_report(self) -> None:
        text = self.report()
        logger.info("%s", text)
        if timing_enabled():
            print(text, flush=True)


startup_timer = StartupTimer()
