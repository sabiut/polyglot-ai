"""Tests for ``build_review_snapshot``.

The snapshot is the single payload the review panel publishes to
``panel_state``, consumed by both the system-prompt builder and the
``get_review_findings`` tool. These tests pin:

- severity sort order (critical → high → medium → low → info)
- counts are derived from findings, not trusted from the result
- duck-typed result objects work (no hard dependency on ReviewResult)
- JSON-safe: no enums, no dataclasses, only primitives
- timestamp is populated
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from polyglot_ai.core.review.snapshot import build_review_snapshot


def _sev(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _finding(file, line, severity, title, category="security", body="", suggestion=None):
    return SimpleNamespace(
        file=file,
        line=line,
        severity=_sev(severity),
        category=_sev(category),
        title=title,
        body=body,
        suggestion=suggestion,
    )


def _result(findings, status="ok", model="", provider="", summary=""):
    return SimpleNamespace(
        findings=findings,
        status=status,
        model=model,
        provider=provider,
        summary=summary,
    )


def test_empty_result_builds_clean_snapshot():
    snap = build_review_snapshot(_result([]), "working", [])
    assert snap["mode"] == "working"
    assert snap["files"] == []
    assert snap["findings"] == []
    assert snap["total"] == 0
    assert snap["counts"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }
    assert snap["status"] == "ok"
    assert snap["timestamp"]  # populated
    # JSON-safe end to end
    json.dumps(snap)


def test_findings_sorted_by_severity_then_file_then_line():
    findings = [
        _finding("z.yml", 10, "low", "cosmetic"),
        _finding("a.yml", 5, "critical", "hardcoded password"),
        _finding("b.yml", 3, "high", "privileged container"),
        _finding("a.yml", 1, "critical", "docker socket mounted"),
        _finding("c.yml", 20, "medium", "no healthcheck"),
    ]
    snap = build_review_snapshot(
        _result(findings),
        "docker_compose",
        ["a.yml", "b.yml", "c.yml", "z.yml"],
    )

    order = [(f["severity"], f["file"], f["line"]) for f in snap["findings"]]
    assert order == [
        ("critical", "a.yml", 1),
        ("critical", "a.yml", 5),
        ("high", "b.yml", 3),
        ("medium", "c.yml", 20),
        ("low", "z.yml", 10),
    ]


def test_counts_derived_from_findings():
    findings = [
        _finding("a", 1, "critical", "x"),
        _finding("a", 2, "critical", "y"),
        _finding("b", 1, "high", "z"),
        _finding("c", 1, "low", "q"),
        _finding("d", 1, "info", "r"),
    ]
    snap = build_review_snapshot(_result(findings), "dockerfile", [])
    assert snap["counts"] == {
        "critical": 2,
        "high": 1,
        "medium": 0,
        "low": 1,
        "info": 1,
    }
    assert snap["total"] == 5


def test_snapshot_is_json_safe_no_enums_leaked():
    findings = [_finding("a.tf", 42, "high", "public s3 bucket", category="security")]
    snap = build_review_snapshot(_result(findings, model="x", provider="y"), "terraform", ["a.tf"])
    payload = json.dumps(snap)  # must not raise
    # Severity/category made it through as plain strings
    decoded = json.loads(payload)
    f = decoded["findings"][0]
    assert f["severity"] == "high"
    assert f["category"] == "security"
    assert decoded["model"] == "x"
    assert decoded["provider"] == "y"
    assert decoded["mode"] == "terraform"
    assert decoded["files"] == ["a.tf"]


def test_missing_attributes_use_safe_defaults():
    """Duck-typed result with no findings attr and sparse fields."""
    minimal = SimpleNamespace()
    snap = build_review_snapshot(minimal, "kubernetes", None)
    assert snap["findings"] == []
    assert snap["files"] == []
    assert snap["status"] == "ok"
    assert snap["model"] == ""
    assert snap["provider"] == ""


def test_failed_status_is_preserved():
    snap = build_review_snapshot(_result([], status="failed"), "working", [])
    assert snap["status"] == "failed"
