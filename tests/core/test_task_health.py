"""Tests for ``compute_health`` — the pure health-synthesis function.

Every test constructs a :class:`Task`, mutates a handful of fields,
and asserts on the returned :class:`HealthSummary`. No store, no
event bus, no Qt. ``now`` is passed explicitly so stale-detection
tests are deterministic.
"""

from __future__ import annotations

from polyglot_ai.core.task_health import (
    HealthLevel,
    HealthSummary,
    compute_health,
)
from polyglot_ai.core.tasks import (
    CIRunSnapshot,
    Task,
    TaskKind,
    TaskState,
    TestRunSnapshot,
)

# Pin ``now`` in tests so stale-detection branches are deterministic.
_NOW = 1_700_000_000.0


def _make(state: TaskState = TaskState.ACTIVE, **kwargs) -> Task:
    """Build a task with ``updated_at`` pinned near ``_NOW`` so the
    default branches don't accidentally fall into the stale case.
    """
    task = Task.new("/tmp/proj", TaskKind.FEATURE, "Test task")
    task.state = state
    task.updated_at = _NOW - 60  # one minute ago
    for key, value in kwargs.items():
        setattr(task, key, value)
    return task


# ── Terminal states ────────────────────────────────────────────────


def test_done_is_healthy_and_silent():
    task = _make(state=TaskState.DONE)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY
    assert summary.attention is False
    assert "done" in summary.label.lower()


def test_archived_is_healthy_and_silent():
    task = _make(state=TaskState.ARCHIVED)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY
    assert summary.attention is False


# ── BLOCKED wins everything else ──────────────────────────────────


def test_blocked_with_reason_shows_reason_in_label():
    task = _make(state=TaskState.BLOCKED)
    task.blocked_reason = "waiting on API contract"
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.BLOCKED
    assert summary.attention is True
    assert "waiting on API contract" in summary.label
    assert "waiting on API contract" in summary.reason


def test_blocked_without_reason_falls_back_to_plain_label():
    task = _make(state=TaskState.BLOCKED)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.BLOCKED
    assert summary.label == "Blocked"
    assert "Set a reason" in summary.reason


def test_blocked_wins_over_failing_tests():
    """BLOCKED state is a deliberate user choice and should outrank
    the raw failing-tests signal, so we don't nag the user about a
    test failure they've already acknowledged and parked."""
    task = _make(state=TaskState.BLOCKED)
    task.blocked_reason = "rollback in progress"
    task.last_test_run = TestRunSnapshot(passed=1, failed=5, skipped=0, timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.BLOCKED


# ── Failing tests / CI push to NEEDS_ATTENTION ────────────────────


def test_failing_tests_needs_attention():
    task = _make(state=TaskState.ACTIVE)
    task.last_test_run = TestRunSnapshot(passed=3, failed=2, skipped=0, timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.NEEDS_ATTENTION
    assert "2 test(s) failing" in summary.label
    assert summary.attention is True


def test_test_failure_outranks_ci_success():
    """Local test signal beats remote CI success — the working copy
    is broken, the green CI run is for a previous commit."""
    task = _make(state=TaskState.ACTIVE)
    task.last_test_run = TestRunSnapshot(passed=0, failed=1, skipped=0, timestamp=_NOW)
    task.last_ci_run = CIRunSnapshot(status="success", workflow="ci.yml", timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.NEEDS_ATTENTION
    assert "failing" in summary.label.lower()


def test_failing_ci_needs_attention():
    task = _make(state=TaskState.ACTIVE)
    task.last_ci_run = CIRunSnapshot(
        status="failure", workflow="release.yml", url="https://ci/run/1"
    )
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.NEEDS_ATTENTION
    assert "release.yml failing" in summary.label
    assert "https://ci/run/1" in summary.reason


def test_ci_in_progress_is_not_attention():
    """Only 'failure' trips the gate, not 'in_progress' or 'queued'."""
    task = _make(state=TaskState.ACTIVE)
    task.last_ci_run = CIRunSnapshot(status="in_progress", workflow="ci.yml")
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY


# ── REVIEW with a PR → IN_REVIEW ──────────────────────────────────


def test_review_with_pr_maps_to_in_review():
    task = _make(state=TaskState.REVIEW)
    task.pr_url = "https://github.com/you/repo/pull/42"
    task.pr_number = 42
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.IN_REVIEW
    assert "#42" in summary.label
    assert summary.attention is False  # waiting on reviewers, not on the user


def test_review_without_pr_is_not_in_review():
    """A task in REVIEW without a PR URL probably moved too early —
    do not mark it IN_REVIEW, fall through to healthy/stale."""
    task = _make(state=TaskState.REVIEW)
    summary = compute_health(task, now=_NOW)
    assert summary.level != HealthLevel.IN_REVIEW


def test_failing_tests_outrank_in_review_badge():
    task = _make(state=TaskState.REVIEW)
    task.pr_url = "https://example/pull/1"
    task.pr_number = 1
    task.last_test_run = TestRunSnapshot(passed=2, failed=1, skipped=0, timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.NEEDS_ATTENTION


# ── Stale detection ───────────────────────────────────────────────


def test_stale_planning_task_after_three_days():
    task = _make(state=TaskState.PLANNING)
    task.updated_at = _NOW - (4 * 24 * 3600)  # 4 days ago
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.STALE
    assert "4d" in summary.label
    assert summary.attention is True


def test_planning_two_days_old_is_not_stale():
    task = _make(state=TaskState.PLANNING)
    task.updated_at = _NOW - (2 * 24 * 3600)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY


def test_stale_active_threshold_is_longer():
    """Active tasks get a more generous stale window than planning."""
    task = _make(state=TaskState.ACTIVE)
    # 4 days in ACTIVE is not yet stale (threshold is 5).
    task.updated_at = _NOW - (4 * 24 * 3600)
    assert compute_health(task, now=_NOW).level == HealthLevel.HEALTHY
    # But 6 days in ACTIVE is.
    task.updated_at = _NOW - (6 * 24 * 3600)
    assert compute_health(task, now=_NOW).level == HealthLevel.STALE


def test_stale_review_threshold_is_shorter():
    """A PR waiting too long should flag faster than an active task."""
    task = _make(state=TaskState.REVIEW)
    task.updated_at = _NOW - (3 * 24 * 3600)  # 3 days > 2 day REVIEW threshold
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.STALE


def test_done_task_is_never_stale():
    """Terminal states should never be flagged as stale, even if
    ``updated_at`` is ancient."""
    task = _make(state=TaskState.DONE)
    task.updated_at = _NOW - (365 * 24 * 3600)  # a year ago
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY


# ── Default healthy path ──────────────────────────────────────────


def test_fresh_active_task_is_healthy():
    task = _make(state=TaskState.ACTIVE)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY
    assert summary.attention is False


def test_healthy_reason_mentions_state_and_branch():
    task = _make(state=TaskState.ACTIVE)
    task.branch = "feat/x"
    summary = compute_health(task, now=_NOW)
    assert "active" in summary.reason.lower()
    assert "feat/x" in summary.reason


def test_healthy_reason_includes_passing_tests_when_set():
    task = _make(state=TaskState.ACTIVE)
    task.last_test_run = TestRunSnapshot(passed=10, failed=0, skipped=1, timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert summary.level == HealthLevel.HEALTHY
    assert "10/11 tests passing" in summary.reason


def test_healthy_reason_includes_ci_green_when_set():
    task = _make(state=TaskState.ACTIVE)
    task.last_ci_run = CIRunSnapshot(status="success", workflow="ci.yml", timestamp=_NOW)
    summary = compute_health(task, now=_NOW)
    assert "CI green" in summary.reason


# ── Colours + structure ───────────────────────────────────────────


def test_colour_mapping_is_complete():
    """Every HealthLevel must map to a colour — a new level added
    without updating the map would crash at paint time otherwise."""
    for level in HealthLevel:
        summary = HealthSummary(level=level, label="x", reason="x", attention=False)
        # Accessing .colour must not raise.
        assert summary.colour.startswith("#")


def test_now_injection_is_respected():
    """Passing ``now`` should override the real clock so tests that
    pin ``updated_at`` to future timestamps still work."""
    task = _make(state=TaskState.PLANNING)
    task.updated_at = _NOW
    # 10 days in the future — must not be stale even though the real
    # ``time.time()`` is earlier.
    summary = compute_health(task, now=_NOW + (10 * 24 * 3600))
    assert summary.level == HealthLevel.STALE
