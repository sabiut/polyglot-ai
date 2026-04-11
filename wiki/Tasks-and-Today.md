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

## Walkthrough: adding a login page from scratch

This example shows the full task lifecycle for a real feature. Follow
along with your own project, or just read it to understand the flow.

### 1. Create the task

Open the Tasks sidebar with `Ctrl+Shift+J` and click the **+** button.
Type "Add login page" and press Enter. A new task card appears under
**ACTIVE** — you're already working on it.

> **Tip:** If you want to set the kind (bugfix, refactor) or add a
> description, click the **⋯** button on the quick-create row instead
> of pressing Enter. That opens the full dialog.

### 2. Generate a checklist

Double-click the task card to open the **Task Detail** window. You'll
see a prominent card that says **"✨ Generate checklist with AI"** —
click it. The AI breaks "Add login page" into ordered steps:

```
1. [ ] Create LoginPage component with email/password fields
2. [ ] Add form validation (required, email format, min length)
3. [ ] Create auth API route POST /api/login
4. [ ] Wire form submission to API with loading/error states
5. [ ] Add protected route redirect for unauthenticated users
6. [ ] Write tests for LoginPage and auth route
```

This checklist is now visible in the AI's system prompt, so when you
chat it knows exactly what step you're on.

### 3. Chat with task context

Switch to the Chat panel. Notice the AI's first message now references
your task. Ask it something like:

> "Let's start with step 1. I'm using React + Tailwind. Create the
> LoginPage component."

The AI sees the task title, checklist, and state in its system prompt.
It stays scoped — if you ask something off-topic, it'll answer briefly
and offer to create a separate task for it.

### 4. Work through the steps

As you implement each step:

- **Commits** are logged on the task timeline automatically when you
  use the Git panel.
- **Test runs** are recorded — the task card shows `4/6 tests` so you
  can see progress at a glance.
- **Code reviews** (from the Review panel) are logged too. Ask the AI
  "what did the review find?" and it can answer without you pasting
  anything.

The task card in the sidebar updates its meta line in real time:

```
feature · ⎇ feat/login-page · 4/6 tests · CI ✓ · 3m ago
```

### 5. Open a PR

When you're done, use the Chat or Git panel to create a PR. The task
state moves to **REVIEW** automatically. The Today panel shows your
open PR under **Attention** so you don't forget about it.

### 6. Mark done

Once the PR is merged: `Ctrl+Shift+P` → type "Task: Mark Active as
Done" → Enter. The task moves to the **DONE** group. Archive it later
if you want a cleaner sidebar.

### What the AI sees (behind the scenes)

When a task is active, the AI's system prompt includes a block like
this at the very top:

```
ACTIVE TASK
Title: Add login page
Kind:  feature
State: active
Branch: feat/login-page

Plan checklist:
1. [ ] Create LoginPage component with email/password fields
2. [x] Add form validation
3. [~] Create auth API route POST /api/login
4. [ ] Wire form submission to API
5. [ ] Add protected route redirect
6. [ ] Write tests

Files touched so far on this task:
- src/pages/LoginPage.tsx
- src/api/auth.ts

Stay scoped to this task.
```

This is why the AI's answers feel focused — it knows the goal, the
plan, and what you've already done.

---

## More examples

### Quick bugfix (no checklist needed)

1. `Ctrl+Shift+J` → **+** → "Fix navbar overflow on mobile" → Enter.
2. Chat: "The navbar items wrap to a second line on screens under 375px.
   Here's the CSS..." — the AI proposes a fix.
3. Approve the file change. Commit. Run tests.
4. `Ctrl+Shift+P` → "Task: Mark Active as Done".

Total overhead: ~10 seconds to create and close the task. You get a
record of what you did and when.

### Switching between tasks

Working on two things? `Ctrl+Shift+P` → "Task: Switch Active..." →
pick from the list. The chat loads that task's conversation, the AI
re-scopes to the new task's checklist, and your git branch context
updates.

### Blocking a task

Waiting on an API key from the backend team?

1. `Ctrl+Shift+P` → "Task: Block Active Task..."
2. Type the reason: "Waiting on Stripe API key from @alex"
3. The task moves to **BLOCKED**. The Today panel shows it as a red
   attention row so you remember to follow up.

---

## Typical lifecycle (summary)

| Step | What you do | What happens |
|------|-------------|--------------|
| **Create** | `+` button or palette | Task card appears, becomes active |
| **Plan** | Click "Generate checklist" | AI creates ordered steps |
| **Build** | Chat + edit + commit | AI stays scoped, timeline logs commits |
| **Test** | Run tests | Pass/fail recorded on task |
| **Review** | Run AI review | Findings logged, AI can query them |
| **PR** | Open PR | State → REVIEW, Today shows PR |
| **Done** | Palette → Mark Done | State → DONE |
| **Archive** | Right-click → Archive | Hidden from sidebar |

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
