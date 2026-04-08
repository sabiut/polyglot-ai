# Git and Pull Requests

The Git panel is the source-control workspace. Open it with `Ctrl+Shift+G`.

## Status view

Top of the panel shows:

- **Current branch** and upstream tracking status.
- **Ahead / behind** counts vs the upstream.
- **Staged / modified / untracked** file lists.

Click a file to see its diff in the editor. Right-click for stage / unstage
/ revert / delete actions.

## Staging and committing

- Check a file to stage it, or use **Stage all**.
- Enter a commit message.
- **Commit** — standard commit.
- **Commit and push** — one-click push to upstream.
- **Amend last commit** — adds staged changes to the previous commit.

Every commit is recorded on the active task (if any) as a `committed`
note with the sha and message. The task's `modified_files` field is
updated.

## Branches

- **Create branch** — pick a name and base. Switches to it on success.
- **Switch branch** — dropdown of local branches. Dirty worktree prompts
  you to stash first.
- **Delete branch** — local or remote.

A branch created while a task is active fills in the task's `branch` and
`base_branch` fields automatically.

## Diff review

- **Review current diff** — runs the AI review engine on your working
  changes.
- **Review branch vs main** — runs the review on your whole branch.
- **Review last commit** — runs on `HEAD^..HEAD`.

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
| `Ctrl+Enter` (in commit message) | Commit |
| `Ctrl+Shift+Enter` | Commit and push |

## Tips

- **Commit through the panel** whenever possible — commits made through
  the terminal don't get recorded on the active task.
- **Use "Review branch vs main"** before opening a PR. It catches half the
  comments you'd otherwise get.
- **Generate PR description last**, after everything is pushed — the AI
  draft is only as good as the diff it sees.
