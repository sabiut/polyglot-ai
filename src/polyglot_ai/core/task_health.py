"""Task health synthesis — a pure function that turns raw task fields
into a small :class:`HealthSummary` the UI can render as a badge.

Kept deliberately side-effect free and off the :class:`Task` dataclass
so:

- Unit tests can drive it without touching the store or the event bus.
- Every view (sidebar card, Today panel, detail dialog) shares the
  same derivation logic — no "but the sidebar disagrees with Today"
  bugs.
- Stale detection is driven by a ``now`` parameter so tests don't
  need ``time.sleep``.

The raw data (``last_test_run``, ``last_ci_run``, ``state``, ``branch``,
``updated_at``, ``pr_url``, ``blocked_reason``) is already tracked on
:class:`Task`. This module just ranks it into five buckets by
priority and returns a short human label.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from polyglot_ai.core.tasks import Task, TaskState

# A task with no activity for longer than this counts as stale. Kept
# generous on purpose — shorter thresholds create false alarms for
# tasks that park overnight while you're doing other work.
_STALE_PLANNING_SECONDS = 3 * 24 * 3600  # 3 days
_STALE_ACTIVE_SECONDS = 5 * 24 * 3600  # 5 days
_STALE_REVIEW_SECONDS = 2 * 24 * 3600  # 2 days


class HealthLevel(str, Enum):
    """Coarse health buckets, ordered roughly by "does the user need to act".

    The values are strings so the enum is stable across serialization
    boundaries (events, logs, future JSON exports).
    """

    HEALTHY = "healthy"  # ACTIVE with passing tests / green CI, nothing to worry about
    IN_REVIEW = "in_review"  # PR open, waiting on reviewers or CI
    NEEDS_ATTENTION = "needs_attention"  # Failing tests, failing CI, missing branch, etc.
    BLOCKED = "blocked"  # Explicitly blocked — surfaced with the reason
    STALE = "stale"  # No updates for a long time in a non-terminal state


@dataclass(frozen=True)
class HealthSummary:
    """Structured health summary for a single task.

    ``level`` is the computed bucket.
    ``label`` is a short human-readable string suitable for the
        Today attention list (e.g. ``"Blocked: waiting on reviewer"``
        or ``"2 test(s) failing"``) — it can be long-ish.
    ``badge`` is a very short word or two intended for a fixed-width
        chip in the sidebar card. Never embeds user-supplied text, so
        a 200-char blocker reason can't overflow the card layout.
    ``reason`` is a longer explanation suitable for a tooltip.
    ``attention`` is True when the task wants the user's eyeballs
        (used by Today's attention list).
    """

    level: HealthLevel
    label: str
    reason: str
    attention: bool
    # Short (≤ 12 chars ideally) label for the sidebar card chip.
    # Optional so existing ``HealthSummary(...)`` construction in
    # tests still works; defaults to the level's title-case name.
    badge: str = ""

    def __post_init__(self) -> None:
        # ``frozen=True`` means we can't just assign — use object
        # __setattr__ to populate a default without forcing every
        # call site to pass ``badge=...``.
        if not self.badge:
            object.__setattr__(self, "badge", _DEFAULT_BADGES[self.level])

    @property
    def colour(self) -> str:
        """Hex colour matching the level — used by the card badge.

        Kept here so every UI surface picks the same colour without
        reimplementing the mapping.
        """
        return _LEVEL_COLOURS[self.level]


_LEVEL_COLOURS: dict[HealthLevel, str] = {
    HealthLevel.HEALTHY: "#4ec9b0",
    HealthLevel.IN_REVIEW: "#9cdcfe",
    HealthLevel.NEEDS_ATTENTION: "#e5a00d",
    HealthLevel.BLOCKED: "#f44747",
    HealthLevel.STALE: "#888888",
}

# Fallback short-form labels used when ``compute_health`` doesn't
# supply a more specific one (e.g. ``"Stale 4d"``). Kept intentionally
# terse so the sidebar chip has a predictable width.
_DEFAULT_BADGES: dict[HealthLevel, str] = {
    HealthLevel.HEALTHY: "OK",
    HealthLevel.IN_REVIEW: "Review",
    HealthLevel.NEEDS_ATTENTION: "Failing",
    HealthLevel.BLOCKED: "Blocked",
    HealthLevel.STALE: "Stale",
}


def compute_health(task: Task, now: float | None = None) -> HealthSummary:
    """Derive the :class:`HealthSummary` for ``task``.

    ``now`` is injectable so tests can exercise the stale-detection
    branches deterministically without monkey-patching ``time.time``.
    Falls back to ``time.time()`` at call time when omitted.

    Priority order (highest first):

    1. Terminal states (DONE / ARCHIVED) are always HEALTHY — the
       work is done, nothing to flag.
    2. BLOCKED state wins everything else and is reported verbatim.
    3. Failing tests / failing CI push to NEEDS_ATTENTION regardless
       of state.
    4. REVIEW state with a PR open maps to IN_REVIEW.
    5. Stale non-terminal tasks fall into STALE.
    6. Otherwise HEALTHY.
    """
    current_time = time.time() if now is None else now

    # (1) Terminal — no attention needed.
    if task.state in (TaskState.DONE, TaskState.ARCHIVED):
        return HealthSummary(
            level=HealthLevel.HEALTHY,
            label="Done",
            reason="Task is complete.",
            attention=False,
        )

    # (2) Explicit block wins everything — the user asked for it.
    if task.state == TaskState.BLOCKED:
        reason_text = (getattr(task, "blocked_reason", "") or "").strip()
        label = f"Blocked: {reason_text}" if reason_text else "Blocked"
        reason = reason_text or "This task is blocked. Set a reason to clarify what's needed."
        return HealthSummary(
            level=HealthLevel.BLOCKED,
            label=label,
            reason=reason,
            attention=True,
            # Fixed short badge — the reason lives in the tooltip and
            # in the card's meta line, never in the chip itself.
            badge="Blocked",
        )

    # (3) Failing signal from tests or CI — NEEDS_ATTENTION regardless
    # of whatever else the task thinks its state is. Tests trump CI
    # because a test failure is a more local/deterministic signal.
    test_run = task.last_test_run
    if test_run is not None and test_run.failed > 0:
        return HealthSummary(
            level=HealthLevel.NEEDS_ATTENTION,
            label=f"{test_run.failed} test(s) failing",
            reason=f"Most recent test run: {test_run.passed}/{test_run.total} passing.",
            attention=True,
        )

    ci_run = task.last_ci_run
    if ci_run is not None and ci_run.status == "failure":
        workflow = ci_run.workflow or "CI"
        return HealthSummary(
            level=HealthLevel.NEEDS_ATTENTION,
            label=f"{workflow} failing",
            reason=f"Most recent CI run failed: {ci_run.url or workflow}",
            attention=True,
        )

    # (4) PR open and the task is in REVIEW — that's the happy review
    # path. Still surface it in Today so reviewers know it's waiting
    # on them.
    if task.state == TaskState.REVIEW and task.pr_url:
        pr_number = f"#{task.pr_number}" if task.pr_number else ""
        label = f"In review {pr_number}".strip()
        # Badge shows just "Review" or "Review #42" — never the PR URL.
        badge = f"Review {pr_number}".strip() if pr_number else "Review"
        return HealthSummary(
            level=HealthLevel.IN_REVIEW,
            label=label,
            reason=f"Pull request open: {task.pr_url}",
            attention=False,
            badge=badge,
        )

    # (5) Stale detection — active/planning/review tasks that haven't
    # moved in a long time. Different thresholds per state because a
    # review waiting 2 days deserves more urgency than an idea in
    # planning.
    stale_threshold: float | None = {
        TaskState.PLANNING: _STALE_PLANNING_SECONDS,
        TaskState.ACTIVE: _STALE_ACTIVE_SECONDS,
        TaskState.REVIEW: _STALE_REVIEW_SECONDS,
    }.get(task.state)
    if stale_threshold is not None:
        age = current_time - task.updated_at
        if age > stale_threshold:
            days = int(age // 86400)
            return HealthSummary(
                level=HealthLevel.STALE,
                label=f"Stale ({days}d)",
                reason=(
                    f"No updates for {days} days while in {task.state.value}. "
                    "Consider moving it forward, blocking it, or archiving."
                ),
                attention=True,
                # Very short — "Stale 4d" fits in the sidebar chip.
                badge=f"Stale {days}d",
            )

    # (6) Default: HEALTHY. Description tries to be useful rather than
    # just saying "ok".
    pieces: list[str] = [f"State: {task.state.value}"]
    if task.branch:
        pieces.append(f"branch {task.branch}")
    if test_run is not None and test_run.total > 0 and test_run.all_green:
        pieces.append(f"{test_run.passed}/{test_run.total} tests passing")
    if ci_run is not None and ci_run.status == "success":
        pieces.append("CI green")
    return HealthSummary(
        level=HealthLevel.HEALTHY,
        label="Healthy",
        reason="; ".join(pieces) + ".",
        attention=False,
    )
