"""Editor diagnostics — lint/parse errors for the file being edited.

Python goes through ``ruff`` (fast, no config needed, respects the
project's own ``[tool.ruff]`` when there is one). JSON and YAML get
parse errors from the standard parsers. Everything runs on the text
in the editor buffer — not the file on disk — so problems show up as
you type, before you save.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PYTHON_SUFFIXES = {".py", ".pyi", ".pyw"}
_JSON_SUFFIXES = {".json"}
_YAML_SUFFIXES = {".yaml", ".yml"}

# Rule prefixes that mean "this code is broken", not "this could be
# tidier". Everything else ruff reports is shown as a warning.
_ERROR_CODES = ("invalid-syntax", "syntax-error", "F82", "F81", "E9", "PLE")


@dataclass(frozen=True)
class Diagnostic:
    """One problem. ``line``/``col`` are 1-based like editors show them."""

    line: int
    col: int
    end_line: int
    end_col: int
    code: str
    message: str
    severity: str  # "error" | "warning"
    fixable: bool = False

    @property
    def title(self) -> str:
        return f"{self.code}: {self.message}" if self.code else self.message


def supported(filename: str | Path | None) -> bool:
    if not filename:
        return False
    suffix = Path(filename).suffix.lower()
    if suffix in _PYTHON_SUFFIXES:
        return find_ruff() is not None
    return suffix in _JSON_SUFFIXES or suffix in _YAML_SUFFIXES


def find_ruff() -> str | None:
    """Locate ``ruff``: on PATH, or alongside the running interpreter.

    The packaged app's venv isn't on PATH, but its ``bin/`` holds the
    ``ruff`` console script from the wheel dependency.
    """
    found = shutil.which("ruff")
    if found:
        return found
    candidate = Path(sys.executable).parent / ("ruff.exe" if sys.platform == "win32" else "ruff")
    return str(candidate) if candidate.exists() else None


def _severity_for(code: str, reported: str | None) -> str:
    # ruff labels *every* lint violation "error" in its JSON output, so
    # its label is only useful when it's telling us to downgrade.
    if reported == "warning":
        return "warning"
    return "error" if code.startswith(_ERROR_CODES) else "warning"


def ruff_diagnostics(text: str, filename: str | Path, timeout: int = 15) -> list[Diagnostic]:
    """Run ``ruff check`` over ``text`` as if it were ``filename``."""
    ruff = find_ruff()
    if ruff is None:
        return []
    path = Path(filename)
    cwd = path.parent if path.is_absolute() and path.parent.is_dir() else None
    cmd = [
        ruff,
        "check",
        "--output-format",
        "json",
        "--no-cache",
        "--stdin-filename",
        str(path),
        "-",
    ]
    try:
        result = subprocess.run(
            cmd, input=text, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ruff check failed to run: %s", exc)
        return []
    # Exit 1 just means "violations found"; anything else is ruff itself failing.
    if result.returncode not in (0, 1) or not result.stdout.strip():
        if result.returncode not in (0, 1):
            logger.debug("ruff exited %s: %s", result.returncode, result.stderr[:200])
        return []
    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    diags: list[Diagnostic] = []
    for item in items:
        loc = item.get("location") or {}
        end = item.get("end_location") or loc
        code = item.get("code") or ""
        diags.append(
            Diagnostic(
                line=int(loc.get("row", 1)),
                col=int(loc.get("column", 1)),
                end_line=int(end.get("row", loc.get("row", 1))),
                end_col=int(end.get("column", loc.get("column", 1))),
                code=code,
                message=str(item.get("message", "")),
                severity=_severity_for(code, item.get("severity")),
                fixable=bool(item.get("fix")),
            )
        )
    return diags


def ruff_autofix(text: str, filename: str | Path, timeout: int = 15) -> str | None:
    """Return ``text`` with ruff's safe fixes applied, or None if nothing changed."""
    ruff = find_ruff()
    if ruff is None:
        return None
    path = Path(filename)
    cwd = path.parent if path.is_absolute() and path.parent.is_dir() else None
    cmd = [ruff, "check", "--fix-only", "--no-cache", "--stdin-filename", str(path), "-"]
    try:
        result = subprocess.run(
            cmd, input=text, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode not in (0, 1) or not result.stdout:
        return None
    fixed = result.stdout
    return fixed if fixed != text else None


def json_diagnostics(text: str) -> list[Diagnostic]:
    if not text.strip():
        return []
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return [
            Diagnostic(
                line=exc.lineno,
                col=exc.colno,
                end_line=exc.lineno,
                end_col=exc.colno + 1,
                code="json",
                message=exc.msg,
                severity="error",
            )
        ]
    return []


def yaml_diagnostics(text: str) -> list[Diagnostic]:
    if not text.strip():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = (mark.line + 1) if mark is not None else 1
        col = (mark.column + 1) if mark is not None else 1
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        return [
            Diagnostic(
                line=line,
                col=col,
                end_line=line,
                end_col=col + 1,
                code="yaml",
                message=str(problem),
                severity="error",
            )
        ]
    return []


def collect(filename: str | Path | None, text: str) -> list[Diagnostic]:
    """Diagnostics for ``text`` as the file ``filename`` (by suffix)."""
    if not filename:
        return []
    suffix = Path(filename).suffix.lower()
    if suffix in _PYTHON_SUFFIXES:
        return ruff_diagnostics(text, filename)
    if suffix in _JSON_SUFFIXES:
        return json_diagnostics(text)
    if suffix in _YAML_SUFFIXES:
        return yaml_diagnostics(text)
    return []
