# Git and Pull Requests

The Git panel is the source-control workspace. Open it with `Ctrl+Shift+G`.

## Status view

Top of the panel shows:

- **Current branch**.
- **Staged changes** and **changes** (unstaged/untracked) file lists.

Double-click a file to see its diff in a side-by-side dialog.
Right-click a file to stage or unstage it.

## Staging and committing

- Right-click a file → **Stage** (or **Unstage** in the staged list), or
  use the **Stage All** / **Unstage All** buttons in the section
  headers.
- Enter a commit message.
- **Commit** — commits the staged changes.
- **Push** — pushes the current branch to origin, setting the upstream
  tracking branch automatically the first time.
- **Pull** / **Fetch** — sync with the remote.

Every commit is recorded on the active task (if any) as a `committed`
note with the sha and message. The task's `modified_files` field is
updated.

## Branches

- **Create branch** — pick a name. Switches to it on success.
- If the active task points at a different branch than the one checked
  out, the branch label shows a clickable hint that checks out the
  task's branch.

A branch created while a task is active fills in the task's `branch` and
`base_branch` fields automatically.

## Diff review

The Review panel (see **[Review](Tests-and-Review#review)**) supports:

- **Working Changes** — runs the AI review engine on your unstaged
  modifications.
- **Staged Changes** — reviews what's about to be committed.
- **Branch vs Main** — reviews your whole branch.

The review engine returns structured findings: bug risks, security issues,
breaking changes, performance concerns, style. Each finding can be clicked
to jump to the offending line. See **[Review](Tests-and-Review#review)**.

## Generate PR description

**Generate PR description** opens a dialog with:

- **Title** — AI-drafted from your diff.
- **Summary** — 3–5 bullets describing what changed.
- **Test plan** — markdown checklist.
- **Risks** — migrations, rollbacks, breaking changes.

You can edit everything before copying. Two actions:

- **Copy to clipboard** — paste into GitHub manually.
- **Create PR with gh** — runs `gh pr create` in the background using the
  edited title/body. Requires `gh` to be installed and authenticated.

If a task is active, opening the PR:

- Writes `pr_url` and `pr_number` onto the task.
- Moves the task's state to `REVIEW`.
- Adds a `pr_opened` note to the timeline.

### Repo PR template

If your repo has a `.github/PULL_REQUEST_TEMPLATE.md`, the generator
conforms to its structure instead of the default layout.

## GitHub integration

`File → Sign in to GitHub` signs in via `gh`. Once signed in:

- PR creation uses your session.
- The chat panel can reference issues and PRs with `@gh#123`.
- The Today panel's **Attention** section lists failed CI runs and open
  PRs on your branches.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+G` | Show Git panel |
| `Enter` (in commit message field) | Commit |

## Tips

- **Commit through the panel** whenever possible — commits made through
  the terminal don't get recorded on the active task.
- **Use "Review branch vs main"** before opening a PR. It catches half the
  comments you'd otherwise get.
- **Generate PR description last**, after everything is pushed — the AI
  draft is only as good as the diff it sees.
