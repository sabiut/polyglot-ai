# Tasks and Today

The **Tasks** system is the workflow layer that ties every panel together.
A task ties your branch, chat conversation, test runs, CI status, and PR
into a single unit of work. Open Tasks with `Ctrl+Shift+J`, Today with
`Ctrl+Shift+H`.

> A standalone, fully-detailed reference lives at
> [`docs/tasks-workflow.html`](../docs/tasks-workflow.html) in the repo.
> This wiki page is the short version.

## Core idea

A **task** represents one piece of work — a feature, a bugfix, an
incident, a refactor. You create it, and every panel that knows about
tasks re-scopes itself to the active one:

- **Chat** loads the task's conversation and injects task context into the
  system prompt.
- **Git** records commits, branches, and PRs on the task timeline.
- **Tests** saves a pass/fail snapshot after each run.
- **CI/CD** tracks the latest run on the task's branch.
- **Review** logs each AI review pass.

When you come back the next day, click the task in the sidebar and
everything is back where you left it.

## Kinds

| Kind | Use for |
|---|---|
| **feature** | New functionality |
| **bugfix** | Targeted defect fix |
| **incident** | Production issue; auto-created from failed CI |
| **refactor** | Restructuring with no behaviour change |
| **explore** | Data / codebase exploration |
| **chore** | Deps, config, maintenance |

## States

| State | Meaning |
|---|---|
| **PLANNING** | Defining the task. AI may draft a plan checklist. |
| **ACTIVE** | You're working on it. |
| **REVIEW** | PR is open, waiting on review / CI. Auto-set on PR open. |
| **BLOCKED** | Waiting on something external. |
| **DONE** | Merged / closed. |
| **ARCHIVED** | Hidden from normal views. |

## Today panel

The landing page. Three sections:

1. **Active Tasks** — cards for ACTIVE / PLANNING / REVIEW tasks. Click
   to make one active.
2. **Attention** — failed CI runs and open PRs on your branches, fetched
   via `gh`. Refreshes on project open and every 60 seconds.
3. **Quick Actions** — buttons for *New task*, *Run all tests*, *Refresh
   CI*, etc.

## Tasks sidebar

Lists every non-archived task in the current project, grouped by state
(ACTIVE → PLANNING → REVIEW → BLOCKED → DONE (recent)).

### Card interactions
- **Click** → make active.
- **Double-click** → open the Task Detail dialog.
- **Right-click** → activate, open details, move to any state, archive,
  delete.
- **+ button** → New Task dialog.
- **Refresh** → reload from disk.
- **Expand** → open Tasks in a standalone window that stays in sync.

### Task card shows
- Coloured dot for the kind.
- Title.
- Meta line: `kind · ⎇ branch · 12/13 tests · CI ✓ · 3m ago`.

## Task Detail dialog

Opened by double-clicking a card. Contents:

1. **Header** — kind dot, title, meta line.
2. **Description** — if set.
3. **Plan** — read-only checklist (empty for now; AI plan generator coming).
4. **Stats card** — test ratio, CI symbol, files touched, PR number.
5. **Timeline** — newest first, one row per note (created, committed,
   pushed, tested, pr_opened, review_*, ci_run, …).
6. **Actions**: *Open PR*, *Copy as standup*, state transitions, *Close*.

### Copy as standup

Builds a markdown summary of the task (title, branch, PR, recent events,
tests, CI) and copies it to the clipboard. Paste straight into your
daily standup.

## Typical lifecycle

1. Open project.
2. `Ctrl+Shift+J` → **+** → pick kind, type title + description.
3. Chat with the AI — the conversation is now bound to the task.
4. Create a branch in the Git panel. Commits get logged.
5. Mark as **Active** in the detail dialog.
6. Run tests. Snapshot is recorded.
7. Run a review. Findings are logged.
8. Generate PR description, open PR. State → **REVIEW**.
9. Merge. Detail dialog → **Mark done**.
10. Archive (optional).

## Storage

Tasks live in their own SQLite database, independent of the main app DB:

```
~/.config/polyglot-ai/tasks.sqlite
```

One `tasks` table with JSON-blob columns for nested structures. Indexed
by `(project_root, state)` and `(project_root, updated_at DESC)`.

## Event bus topics

Panels subscribe to these:

| Topic | Payload | Fired on |
|---|---|---|
| `task:changed` | `task` (or `None`) | Active task switched / cleared |
| `task:list_changed` | — | Create, archive, delete, project switch |
| `task:state_changed` | `task, old_state, new_state` | `update_state()` |
| `task:note_added` | `task, note` | `add_note()` |

## Full reference

The full feature reference — every field, every glyph, troubleshooting,
and a scripting API example — is in
[`docs/tasks-workflow.html`](../docs/tasks-workflow.html).
