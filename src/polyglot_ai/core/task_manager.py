"""Owns the active task and broadcasts changes to the rest of the app.

The TaskManager is the single source of truth for "what is the user
working on right now". UI panels subscribe to its events via the
shared :class:`EventBus`; when the active task changes, every panel
re-scopes itself.

Currently the chat, git, tests, CI, and review panels all subscribe
to these events. See ``roadmap.md`` for future workflow integrations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.task_store import TaskStore, get_task_store
from polyglot_ai.core.tasks import PlanStep, Task, TaskKind, TaskNote, TaskState

logger = logging.getLogger(__name__)


# Event bus topics emitted by the TaskManager. Keeping them as
# constants makes it easy to grep for subscribers later.
EVT_TASK_CHANGED = "task:changed"
EVT_TASK_NOTE_ADDED = "task:note_added"
EVT_TASK_STATE_CHANGED = "task:state_changed"
EVT_TASK_LIST_CHANGED = "task:list_changed"
# Fired when a write to the task store failed. Subscribers can surface
# this to the user (status bar toast, error dialog, etc.). Payload:
# ``operation`` (str, e.g. "create"/"update"/"archive") and ``task_id``
# if known. Intentionally separate from the per-mutation events so a
# failure can be observed without the UI applying a stale update.
EVT_TASK_WRITE_FAILED = "task:write_failed"


class TaskManager:
    """Singleton owning the active task and emitting change events.

    Lifetime: app-wide. Constructed in app.py and shared via
    ui_wiring. Panels are wired up by subscribing to the events
    above on the same EventBus instance the manager was given.
    """

    def __init__(
        self,
        store: TaskStore | None = None,
        event_bus: EventBus | None = None,
        plan_generator=None,
    ) -> None:
        self._store = store or get_task_store()
        self._bus = event_bus
        self._project_root: Path | None = None
        self._active_task: Task | None = None
        # Optional AI plan generator. Injected from app.py once a
        # provider manager exists. Kept loose-typed so this module
        # has no hard dependency on the AI layer.
        self._plan_generator = plan_generator

    def set_plan_generator(self, plan_generator) -> None:
        """Late-bind the plan generator (used by ``app.py``)."""
        self._plan_generator = plan_generator

    @property
    def plan_generator(self):
        return self._plan_generator

    # ── Lifecycle ───────────────────────────────────────────────────

    def set_project_root(self, root: Path) -> None:
        """Switch to a different project. Auto-activates the most recent
        non-done task in that project, if any."""
        self._project_root = root
        recent = self._store.list_tasks(
            str(root),
            state_filter=[TaskState.ACTIVE, TaskState.PLANNING, TaskState.REVIEW],
        )
        self._active_task = recent[0] if recent else None
        self._emit(EVT_TASK_LIST_CHANGED)
        self._emit(EVT_TASK_CHANGED, task=self._active_task)

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @property
    def active(self) -> Task | None:
        return self._active_task

    # ── Listing ─────────────────────────────────────────────────────

    def list_tasks(
        self,
        state_filter: list[TaskState] | None = None,
        include_archived: bool = False,
    ) -> list[Task]:
        if self._project_root is None:
            return []
        return self._store.list_tasks(
            str(self._project_root),
            state_filter=state_filter,
            include_archived=include_archived,
        )

    # ── CRUD ────────────────────────────────────────────────────────

    def create_task(
        self,
        kind: TaskKind,
        title: str,
        description: str = "",
    ) -> Task | None:
        """Create a new task in the current project and make it active.

        Returns ``None`` if no project is open (the caller should
        prompt the user to open a folder first).
        """
        if self._project_root is None:
            logger.warning("task_manager: cannot create task — no project open")
            return None
        if not title.strip():
            logger.warning("task_manager: cannot create task with empty title")
            return None
        task = Task.new(
            project_root=str(self._project_root),
            kind=kind,
            title=title.strip(),
            description=description.strip(),
        )
        if not self._store.save(task):
            # Persistence failed — do NOT advance in-memory state,
            # otherwise the UI would render a task that vanishes on
            # restart. Surface the failure via the dedicated event.
            self._emit(EVT_TASK_WRITE_FAILED, operation="create", task_id=task.id)
            return None
        self._active_task = task
        self._emit(EVT_TASK_LIST_CHANGED)
        self._emit(EVT_TASK_CHANGED, task=task)
        logger.info("task_manager: created task %s (%s)", task.id, task.title)
        return task

    def set_active(self, task_id: str | None) -> None:
        """Switch the active task. ``None`` clears the active task."""
        if task_id is None:
            self._active_task = None
            self._emit(EVT_TASK_CHANGED, task=None)
            return
        task = self._store.get(task_id)
        if task is None:
            logger.warning("task_manager: cannot activate unknown task %s", task_id)
            return
        self._active_task = task
        self._emit(EVT_TASK_CHANGED, task=task)

    def update_state(
        self,
        new_state: TaskState,
        *,
        reason: str = "",
        source: str = "user",
    ) -> None:
        """Transition the active task to a new state.

        Emits both ``task:state_changed`` (so UI can animate) and
        ``task:list_changed`` (so the sidebar regrouping refreshes).

        ``reason`` is stored on the timeline note (useful for
        blocking/unblocking decisions). ``source`` tags the note as
        ``"user"``, ``"ai"``, ``"automation"``, or ``"system"`` so
        the timeline can later be filtered by origin. Defaults to
        ``"user"`` because explicit state changes almost always come
        from a user click.

        Leaving BLOCKED clears ``blocked_reason`` so a stale reason
        doesn't linger on the card after the user unparks the task.
        """
        task = self._active_task
        if task is None:
            return
        old_state = task.state
        if old_state == new_state:
            return
        prev_blocked_reason = task.blocked_reason
        task.state = new_state
        # If we're moving OUT of BLOCKED, clear the reason so the
        # card stops showing it. The transition note below still
        # mentions the reason we came from.
        if old_state == TaskState.BLOCKED and new_state != TaskState.BLOCKED:
            task.blocked_reason = ""
        # Touch BEFORE building the note so the note's timestamp
        # reflects *this* mutation, not the previous one.
        task.touch()
        note_text = f"State: {old_state.value} → {new_state.value}"
        if reason:
            note_text += f" ({reason})"
        task.notes.append(
            TaskNote(
                timestamp=task.updated_at,
                kind="state_changed",
                text=note_text,
                data={"from": old_state.value, "to": new_state.value, "reason": reason},
                source=source,
                category="workflow",
            )
        )
        if not self._store.save(task):
            # Roll back the in-memory change so the UI and disk agree.
            task.state = old_state
            task.blocked_reason = prev_blocked_reason
            task.notes.pop()
            self._emit(EVT_TASK_WRITE_FAILED, operation="state_change", task_id=task.id)
            return
        self._emit(
            EVT_TASK_STATE_CHANGED,
            task=task,
            old_state=old_state,
            new_state=new_state,
        )
        self._emit(EVT_TASK_LIST_CHANGED)

    def block_task(self, reason: str, *, source: str = "user") -> bool:
        """Move the active task to BLOCKED and capture *why*.

        Refuses an empty reason — the whole point of this helper is
        that BLOCKED without a reason is useless. Returns ``True``
        on success, ``False`` if there is no active task, the
        reason is empty, or the store write failed. The caller
        should surface ``False`` to the user.
        """
        reason = (reason or "").strip()
        if not reason:
            logger.warning("task_manager: block_task refused — empty reason")
            return False
        task = self._active_task
        if task is None:
            return False
        old_state = task.state
        prev_blocked_reason = task.blocked_reason
        task.blocked_reason = reason
        task.state = TaskState.BLOCKED
        task.touch()
        task.notes.append(
            TaskNote(
                timestamp=task.updated_at,
                kind="blocked",
                text=f"Blocked: {reason}",
                data={"from": old_state.value, "reason": reason},
                source=source,
                category="workflow",
            )
        )
        if not self._store.save(task):
            task.state = old_state
            task.blocked_reason = prev_blocked_reason
            task.notes.pop()
            self._emit(EVT_TASK_WRITE_FAILED, operation="block", task_id=task.id)
            return False
        self._emit(
            EVT_TASK_STATE_CHANGED,
            task=task,
            old_state=old_state,
            new_state=TaskState.BLOCKED,
        )
        self._emit(EVT_TASK_LIST_CHANGED)
        return True

    def update_active(self, **fields) -> None:
        """Patch arbitrary fields on the active task and persist.

        Used by panels (git, tests, CI) to record artifacts on the
        active task. Whitelist of allowed fields prevents accidental
        attribute injection from untrusted callers.
        """
        task = self._active_task
        if task is None:
            return
        allowed = {
            "branch",
            "base_branch",
            "pr_url",
            "pr_number",
            "chat_session_id",
            "modified_files",
            "last_test_run",
            "last_ci_run",
            "title",
            "description",
        }
        # Snapshot the previous values of every field we are about to
        # mutate so we can roll back if the write fails.
        previous: dict[str, object] = {}
        for key, value in fields.items():
            if key not in allowed:
                logger.warning("task_manager: ignoring unknown field %s", key)
                continue
            previous[key] = getattr(task, key)
            setattr(task, key, value)
        prev_updated_at = task.updated_at
        task.touch()
        if not self._store.save(task):
            for key, value in previous.items():
                setattr(task, key, value)
            task.updated_at = prev_updated_at
            self._emit(EVT_TASK_WRITE_FAILED, operation="update", task_id=task.id)
            return
        self._emit(EVT_TASK_CHANGED, task=task)

    def set_plan(self, steps: list[PlanStep]) -> bool:
        """Replace the active task's plan with ``steps``.

        Used by the AI plan generator. Kept separate from
        :meth:`update_active` because ``plan`` is internal-only and
        we don't want it on the field whitelist where any panel
        could overwrite it. Returns ``True`` on success.
        """
        task = self._active_task
        if task is None:
            return False
        previous_plan = list(task.plan)
        prev_updated_at = task.updated_at
        task.plan = list(steps)
        task.touch()
        if not self._store.save(task):
            task.plan = previous_plan
            task.updated_at = prev_updated_at
            self._emit(EVT_TASK_WRITE_FAILED, operation="set_plan", task_id=task.id)
            return False
        # Drop a timeline note so the user can see when the plan was
        # generated/regenerated. We use add_note here intentionally so
        # the note timestamp is independent of the save we just did.
        self.add_note("plan_generated", f"Plan generated ({len(steps)} steps)")
        self._emit(EVT_TASK_CHANGED, task=task)
        return True

    def add_note(
        self,
        kind: str,
        text: str,
        data: dict | None = None,
        *,
        source: str = "system",
        category: str = "",
    ) -> None:
        """Append a timeline event to the active task.

        ``source`` / ``category`` tag the note for the future
        timeline filter UI. Callers that don't care can omit both
        and get the legacy defaults (``system`` / ``""``), so
        existing call sites are unchanged.
        """
        task = self._active_task
        if task is None:
            return
        # Touch BEFORE stamping the note so two rapid events don't
        # share a timestamp and the note reflects *now*, not the
        # previous mutation.
        prev_updated_at = task.updated_at
        task.touch()
        note = TaskNote(
            timestamp=task.updated_at,
            kind=kind,
            text=text,
            data=data or {},
            source=source,
            category=category,
        )
        task.notes.append(note)
        if not self._store.save(task):
            task.notes.pop()
            task.updated_at = prev_updated_at
            self._emit(EVT_TASK_WRITE_FAILED, operation="add_note", task_id=task.id)
            return
        self._emit(EVT_TASK_NOTE_ADDED, task=task, note=note)

    def archive(self, task_id: str) -> None:
        """Move a task to the ARCHIVED state and remove it from views."""
        task = self._store.get(task_id)
        if task is None:
            return
        task.state = TaskState.ARCHIVED
        task.touch()
        task.archived_at = task.updated_at
        if not self._store.save(task):
            self._emit(EVT_TASK_WRITE_FAILED, operation="archive", task_id=task_id)
            return
        if self._active_task and self._active_task.id == task_id:
            self._active_task = None
            self._emit(EVT_TASK_CHANGED, task=None)
        self._emit(EVT_TASK_LIST_CHANGED)

    def delete(self, task_id: str) -> None:
        """Permanently delete a task. Also clears active if matched."""
        if not self._store.delete(task_id):
            self._emit(EVT_TASK_WRITE_FAILED, operation="delete", task_id=task_id)
            return
        if self._active_task and self._active_task.id == task_id:
            self._active_task = None
            self._emit(EVT_TASK_CHANGED, task=None)
        self._emit(EVT_TASK_LIST_CHANGED)

    # ── Internals ───────────────────────────────────────────────────

    def _emit(self, event: str, **kwargs) -> None:
        # EventBus already isolates subscriber exceptions (see
        # ``bridge.py``), so we don't need to wrap this in try/except —
        # doing so would only hide real problems if ``emit`` itself
        # ever started raising.
        if self._bus is None:
            return
        self._bus.emit(event, **kwargs)


# Module-level singleton.
_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager


def init_task_manager(event_bus: EventBus) -> TaskManager:
    """Initialise the singleton with an event bus. Called from app.py."""
    global _manager
    _manager = TaskManager(event_bus=event_bus)
    return _manager
