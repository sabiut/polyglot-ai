"""Discover and run pytest tests for the open project.

Provides a small async API used by the Tests panel:

* :func:`collect_tests` — runs ``pytest --collect-only -q`` and parses
  the output into a structured tree (file → class → test).
* :func:`run_tests` — runs ``pytest <node_id>`` (or the whole project)
  via ``subprocess.Popen`` so the output can be streamed line-by-line
  back to the UI.

Test results are reported through a small dataclass so the panel can
render pass/fail/skip status without re-parsing pytest output.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class TestNode:
    """A node in the discovered test tree.

    ``kind`` is one of ``"file" | "class" | "test"``. The pytest node id
    (e.g. ``tests/test_foo.py::TestBar::test_baz``) is stored on every
    node so any node can be passed straight to ``pytest`` for execution.
    """

    name: str  # Display name (basename for files, method name for tests)
    node_id: str  # Full pytest node identifier
    kind: str  # "file" | "class" | "test"
    file_path: str = ""  # Filesystem path for "file" nodes
    line: int | None = None  # Line number for "test" nodes (filled in on run)
    children: list["TestNode"] = field(default_factory=list)
    status: str = "unknown"  # "unknown" | "pass" | "fail" | "skip" | "error"
    output: str = ""  # Captured output from the most recent run


@dataclass
class CollectResult:
    """Result of running ``pytest --collect-only``."""

    ok: bool
    roots: list[TestNode] = field(default_factory=list)
    error: str | None = None  # Set when ok=False
    raw_output: str = ""


# Matches pytest --collect-only -q output lines like:
#   tests/test_foo.py::TestBar::test_baz
#   tests/test_foo.py::test_top_level
_NODE_LINE_RE = re.compile(r"^([^\s:]+\.py)((?:::[^\s:]+)*)$")


def _split_node_id(node_id: str) -> tuple[str, list[str]]:
    """Return ``(file, parts)`` where parts are the ``::``-separated tail."""
    if "::" not in node_id:
        return node_id, []
    head, _, tail = node_id.partition("::")
    return head, tail.split("::")


def _parse_collect_output(output: str) -> list[TestNode]:
    """Parse ``pytest --collect-only -q`` output into a tree.

    -q output is one node id per line followed by a summary line, e.g.::

        tests/test_a.py::test_one
        tests/test_a.py::TestB::test_two
        tests/test_b.py::test_three
        ============= 3 tests collected in 0.04s =============

    We build a file → class → test tree from those node ids.
    """
    files: dict[str, TestNode] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("=") or line.startswith("no tests"):
            continue
        match = _NODE_LINE_RE.match(line)
        if not match:
            continue

        file_path = match.group(1)
        # Skip the summary lines that match the regex by accident.
        if "::" not in line and not line.endswith(".py"):
            continue

        file_node = files.setdefault(
            file_path,
            TestNode(
                name=Path(file_path).name,
                node_id=file_path,
                kind="file",
                file_path=file_path,
            ),
        )

        _, parts = _split_node_id(line)
        if not parts:
            # File-level discovery line — not a runnable test, skip.
            continue

        if len(parts) == 1:
            # Top-level test function
            file_node.children.append(
                TestNode(
                    name=parts[0],
                    node_id=line,
                    kind="test",
                    file_path=file_path,
                )
            )
        else:
            # Nested under one or more classes. Pytest supports nested
            # classes; collapse them into a single class node by joining
            # all but the last segment.
            class_name = "::".join(parts[:-1])
            class_node_id = f"{file_path}::{class_name}"
            class_node = next(
                (c for c in file_node.children if c.kind == "class" and c.name == class_name),
                None,
            )
            if class_node is None:
                class_node = TestNode(
                    name=class_name,
                    node_id=class_node_id,
                    kind="class",
                    file_path=file_path,
                )
                file_node.children.append(class_node)
            class_node.children.append(
                TestNode(
                    name=parts[-1],
                    node_id=line,
                    kind="test",
                    file_path=file_path,
                )
            )

    return sorted(files.values(), key=lambda n: n.node_id)


async def collect_tests(project_root: Path) -> CollectResult:
    """Discover pytest tests in ``project_root``.

    Returns ``CollectResult`` carrying either a tree of test nodes or
    a structured error so the UI can show a friendly message.
    """
    if not project_root.is_dir():
        return CollectResult(ok=False, error=f"Project root does not exist: {project_root}")

    pytest_cmd = _find_pytest(project_root)
    if pytest_cmd is None:
        return CollectResult(
            ok=False,
            error=(
                "pytest is not installed. Install it with `pip install pytest` "
                "(or `uv pip install pytest` if you use uv) inside your project's "
                "virtual environment, then click Refresh."
            ),
        )

    cmd = [*pytest_cmd, "--collect-only", "-q", "--no-header"]
    logger.info("test_collector: running %s in %s", " ".join(cmd), project_root)
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except FileNotFoundError as e:
        logger.exception("test_collector: pytest binary not found")
        return CollectResult(ok=False, error=f"Could not launch pytest: {e}")
    except asyncio.TimeoutError:
        logger.error("test_collector: collection timed out after 60s")
        # Always kill the child so we don't leave a zombie pytest
        # process holding the venv lock.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return CollectResult(ok=False, error="Test collection timed out after 60 seconds.")
    except OSError as e:
        logger.exception("test_collector: OSError running pytest")
        return CollectResult(ok=False, error=str(e))

    raw = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")

    if proc.returncode not in (0, 5):
        # rc 5 = "no tests collected" — that's fine, return empty.
        # Anything else is a real error (config / import / syntax).
        msg = (err.strip() or raw.strip() or f"pytest exited with code {proc.returncode}")[:2000]
        return CollectResult(ok=False, error=msg, raw_output=raw + err)

    nodes = _parse_collect_output(raw)
    return CollectResult(ok=True, roots=nodes, raw_output=raw)


def _find_pytest(project_root: Path) -> list[str] | None:
    """Locate a pytest executable, preferring the project's venv."""
    # Check common venv locations first
    for venv_dir in (".venv", "venv", "env"):
        candidate = project_root / venv_dir / "bin" / "pytest"
        if candidate.is_file():
            return [str(candidate)]
        candidate = project_root / venv_dir / "Scripts" / "pytest.exe"  # Windows
        if candidate.is_file():
            return [str(candidate)]
    # Fall back to whatever's on PATH
    on_path = shutil.which("pytest")
    if on_path:
        return [on_path]
    # Last resort: python -m pytest
    py = shutil.which("python3") or shutil.which("python")
    if py:
        return [py, "-m", "pytest"]
    return None


@dataclass
class TestRunEvent:
    """Streamed line from a running pytest invocation.

    ``kind`` is ``"line"`` for normal output, ``"result"`` when a single
    test's pass/fail status can be parsed from the line, ``"summary"``
    for the final ``= N passed in 0.5s =`` line, and ``"coverage"``
    once after the run when ``with_coverage=True`` was passed and a
    Cobertura XML report could be parsed. The coverage payload is
    attached to ``payload`` to keep the dataclass backwards-compatible.
    """

    kind: str  # "line" | "result" | "summary" | "coverage"
    text: str
    node_id: str = ""
    status: str = ""  # for kind="result"
    payload: Any = None  # CoverageReport when kind="coverage"


# Matches pytest's verbose progress lines:
#   tests/test_foo.py::test_bar PASSED                                         [ 50%]
#   tests/test_foo.py::TestBaz::test_qux FAILED                                [100%]
_RESULT_RE = re.compile(r"^([^\s]+\.py(?:::[^\s]+)+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)")


async def run_tests(
    project_root: Path,
    node_id: str | None = None,
    extra_args: list[str] | None = None,
    with_coverage: bool = False,
) -> AsyncIterator[TestRunEvent]:
    """Run pytest and yield :class:`TestRunEvent` for each output line.

    ``node_id`` may be a file, class, or test (anything pytest accepts);
    when ``None``, runs the entire suite. Lines that match a per-test
    result get a ``kind="result"`` event so the panel can flip its
    status icon as soon as each test completes.

    When ``with_coverage`` is true we ask pytest to emit a Cobertura
    XML report into a temp file and parse it after the run. A final
    ``kind="coverage"`` event carries the parsed
    :class:`~polyglot_ai.core.coverage.CoverageReport` on its
    ``payload`` field. Coverage requires ``pytest-cov`` to be
    installed in the *project's* venv; if it isn't, pytest exits
    with a usage error and we surface that as a summary line —
    no ``kind="coverage"`` event is emitted in that case.
    """
    if not project_root.is_dir():
        yield TestRunEvent(kind="line", text=f"Project root does not exist: {project_root}")
        return

    pytest_cmd = _find_pytest(project_root)
    if pytest_cmd is None:
        yield TestRunEvent(
            kind="line",
            text="pytest is not installed in this project. See the panel for install instructions.",
        )
        return

    cmd = [*pytest_cmd, "-v", "--no-header", "--color=no", "-rN"]

    # Coverage XML goes to a per-run temp file. We don't reuse the
    # default ``coverage.xml`` because the user may run a parallel
    # ``pytest --cov`` from the terminal and we'd race on the file.
    cov_xml_path: Path | None = None
    if with_coverage:
        # ``delete=False`` so the file survives close(); we clean up
        # in the finally block. mkstemp would also work but
        # NamedTemporaryFile keeps the path-handling consistent.
        tmp = tempfile.NamedTemporaryFile(prefix="polyglot-cov-", suffix=".xml", delete=False)
        tmp.close()
        cov_xml_path = Path(tmp.name)
        cmd.extend(
            [
                "--cov",
                str(project_root),
                f"--cov-report=xml:{cov_xml_path}",
                # Suppress the terminal report — we already stream
                # pytest's normal output line-by-line and an extra
                # tabular summary just adds noise.
                "--cov-report=",
            ]
        )

    if extra_args:
        cmd.extend(extra_args)
    if node_id:
        cmd.append(node_id)

    logger.info("test_collector: running %s in %s", " ".join(cmd), project_root)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        logger.exception("test_collector: could not start pytest")
        yield TestRunEvent(kind="line", text=f"Could not start pytest: {e}")
        return

    assert proc.stdout is not None  # noqa: S101 — bufsize=1 + PIPE guarantees this
    loop = asyncio.get_running_loop()

    # Wrap the read loop in try/finally so the pytest child is always
    # killed if the async generator is abandoned (user clicks another
    # node mid-run, panel teardown, asyncio.CancelledError, etc.).
    # Without this we'd leak zombie pytest processes that hold the
    # venv lock until the app exits.
    try:
        while True:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if not line:
                break
            line = line.rstrip("\n")
            match = _RESULT_RE.match(line)
            if match:
                yield TestRunEvent(
                    kind="result",
                    text=line,
                    node_id=match.group(1),
                    status=match.group(2).lower(),
                )
            elif line.startswith("=") and ("passed" in line or "failed" in line or "error" in line):
                yield TestRunEvent(kind="summary", text=line)
            else:
                yield TestRunEvent(kind="line", text=line)
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                # Give it a moment to flush, then force-kill if needed.
                try:
                    await loop.run_in_executor(None, lambda: proc.wait(timeout=2))
                except subprocess.TimeoutExpired:
                    logger.warning("test_collector: pytest didn't exit cleanly, killing")
                    proc.kill()
                    await loop.run_in_executor(None, proc.wait)
            except (ProcessLookupError, OSError):
                pass

    rc = proc.returncode
    # rc 0 = success, 1 = tests failed, 5 = no tests collected (treat as ok).
    # Anything else (2 usage, 3 internal, 4 cli) is a real problem the user
    # should know about — surface it as a final summary line.
    if rc not in (0, 1, 5, None):
        yield TestRunEvent(
            kind="summary",
            text=f"pytest exited with non-zero status {rc}",
        )

    # Coverage post-processing. Done after rc is known so we don't
    # try to parse a half-written XML file when the user cancels mid-run.
    if cov_xml_path is not None:
        try:
            if rc in (0, 1, 5) and cov_xml_path.is_file() and cov_xml_path.stat().st_size > 0:
                # Local import — keeps coverage parsing optional
                # for callers that don't pass ``with_coverage=True``,
                # and avoids a circular import with editor wiring.
                from polyglot_ai.core.coverage import (
                    CoverageParseError,
                    parse_coverage_xml,
                )

                try:
                    report = parse_coverage_xml(cov_xml_path, project_root)
                except CoverageParseError as e:
                    yield TestRunEvent(
                        kind="summary",
                        text=f"Coverage parsing failed: {e}",
                    )
                else:
                    yield TestRunEvent(
                        kind="coverage",
                        text=f"Coverage: {report.overall_pct:.1f}% across {len(report.files)} file(s)",
                        payload=report,
                    )
        finally:
            try:
                cov_xml_path.unlink(missing_ok=True)
            except OSError:
                # Temp file cleanup is best-effort — the OS will reap
                # /tmp eventually. Worth a debug log so we notice if
                # something is keeping the file alive.
                logger.debug("test_collector: could not delete coverage XML temp file")
