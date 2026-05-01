"""Tests for the Cobertura XML parser in ``core/coverage.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from polyglot_ai.core.coverage import (
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    files_intersecting,
    parse_coverage_xml,
)


# Minimal Cobertura XML — a single class with a mix of hits/misses
# and a partial branch line. Schema follows what coverage.py emits
# when given ``--cov-report=xml``.
_BASIC_XML = """<?xml version="1.0" ?>
<coverage version="6.0" timestamp="1700000000000" line-rate="0.8" branch-rate="0.5">
  <packages>
    <package name="src">
      <classes>
        <class filename="src/foo.py" name="foo">
          <lines>
            <line number="1" hits="3"/>
            <line number="2" hits="1"/>
            <line number="3" hits="0"/>
            <line number="4" hits="2" branch="true" condition-coverage="50% (1/2)"/>
            <line number="5" hits="2" branch="true" condition-coverage="100% (2/2)"/>
            <line number="6" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def _write_xml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "coverage.xml"
    p.write_text(content)
    return p


# ── Happy path ───────────────────────────────────────────────────────


def test_parses_basic_report(tmp_path):
    xml = _write_xml(tmp_path, _BASIC_XML)
    project = tmp_path
    (project / "src").mkdir()
    report = parse_coverage_xml(xml, project)

    assert isinstance(report, CoverageReport)
    assert report.line_rate == pytest.approx(0.8)
    assert report.timestamp_ms == 1_700_000_000_000

    abs_path = str((project / "src/foo.py").resolve())
    assert abs_path in report.files
    fc = report.files[abs_path]
    assert isinstance(fc, FileCoverage)
    assert fc.hit_lines == frozenset({1, 2, 4, 5})
    assert fc.miss_lines == frozenset({3, 6})
    # Partial: line 4 had branch coverage < 100%; line 5 was full.
    assert fc.partial_lines == frozenset({4})


def test_overall_pct_uses_line_rate(tmp_path):
    xml = _write_xml(tmp_path, _BASIC_XML)
    report = parse_coverage_xml(xml, tmp_path)
    assert report.overall_pct == pytest.approx(80.0)


def test_per_file_pct(tmp_path):
    xml = _write_xml(tmp_path, _BASIC_XML)
    report = parse_coverage_xml(xml, tmp_path)
    fc = next(iter(report.files.values()))
    # 4 hit / 6 total = 66.66...%
    assert fc.coverage_pct == pytest.approx(4 / 6 * 100)


# ── Edge cases ───────────────────────────────────────────────────────


def test_empty_file_reports_100_pct():
    """A file with no measurable lines is treated as 100% by convention."""
    fc = FileCoverage(path="/x", hit_lines=frozenset(), miss_lines=frozenset())
    assert fc.coverage_pct == 100.0


def test_missing_xml_raises(tmp_path):
    with pytest.raises(CoverageParseError, match="not found"):
        parse_coverage_xml(tmp_path / "nope.xml", tmp_path)


def test_malformed_xml_raises(tmp_path):
    p = _write_xml(tmp_path, "<not really xml")
    with pytest.raises(CoverageParseError, match="not valid XML"):
        parse_coverage_xml(p, tmp_path)


def test_wrong_root_raises(tmp_path):
    p = _write_xml(tmp_path, "<?xml version='1.0'?><other/>")
    with pytest.raises(CoverageParseError, match="unexpected root"):
        parse_coverage_xml(p, tmp_path)


def test_missing_line_rate_falls_back(tmp_path):
    xml = """<?xml version="1.0"?>
    <coverage>
      <packages><package><classes></classes></package></packages>
    </coverage>"""
    p = _write_xml(tmp_path, xml)
    report = parse_coverage_xml(p, tmp_path)
    assert report.line_rate == 0.0
    assert report.timestamp_ms is None


def test_non_numeric_hits_treated_as_miss(tmp_path):
    """A garbage hits attribute must not raise — treat as 0/miss."""
    xml = """<?xml version="1.0"?>
    <coverage line-rate="0.0">
      <packages><package><classes>
        <class filename="x.py" name="x">
          <lines><line number="1" hits="garbage"/></lines>
        </class>
      </classes></package></packages>
    </coverage>"""
    p = _write_xml(tmp_path, xml)
    report = parse_coverage_xml(p, tmp_path)
    fc = next(iter(report.files.values()))
    assert fc.miss_lines == frozenset({1})
    assert fc.hit_lines == frozenset()


def test_non_numeric_line_number_skipped(tmp_path):
    xml = """<?xml version="1.0"?>
    <coverage line-rate="1.0">
      <packages><package><classes>
        <class filename="x.py" name="x">
          <lines>
            <line number="abc" hits="1"/>
            <line number="2" hits="1"/>
          </lines>
        </class>
      </classes></package></packages>
    </coverage>"""
    p = _write_xml(tmp_path, xml)
    report = parse_coverage_xml(p, tmp_path)
    fc = next(iter(report.files.values()))
    assert fc.hit_lines == frozenset({2})


def test_multiple_classes_same_file_merge(tmp_path):
    """Cobertura emits one <class> per top-level class definition.

    Two classes in the same file must merge their hit/miss line sets
    rather than overwriting — otherwise the second class would
    obliterate the first's coverage."""
    xml = """<?xml version="1.0"?>
    <coverage line-rate="0.5">
      <packages><package><classes>
        <class filename="m.py" name="A">
          <lines><line number="1" hits="1"/><line number="2" hits="0"/></lines>
        </class>
        <class filename="m.py" name="B">
          <lines><line number="3" hits="1"/><line number="4" hits="0"/></lines>
        </class>
      </classes></package></packages>
    </coverage>"""
    p = _write_xml(tmp_path, xml)
    report = parse_coverage_xml(p, tmp_path)
    assert len(report.files) == 1
    fc = next(iter(report.files.values()))
    assert fc.hit_lines == frozenset({1, 3})
    assert fc.miss_lines == frozenset({2, 4})


def test_absolute_filename_handled(tmp_path):
    """Some emitters write absolute paths in @filename — accept them as-is."""
    abs_target = (tmp_path / "real.py").resolve()
    abs_target.write_text("# placeholder\n")
    xml = f"""<?xml version="1.0"?>
    <coverage line-rate="1.0">
      <packages><package><classes>
        <class filename="{abs_target}" name="real">
          <lines><line number="1" hits="1"/></lines>
        </class>
      </classes></package></packages>
    </coverage>"""
    p = _write_xml(tmp_path, xml)
    report = parse_coverage_xml(p, tmp_path)
    assert str(abs_target) in report.files


def test_files_intersecting_filters(tmp_path):
    xml = _write_xml(tmp_path, _BASIC_XML)
    report = parse_coverage_xml(xml, tmp_path)
    abs_match = str((tmp_path / "src/foo.py").resolve())
    abs_miss = str((tmp_path / "src/bar.py").resolve())
    out = files_intersecting(report, [abs_match, abs_miss])
    assert list(out.keys()) == [abs_match]


# ── FileCoverage invariants ──────────────────────────────────────────


def test_partial_is_subset_of_hit():
    """A partial line was hit — the parser must include it in hit_lines too.

    Otherwise the editor's "partial" overlay would paint over a line
    that isn't marked as hit, leaving a stripe with no green underneath.
    """
    fc = FileCoverage(
        path="/x",
        hit_lines=frozenset({4, 5}),
        miss_lines=frozenset(),
        partial_lines=frozenset({4}),
    )
    assert fc.partial_lines.issubset(fc.hit_lines)
