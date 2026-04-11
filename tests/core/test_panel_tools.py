"""Tests for ``get_review_findings`` — the AI-facing drill-down tool.

The tool reads from ``panel_state`` and returns JSON, so the model
can filter the most recent review by severity and/or file without
blowing the system-prompt budget. These tests pin:

- available=false when nothing has been published
- no filter → everything, sorted as stored
- severity filter including the ``high+`` shorthand
- file substring filter
- limit clamping (default 20, max 100, min 1)
- truncated flag honest when more findings exist than limit
- bad/missing args don't raise
"""

from __future__ import annotations

import json

import pytest

from polyglot_ai.core import panel_state
from polyglot_ai.core.ai.tools.panel_tools import get_review_findings


@pytest.fixture(autouse=True)
def _reset_panel_state():
    panel_state.clear()
    yield
    panel_state.clear()


def _make_snapshot(findings):
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return {
        "mode": "docker_compose",
        "files": ["docker-compose.yml", "docker-compose.prod.yml"],
        "findings": findings,
        "counts": counts,
        "total": len(findings),
        "status": "ok",
        "model": "claude-opus-4-6",
        "provider": "anthropic",
        "summary": "",
        "timestamp": "2026-04-10T12:00:00+00:00",
    }


def _f(file, line, severity, title):
    return {
        "file": file,
        "line": line,
        "severity": severity,
        "category": "security",
        "title": title,
        "body": "",
        "suggestion": None,
    }


@pytest.mark.asyncio
async def test_no_review_returns_available_false():
    out = json.loads(await get_review_findings({}))
    assert out["available"] is False
    assert "no review" in out["message"].lower()


@pytest.mark.asyncio
async def test_no_filter_returns_all_findings_with_metadata():
    panel_state.set_last_review(
        _make_snapshot(
            [
                _f("docker-compose.yml", 14, "critical", "privileged: true"),
                _f("docker-compose.prod.yml", 22, "critical", "POSTGRES_PASSWORD hardcoded"),
                _f("docker-compose.yml", 31, "high", "docker.sock mounted"),
            ]
        )
    )

    out = json.loads(await get_review_findings({}))
    assert out["available"] is True
    assert out["mode"] == "docker_compose"
    assert out["files_scanned"] == ["docker-compose.yml", "docker-compose.prod.yml"]
    assert out["total"] == 3
    assert out["returned"] == 3
    assert out["truncated"] is False
    assert out["counts"]["critical"] == 2
    assert len(out["findings"]) == 3


@pytest.mark.asyncio
async def test_severity_filter_exact_match():
    panel_state.set_last_review(
        _make_snapshot(
            [
                _f("a.yml", 1, "critical", "x"),
                _f("b.yml", 2, "high", "y"),
                _f("c.yml", 3, "medium", "z"),
            ]
        )
    )

    out = json.loads(await get_review_findings({"severity": "high"}))
    assert out["returned"] == 1
    assert out["findings"][0]["severity"] == "high"
    assert out["total"] == 3  # unchanged — total reflects full review


@pytest.mark.asyncio
async def test_severity_high_plus_matches_critical_and_high():
    panel_state.set_last_review(
        _make_snapshot(
            [
                _f("a.yml", 1, "critical", "x"),
                _f("b.yml", 2, "high", "y"),
                _f("c.yml", 3, "medium", "z"),
                _f("d.yml", 4, "low", "q"),
            ]
        )
    )

    out = json.loads(await get_review_findings({"severity": "high+"}))
    assert out["returned"] == 2
    sevs = sorted(f["severity"] for f in out["findings"])
    assert sevs == ["critical", "high"]


@pytest.mark.asyncio
async def test_file_substring_filter():
    panel_state.set_last_review(
        _make_snapshot(
            [
                _f("docker-compose.yml", 1, "high", "x"),
                _f("docker-compose.prod.yml", 2, "high", "y"),
                _f("docker-compose.dev.yml", 3, "high", "z"),
            ]
        )
    )

    out = json.loads(await get_review_findings({"file": "prod"}))
    assert out["returned"] == 1
    assert out["findings"][0]["file"] == "docker-compose.prod.yml"


@pytest.mark.asyncio
async def test_combined_severity_and_file_filters():
    panel_state.set_last_review(
        _make_snapshot(
            [
                _f("docker-compose.prod.yml", 1, "critical", "x"),
                _f("docker-compose.prod.yml", 2, "low", "y"),
                _f("docker-compose.yml", 3, "critical", "z"),
            ]
        )
    )

    out = json.loads(await get_review_findings({"severity": "critical", "file": "prod"}))
    assert out["returned"] == 1
    f = out["findings"][0]
    assert f["severity"] == "critical"
    assert f["file"] == "docker-compose.prod.yml"


@pytest.mark.asyncio
async def test_limit_clamps_and_marks_truncated():
    findings = [_f(f"a{i}.yml", i, "high", f"t{i}") for i in range(30)]
    panel_state.set_last_review(_make_snapshot(findings))

    out = json.loads(await get_review_findings({"limit": 5}))
    assert out["returned"] == 5
    assert out["truncated"] is True
    assert out["total"] == 30


@pytest.mark.asyncio
async def test_limit_upper_bound_is_100():
    findings = [_f(f"a{i}.yml", i, "info", f"t{i}") for i in range(150)]
    panel_state.set_last_review(_make_snapshot(findings))

    out = json.loads(await get_review_findings({"limit": 999}))
    assert out["returned"] == 100
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_invalid_limit_falls_back_to_default():
    findings = [_f(f"a{i}.yml", i, "info", f"t{i}") for i in range(25)]
    panel_state.set_last_review(_make_snapshot(findings))

    out = json.loads(await get_review_findings({"limit": "not a number"}))
    assert out["returned"] == 20  # default


@pytest.mark.asyncio
async def test_limit_at_least_one():
    findings = [_f("a.yml", 1, "info", "t")]
    panel_state.set_last_review(_make_snapshot(findings))

    out = json.loads(await get_review_findings({"limit": 0}))
    assert out["returned"] == 1
