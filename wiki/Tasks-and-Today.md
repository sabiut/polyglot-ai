# Tasks and Today

The **Tasks** system is the workflow layer that ties every panel together.
A task ties your branch, chat conversation, test runs, CI status, and PR
into a single unit of work. Open Tasks with `Ctrl+Shift+J`, Today with
`Ctrl+Shift+H`.

> **You don't have to use tasks.** Everything in the app — chat, review,
> git, tests — works standalone. If you just want to run a security
> review and ask the AI about it, skip this page. The AI can see review
> results via the `PANEL STATE` block in the system prompt and the
> `get_review_findings` tool without any task being active. See
> [Tests and Review](Tests-and-Review.md).

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

The new-task dialog offers three primary kinds — the rest are still
valid enum values (so old stored tasks keep loading) but removed from
the picker to reduce friction.

| Kind | Use for | In dropdown? |
|---|---|---|
| **feature** | New functionality (default) | ✅ |
| **bugfix** | Targeted defect fix | ✅ |
| **refactor** | Restructuring with no behaviour change | ✅ |
| incident | Production issue | legacy only |
| explore | Data / codebase exploration | legacy only |
| chore | Deps, config, maintenance | legacy only |

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

The landing page. **Two sections now** — the old "Active Tasks" mini
list was removed because it duplicated the Tasks sidebar. Today is
focused purely on alerts:

1. **Attention** — failed CI runs, open PRs on your branches, and any
   task that's blocked / stale / failing. Fetched via `gh` and refreshed
   on project open and every 60 seconds.
2. **Quick Actions** — buttons for *New task*, *View all tasks*, *Run all
   tests*, *Refresh CI*, *Source control*, *Chat*.

Click **🗂 View all tasks** to jump to the Tasks sidebar when you need
the full list.

## Tasks sidebar

Lists every non-archived task in the current project, grouped by state
(ACTIVE → PLANNING → REVIEW → BLOCKED → DONE (recent)).

### Card interactions
- **Click** → make active.
- **Double-click** → open the Task Detail window.
- **Right-click** → activate, open details, move to any state, archive,
  delete.
- **+ button** → **inline quick-create row** — type a title and press
  Enter. That's it. Click ⋯ on the row if you want to set kind or
  description via the full dialog. Press Esc to cancel.
- **Refresh** → reload from disk.
- **Expand** → open Tasks in a standalone window that stays in sync.

### Keyboard

- `Ctrl+Shift+J` — open the Tasks sidebar. Click + or use the palette
  to create.
- `Ctrl+Shift+P` → search `Task:` — full command palette entry points:
  *Task: New*, *Task: New (with kind/description)*, *Task: Switch
  Active…*, *Task: Open Active Task Detail*, *Task: Mark Active as
  Done*, *Task: Block Active Task…*, *Task: Show Tasks Panel*,
  *Task: Show Today Panel*.

> The *Task: New* palette entry has no direct shortcut because
> `Ctrl+Shift+T` is the Tests panel shortcut. Use the palette or click
> the `+` button in the Tasks sidebar.

### Task card shows
- Coloured dot for the kind.
- Title.
- Meta line: `kind · ⎇ branch · 12/13 tests · CI ✓ · 3m ago`.

## Task Detail window

Opened by double-clicking a card. **Non-modal** — you can keep it open
alongside the chat, review, and git panels while you work. The `⛶`
button in the title row toggles maximize. Close it any time; a new one
opens fresh next time.

Contents:

1. **Header** — kind dot, title, ⛶ maximize button, meta line.
2. **Description** — if set.
3. **Checklist** — either a populated read-only list of steps (when the
   AI has generated one), or a prominent **"✨ Generate checklist with
   AI"** primary-action card when the task is new and a plan generator
   is configured. This is the main thing to click first on a fresh task.
4. **Stats card** — test ratio, CI symbol, files touched, PR number.
5. **Timeline** — newest first, one row per note (created, committed,
   pushed, tested, pr_opened, review_*, ci_run, …).
6. **Actions**: *Open PR*, *Copy as standup*, *Regenerate checklist*
   (only when a checklist exists), state transitions, *Close*.

> **Vocabulary:** the section is now called **Checklist** everywhere
> in the detail window. The left-sidebar **Plan** panel (driven by the
> `create_plan` tool + `PlanExecutor`) is a different feature — it
> visualises multi-step tool-use execution, not a task's checklist.

### Copy as standup

Builds a markdown summary of the task (title, branch, PR, recent events,
tests, CI) and copies it to the clipboard. Paste straight into your
daily standup.

## Typical lifecycle

1. Open project.
2. `Ctrl+Shift+J` → click **+** → type title → Enter. Done.
3. Double-click the new card to open the detail window. Click
   **✨ Generate checklist with AI** to break the task into ordered
   steps.
4. Chat with the AI — the conversation is now bound to the task and
   the AI sees the checklist in its system prompt.
5. Create a branch in the Git panel. Commits get logged automatically.
6. Run tests. Snapshot is recorded on the task timeline.
7. Run a review. Findings are logged AND the AI can query them via
   `get_review_findings`.
8. Generate PR description, open PR. State → **REVIEW**.
9. `Ctrl+Shift+P` → *Task: Mark Active as Done*.
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
