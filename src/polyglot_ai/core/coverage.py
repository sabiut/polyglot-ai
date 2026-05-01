"""Cobertura XML coverage parser.

The Tests panel optionally runs ``pytest --cov --cov-report=xml`` and
hands the resulting ``coverage.xml`` to :func:`parse_coverage_xml`.
The output is a :class:`CoverageReport` keyed by absolute file path,
where each entry tells the editor which lines were *hit*, *missed*,
or *partial* (a branch took only some of its targets).

We pick Cobertura over the binary ``.coverage`` SQLite format on
purpose: it's what ``coverage.py`` writes when you ask for XML, the
schema is stable, and we don't have to depend on the ``coverage``
library at runtime — only stdlib XML parsing. Tests still need
``pytest-cov`` installed in the *project's* venv, but the editor
itself stays lightweight.

What we do *not* do:
* No branch-level annotations beyond the binary "partial" flag —
  rendering arrows for "this branch went left only" is interesting
  but quickly gets visually noisy in the gutter.
* No diff-based blame across runs — each parse is a snapshot.
* No statement-vs-branch reconciliation when a tool emits both
  ``hits`` *and* ``branch="true"`` on the same line; we trust
  ``hits`` and downgrade to "partial" only when the branch summary
  says so.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileCoverage:
    """Per-file coverage outcome.

    ``hit_lines`` and ``miss_lines`` are mutually exclusive. ``partial_lines``
    is a subset of the lines that were nominally hit but had a branch
    that didn't take all of its outgoing edges — the editor paints them
    in a different colour so the user can spot half-tested conditionals.
    """

    path: str  # absolute path; the parser resolves relative paths against project_root
    hit_lines: frozenset[int]
    miss_lines: frozenset[int]
    partial_lines: frozenset[int] = field(default_factory=frozenset)

    @property
    def coverage_pct(self) -> float:
        """Line coverage percentage in [0, 100]. Empty files report 100.0."""
        total = len(self.hit_lines) + len(self.miss_lines)
        if total == 0:
            # An empty file reports 100% by convention so the dashboard
            # doesn't show a misleading 0% for header-only modules.
            return 100.0
        return 100.0 * len(self.hit_lines) / total


@dataclass(frozen=True)
class CoverageReport:
    """Aggregate coverage across all files in a single pytest run."""

    files: dict[str, FileCoverage]
    line_rate: float  # 0..1, as reported by Cobertura's <coverage line-rate=...>
    timestamp_ms: int | None  # may be None on emitters that omit it

    @property
    def overall_pct(self) -> float:
        """Convenience for the Tests-panel header label."""
        return 100.0 * self.line_rate


def parse_coverage_xml(xml_path: Path, project_root: Path) -> CoverageReport:
    """Read a Cobertura XML report and return per-file coverage.

    ``project_root`` is needed because Cobertura paths are relative to
    the directory pytest was invoked from; we resolve them to
    absolute paths so the editor doesn't have to repeat that logic
    when looking up the open buffer.

    Raises :class:`CoverageParseError` on malformed input — callers
    should surface it as a Tests-panel summary line rather than
    crashing the run.
    """
    if not xml_path.is_file():
        raise CoverageParseError(f"coverage XML not found: {xml_path}")

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        raise CoverageParseError(f"coverage XML is not valid XML: {e}") from e

    root = tree.getroot()
    if root.tag != "coverage":
        raise CoverageParseError(f"unexpected root element <{root.tag}>; expected <coverage>")

    # ``line-rate`` is a 0..1 float in the schema; some generators omit
    # it on empty reports (no Python files were collected) — treat
    # missing as zero so the panel label stays informative.
    line_rate = _safe_float(root.get("line-rate"), default=0.0)
    ts_attr = root.get("timestamp")
    timestamp_ms = int(ts_attr) if ts_attr and ts_attr.isdigit() else None

    files: dict[str, FileCoverage] = {}
    for cls in root.iter("class"):
        # ``filename`` is the path Cobertura emits — Cobertura calls
        # files "classes" for legacy Java reasons. ``name`` is a
        # dotted-module form which we don't need.
        rel = cls.get("filename")
        if not rel:
            continue
        absolute = _resolve(project_root, rel)
        hits, misses, partials = _parse_lines(cls)
        # Multiple <class> entries may share a filename in Python (one
        # per top-level class definition). Merge by union.
        existing = files.get(absolute)
        if existing is None:
            files[absolute] = FileCoverage(
                path=absolute,
                hit_lines=frozenset(hits),
                miss_lines=frozenset(misses),
                partial_lines=frozenset(partials),
            )
        else:
            files[absolute] = FileCoverage(
                path=absolute,
                hit_lines=existing.hit_lines | hits,
                miss_lines=existing.miss_lines | misses,
                partial_lines=existing.partial_lines | partials,
            )

    return CoverageReport(files=files, line_rate=line_rate, timestamp_ms=timestamp_ms)


# ── internals ────────────────────────────────────────────────────────


class CoverageParseError(Exception):
    """Raised when a Cobertura XML file is missing or malformed."""


def _resolve(project_root: Path, rel_or_abs: str) -> str:
    """Resolve a Cobertura filename to an absolute string path.

    Cobertura paths are usually relative to pytest's invocation
    directory but some generators emit absolute paths already. We
    handle both. ``str()`` is intentional — the rest of the app keys
    coverage by string path to interop with editor buffers, which
    track open files by their string path too.
    """
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = (project_root / p).resolve()
    else:
        # Even absolute paths get a ``resolve()`` so symlinked checkouts
        # match the editor's resolved buffer paths.
        p = p.resolve()
    return str(p)


def _parse_lines(class_el: ET.Element) -> tuple[set[int], set[int], set[int]]:
    """Pull hit/miss/partial line sets out of a single ``<class>`` element."""
    hits: set[int] = set()
    misses: set[int] = set()
    partials: set[int] = set()
    for line in class_el.iter("line"):
        n = line.get("number")
        if n is None or not n.isdigit():
            continue
        lineno = int(n)
        # ``hits="0"`` → not executed. Anything ≥1 → executed at least
        # once. The schema sometimes uses non-integer count strings on
        # very old emitters, so ``_safe_int`` falls back to 0.
        hit_count = _safe_int(line.get("hits"), default=0)
        if hit_count <= 0:
            misses.add(lineno)
            continue
        hits.add(lineno)
        # Branch coverage: ``branch="true"`` plus ``condition-coverage``
        # like "50% (1/2)" means the line executed but only half its
        # outgoing branches did. Demote to "partial".
        if line.get("branch") == "true":
            cc = line.get("condition-coverage", "")
            if cc and not cc.startswith("100%"):
                partials.add(lineno)
    return hits, misses, partials


def _safe_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def files_intersecting(report: CoverageReport, paths: Iterable[str]) -> dict[str, FileCoverage]:
    """Return only the coverage entries matching one of ``paths``.

    Convenience for the editor: when applying coverage to open tabs,
    we only need the subset matching the buffers that are actually
    visible. Missing entries map to "no data" (no markers shown).
    """
    wanted = {str(Path(p).resolve()) for p in paths}
    return {p: fc for p, fc in report.files.items() if p in wanted}
