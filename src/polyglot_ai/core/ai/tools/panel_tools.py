"""Tools that expose UI panel state to the AI.

These tools let the model read cross-panel state that isn't in the
filesystem — starting with the most recent code review. They are
standalone: no sandbox, no file_ops, no network. They read from
:mod:`polyglot_ai.core.panel_state`, which the panels populate.

Currently exposes:

- :func:`get_review_findings` — filter the most recent review by
  severity and/or file substring. The model should call this after
  seeing the ``PANEL STATE`` block in the system prompt when the
  user asks about specific findings.
"""

from __future__ import annotations

import json
from typing import Any


async def get_review_findings(args: dict) -> str:
    """Return filtered findings from the most recent code review.

    Arguments (all optional):

    - ``severity``: one of ``critical``, ``high``, ``medium``, ``low``,
      ``info``, or the shorthand ``high+`` which matches both
      ``critical`` and ``high``. Omit to return all severities.
    - ``file``: substring match against the finding's file path
      (case-sensitive). Useful for "tell me about issues in
      docker-compose.prod.yml".
    - ``limit``: max findings to return. Default 20, capped at 100.

    Returns a JSON string describing the current review state and the
    filtered findings. If no review has been run yet the result is
    ``{"available": false, ...}`` so the model can tell the user to
    run a review from the panel instead of hallucinating findings.
    """
    from polyglot_ai.core import panel_state

    review = panel_state.get_last_review()
    if not review:
        return json.dumps(
            {
                "available": False,
                "message": (
                    "No review has been run in this session. Ask the user "
                    "to pick a mode in the Code Review panel and click Run."
                ),
            }
        )

    severity = (args.get("severity") or "").strip().lower() or None
    file_filter = (args.get("file") or "").strip() or None

    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))

    findings: list[dict[str, Any]] = list(review.get("findings") or [])

    if severity == "high+":
        findings = [f for f in findings if f.get("severity") in ("critical", "high")]
    elif severity:
        findings = [f for f in findings if f.get("severity") == severity]

    if file_filter:
        findings = [f for f in findings if file_filter in (f.get("file") or "")]

    truncated = len(findings) > limit
    findings = findings[:limit]

    return json.dumps(
        {
            "available": True,
            "mode": review.get("mode"),
            "status": review.get("status"),
            "files_scanned": review.get("files") or [],
            "counts": review.get("counts") or {},
            "total": review.get("total", 0),
            "returned": len(findings),
            "truncated": truncated,
            "findings": findings,
            "model": review.get("model") or "",
            "provider": review.get("provider") or "",
            "timestamp": review.get("timestamp"),
        },
        indent=2,
    )
