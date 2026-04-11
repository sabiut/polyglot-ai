"""Shared in-memory snapshots of UI panel state.

Panels publish compact, plain-dict summaries of their current state
here so the AI can see them WITHOUT each panel having to hold a
reference to the context builder or the tool registry.

Current consumers:

- :class:`polyglot_ai.core.ai.context.ContextBuilder` reads
  :func:`get_last_review` when assembling the system prompt and
  renders a ``--- PANEL STATE ---`` block. This is what lets the
  chat model answer questions like "what did the last review find?"
  without the user pasting anything.

- ``core/ai/tools/panel_tools.py`` exposes
  :func:`polyglot_ai.core.ai.tools.panel_tools.get_review_findings`,
  which reads the same snapshot to return filtered findings on
  demand. The snapshot is the single source of truth, so the summary
  the model sees and the drill-down the tool returns cannot drift.

Publishers:

- :class:`polyglot_ai.ui.panels.review_panel.ReviewPanel` calls
  :func:`set_last_review` at the end of ``_run_review`` with a dict
  built by :func:`polyglot_ai.core.review.snapshot.build_review_snapshot`.

This module is deliberately tiny and has no imports from ``core.ai``
or ``ui`` so any module can depend on it without creating import
cycles. State is process-wide (the app is single-process) and
guarded by a lock so Qt and asyncio workers can't race.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_lock = Lock()
_last_review: dict[str, Any] | None = None
_last_workflow_run: dict[str, Any] | None = None


def set_last_review(snapshot: dict[str, Any] | None) -> None:
    """Publish the most recent review snapshot.

    Pass ``None`` to clear. Callers should build the dict via
    :func:`polyglot_ai.core.review.snapshot.build_review_snapshot` so
    the shape stays consistent across publishers.
    """
    global _last_review
    with _lock:
        _last_review = snapshot


def get_last_review() -> dict[str, Any] | None:
    """Return a shallow copy of the most recent review snapshot, or ``None``.

    Returns a copy so consumers cannot accidentally mutate the shared
    state. The lock only guards the pointer read — post-read mutation
    of the original dict would be a data race without this copy.
    """
    with _lock:
        return dict(_last_review) if _last_review is not None else None


def set_last_workflow_run(snapshot: dict[str, Any] | None) -> None:
    """Publish the most recent workflow run result."""
    global _last_workflow_run
    with _lock:
        _last_workflow_run = snapshot


def get_last_workflow_run() -> dict[str, Any] | None:
    """Return a shallow copy of the last workflow run, or ``None``."""
    with _lock:
        return dict(_last_workflow_run) if _last_workflow_run is not None else None


def clear() -> None:
    """Reset all panel state. Primarily used by tests."""
    global _last_review, _last_workflow_run
    with _lock:
        _last_review = None
        _last_workflow_run = None
