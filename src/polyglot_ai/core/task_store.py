"""SQLite persistence layer for tasks.

Tasks live in their own SQLite database under
``~/.config/polyglot-ai/tasks.sqlite`` so the schema can evolve
independently of the main app DB. JSON columns are used for the
nested structures (plan, notes, modified_files, last_test_run,
last_ci_run) — they are small and rarely queried by content.

The store is intentionally synchronous: every call is well under a
frame budget. Wrap in ``asyncio.to_thread`` if a future caller needs
to run it from a tight async loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from polyglot_ai.core.tasks import (
    CIRunSnapshot,
    PlanStep,
    Task,
    TaskKind,
    TaskNote,
    TaskState,
    TestRunSnapshot,
)

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    return Path.home() / ".config" / "polyglot-ai" / "tasks.sqlite"


class TaskStore:
    """SQLite-backed CRUD for :class:`Task`.

    Single table, JSON-blob columns for the nested structures. The
    schema is created on first access.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    state TEXT NOT NULL,
                    branch TEXT,
                    base_branch TEXT,
                    pr_url TEXT,
                    pr_number INTEGER,
                    chat_session_id TEXT,
                    plan_json TEXT,
                    modified_files_json TEXT,
                    last_test_run_json TEXT,
                    last_ci_run_json TEXT,
                    notes_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    archived_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_project_state
                    ON tasks(project_root, state);
                CREATE INDEX IF NOT EXISTS idx_tasks_project_updated
                    ON tasks(project_root, updated_at DESC);
                """
            )

    # ── CRUD ────────────────────────────────────────────────────────

    def save(self, task: Task) -> bool:
        """Insert or replace ``task`` by id.

        Returns ``True`` on success, ``False`` if the write failed.
        Callers (notably :class:`TaskManager`) must check the return
        value so an in-memory mutation does not silently outlive a
        failed persistence — otherwise the UI shows state that will
        vanish on restart.
        """
        try:
            with self._conn() as c:
                c.execute(
                    """
                    INSERT INTO tasks (
                        id, project_root, kind, title, description, state,
                        branch, base_branch, pr_url, pr_number, chat_session_id,
                        plan_json, modified_files_json, last_test_run_json,
                        last_ci_run_json, notes_json,
                        created_at, updated_at, archived_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        project_root = excluded.project_root,
                        kind = excluded.kind,
                        title = excluded.title,
                        description = excluded.description,
                        state = excluded.state,
                        branch = excluded.branch,
                        base_branch = excluded.base_branch,
                        pr_url = excluded.pr_url,
                        pr_number = excluded.pr_number,
                        chat_session_id = excluded.chat_session_id,
                        plan_json = excluded.plan_json,
                        modified_files_json = excluded.modified_files_json,
                        last_test_run_json = excluded.last_test_run_json,
                        last_ci_run_json = excluded.last_ci_run_json,
                        notes_json = excluded.notes_json,
                        updated_at = excluded.updated_at,
                        archived_at = excluded.archived_at
                    """,
                    (
                        task.id,
                        task.project_root,
                        task.kind.value,
                        task.title,
                        task.description,
                        task.state.value,
                        task.branch,
                        task.base_branch,
                        task.pr_url,
                        task.pr_number,
                        task.chat_session_id,
                        json.dumps([asdict(p) for p in task.plan]),
                        json.dumps(task.modified_files),
                        json.dumps(asdict(task.last_test_run)) if task.last_test_run else None,
                        json.dumps(asdict(task.last_ci_run)) if task.last_ci_run else None,
                        json.dumps([asdict(n) for n in task.notes]),
                        task.created_at,
                        task.updated_at,
                        task.archived_at,
                    ),
                )
        except sqlite3.Error:
            logger.exception("task_store: could not save task %s", task.id)
            return False
        return True

    def get(self, task_id: str) -> Task | None:
        try:
            with self._conn() as c:
                row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        except sqlite3.Error:
            logger.exception("task_store: could not load task %s", task_id)
            return None
        if row is None:
            return None
        try:
            return self._row_to_task(row)
        except (ValueError, TypeError):
            logger.exception("task_store: corrupt row for task %s", task_id)
            return None

    def list_tasks(
        self,
        project_root: str,
        state_filter: list[TaskState] | None = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[Task]:
        """List tasks for a project, newest first.

        ``state_filter`` filters to specific states; ``include_archived``
        is False by default so the sidebar doesn't show old finished
        work unless explicitly asked.
        """
        sql = "SELECT * FROM tasks WHERE project_root = ?"
        params: list = [project_root]
        if state_filter:
            placeholders = ",".join("?" for _ in state_filter)
            sql += f" AND state IN ({placeholders})"
            params.extend(s.value for s in state_filter)
        if not include_archived:
            sql += " AND state != ?"
            params.append(TaskState.ARCHIVED.value)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        try:
            with self._conn() as c:
                rows = c.execute(sql, params).fetchall()
        except sqlite3.Error:
            logger.exception("task_store: could not list tasks for %s", project_root)
            return []
        # Convert rows individually so one corrupt row (unknown enum
        # value, JSON shape drift after a schema change, etc.) can't
        # crash the whole sidebar refresh. Bad rows are logged and
        # skipped; the rest render normally.
        tasks: list[Task] = []
        for row in rows:
            if not row:
                continue
            try:
                tasks.append(self._row_to_task(row))
            except (ValueError, TypeError):
                row_id = row["id"] if "id" in row.keys() else "?"
                logger.exception("task_store: skipping corrupt row %s", row_id)
        return tasks

    def delete(self, task_id: str) -> bool:
        """Delete a task by id. Returns ``True`` on success."""
        try:
            with self._conn() as c:
                c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        except sqlite3.Error:
            logger.exception("task_store: could not delete task %s", task_id)
            return False
        return True

    # ── Row → Task ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        def _load_json(col: str, default):
            raw = row[col] if col in row.keys() else None
            if not raw:
                return default
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("task_store: corrupt %s for task %s", col, row["id"])
                return default

        plan_data = _load_json("plan_json", [])
        plan = [PlanStep(**p) for p in plan_data] if plan_data else []

        notes_data = _load_json("notes_json", [])
        notes = [TaskNote(**n) for n in notes_data] if notes_data else []

        last_test_data = _load_json("last_test_run_json", None)
        last_test = TestRunSnapshot(**last_test_data) if last_test_data else None

        last_ci_data = _load_json("last_ci_run_json", None)
        last_ci = CIRunSnapshot(**last_ci_data) if last_ci_data else None

        modified_files = _load_json("modified_files_json", [])

        return Task(
            id=row["id"],
            project_root=row["project_root"],
            kind=TaskKind(row["kind"]),
            title=row["title"],
            description=row["description"] or "",
            state=TaskState(row["state"]),
            branch=row["branch"],
            base_branch=row["base_branch"],
            pr_url=row["pr_url"],
            pr_number=row["pr_number"],
            chat_session_id=row["chat_session_id"],
            plan=plan,
            modified_files=modified_files,
            last_test_run=last_test,
            last_ci_run=last_ci,
            notes=notes,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
        )


# Module-level singleton — UI code uses get_task_store() to share one instance.
_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    global _store
    if _store is None:
        _store = TaskStore()
    return _store
