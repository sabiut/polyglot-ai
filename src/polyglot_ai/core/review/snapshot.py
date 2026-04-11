"""Project a ``ReviewResult`` into a plain-dict snapshot.

The snapshot is the single piece of data that travels out of the
review panel and into the AI-facing layer. Keeping it as a plain
dict of primitives (no enums, no dataclasses) means:

- It can be stored in :mod:`polyglot_ai.core.panel_state` without
  pulling ``review.models`` into that module's import graph.
- The ``get_review_findings`` tool can ``json.dumps`` it verbatim.
- :class:`polyglot_ai.core.ai.context.ContextBuilder` can render a
  compact ``--- PANEL STATE ---`` block without touching the
  ``Severity`` / ``Category`` enums.
- Tests are trivial — no Qt, no database, no network.

See :mod:`polyglot_ai.core.panel_state` for how the snapshot is
shared between the system-prompt builder and the drill-down tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Severity ordering used when sorting findings for stable rendering.
# Lower number = more severe — unknown severities sort last.
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def build_review_snapshot(
    result: Any,
    mode: str,
    files_scanned: list[str] | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe snapshot dict from a ``ReviewResult``.

    Duck-typed on purpose: any object with ``findings``, ``status``,
    ``model``, ``provider``, ``summary`` attributes works, which keeps
    the tests from having to construct full ``ReviewResult`` instances.

    Parameters
    ----------
    result:
        The review result returned by ``ReviewEngine.review_diff`` or
        ``ReviewEngine.review_content``.
    mode:
        The review mode label (e.g. ``"working"``, ``"branch"``,
        ``"dockerfile"``, ``"docker_compose"``). This is what the AI
        sees and matches the UI dropdown.
    files_scanned:
        Files the review actually touched. For diff modes the panel
        passes ``[]`` because the files list is embedded in the diff
        itself and surfacing it here isn't useful.
    """
    findings: list[dict[str, Any]] = []
    for f in getattr(result, "findings", None) or []:
        severity = str(getattr(getattr(f, "severity", None), "value", "") or "info")
        category = str(getattr(getattr(f, "category", None), "value", "") or "other")
        findings.append(
            {
                "file": str(getattr(f, "file", "") or ""),
                "line": int(getattr(f, "line", 0) or 0),
                "severity": severity,
                "category": category,
                "title": str(getattr(f, "title", "") or ""),
                "body": str(getattr(f, "body", "") or ""),
                "suggestion": getattr(f, "suggestion", None),
            }
        )

    # Stable ordering: most severe first, then by file/line so tests
    # and the rendered prompt don't flap on re-runs.
    findings.sort(key=lambda x: (_SEV_ORDER.get(x["severity"], 99), x["file"], x["line"]))

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = f["severity"]
        if sev in counts:
            counts[sev] += 1

    return {
        "mode": mode,
        "files": list(files_scanned or []),
        "findings": findings,
        "counts": counts,
        "total": len(findings),
        "status": str(getattr(result, "status", "ok") or "ok"),
        "model": str(getattr(result, "model", "") or ""),
        "provider": str(getattr(result, "provider", "") or ""),
        "summary": str(getattr(result, "summary", "") or ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
