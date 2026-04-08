"""Task data model — the unit of work a developer is doing right now.

A Task ties together a branch, a chat conversation, a test history, a
PR, and a timeline of events. Every existing panel will eventually
become a view of the active task; this module owns the dataclasses
and the enums, while ``task_store`` owns persistence and
``task_manager`` owns the singleton + event bus integration.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class TaskKind(str, Enum):
    """The flavour of work the user is doing.

    Each kind unlocks a slightly different default workflow:
    - FEATURE: reserved for an AI plan-checklist generator (not yet
      wired — ``task.plan`` is currently always empty unless a caller
      populates it manually)
    - BUGFIX:  shorter loop, focused on reproducing then fixing
    - INCIDENT: triggers the trace-extractor flow (paste a stack trace)
    - REFACTOR: cleanup / restructure with no new behaviour
    - EXPLORE: data exploration (SQL notebook scoped to the task)
    - CHORE:   deps, config, infra
    """

    FEATURE = "feature"
    BUGFIX = "bugfix"
    INCIDENT = "incident"
    REFACTOR = "refactor"
    EXPLORE = "explore"
    CHORE = "chore"


class TaskState(str, Enum):
    """Where a task is in its lifecycle.

    Drives the colour and grouping in the Tasks sidebar panel.
    """

    PLANNING = "planning"  # user is defining / AI is drafting
    ACTIVE = "active"  # user is working on it
    REVIEW = "review"  # PR open, waiting on checks
    BLOCKED = "blocked"  # waiting on something external
    DONE = "done"  # merged / closed
    ARCHIVED = "archived"  # older than N days, hidden by default


@dataclass
class PlanStep:
    """A single checkbox in a task's plan.

    Reserved for a future AI plan-checklist generator. No code path
    currently populates ``task.plan`` — the model and the read-only
    UI are in place so that the generator can be wired in without a
    schema change. Each step optionally tracks the files it touched
    and any AI notes about the implementation strategy.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    status: str = "pending"  # pending | in_progress | done | skipped
    files: list[str] = field(default_factory=list)
    ai_notes: str = ""


@dataclass
class TaskNote:
    """A single timeline event on a task.

    Notes accumulate as the task runs: created, AI responses, commits,
    test runs, CI events, PR opened/merged. The UI renders them as a
    chronological feed so the user (or a future ``Share`` button) can
    reconstruct what happened.
    """

    timestamp: float
    kind: str  # "created" | "committed" | "tested" | "ai_response" | "pr_opened" | ...
    text: str
    data: dict = field(default_factory=dict)  # freeform payload for UI rendering


@dataclass
class TestRunSnapshot:
    """Compact summary of the most recent test run for a task."""

    # Tell pytest not to collect this dataclass as a test class.
    __test__ = False

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    timestamp: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    @property
    def all_green(self) -> bool:
        return self.failed == 0 and self.total > 0


@dataclass
class CIRunSnapshot:
    """Compact summary of the most recent CI run for a task's branch."""

    status: str = ""  # queued | in_progress | success | failure | cancelled
    workflow: str = ""
    url: str = ""
    timestamp: float = 0.0


@dataclass
class Task:
    """The unit of work a developer is doing right now.

    A task ties together everything the user touches while working on
    a single piece of work: branch, conversation, modified files,
    plan, test history, CI status, and a PR. Panels read the active
    task and re-scope themselves accordingly.
    """

    id: str
    project_root: str
    kind: TaskKind
    title: str
    description: str = ""
    state: TaskState = TaskState.PLANNING

    # Linked artifacts (all optional, populated as the task progresses)
    branch: str | None = None
    base_branch: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    chat_session_id: str | None = None

    # Workflow data
    plan: list[PlanStep] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    last_test_run: TestRunSnapshot | None = None
    last_ci_run: CIRunSnapshot | None = None
    notes: list[TaskNote] = field(default_factory=list)

    # Lifecycle timestamps
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    archived_at: float | None = None

    @classmethod
    def new(
        cls,
        project_root: str,
        kind: TaskKind,
        title: str,
        description: str = "",
    ) -> "Task":
        """Construct a fresh task with a generated id and a 'created' note."""
        now = time.time()
        task = cls(
            id=str(uuid.uuid4()),
            project_root=project_root,
            kind=kind,
            title=title,
            description=description,
            created_at=now,
            updated_at=now,
        )
        task.notes.append(
            TaskNote(timestamp=now, kind="created", text=f"Task created ({kind.value})")
        )
        return task

    def touch(self) -> None:
        """Bump ``updated_at`` to now. Call after any mutation."""
        self.updated_at = time.time()
