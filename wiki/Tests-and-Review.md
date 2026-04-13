# Tests and Review

Two panels that help you catch problems before they ship: the **Tests**
panel (pytest explorer) and the **Review** panel (AI code review).

---

## Tests

Open with `Ctrl+Shift+T`. The tests panel is a pytest test explorer
with live output and AI-assisted fixing.

### Discovery

On project open the panel runs `pytest --collect-only -q` in the project
root and builds a tree:

```
tests/
 └── core/
      ├── test_tasks.py
      │    ├── test_task_new_generates_id_and_created_note
      │    ├── test_test_run_snapshot_all_green
      │    └── ...
      └── test_settings.py
```

Refresh button re-runs collection.

### Running tests

- **Click a test** → selects it.
- **Run** button runs the selection (file, class, or single test).
- **Run all** runs the whole suite.
- **Re-run failed** runs only the tests that failed in the last run.

Pytest is launched via `subprocess.Popen` and streamed to the output pane
in real time. Pass/fail status shows inline with green/red dots next to
each test.

### Jump to failure

Click a failed test → the editor opens the file and jumps to the
assertion line. The failure output is shown in the panel's output pane.

### Fix with AI

On any failed test: click **Fix with AI**. The panel sends the chat:

- The test source
- The failure output
- The module under test (resolved via import)

The chat opens with a drafted prompt asking for a fix.

### Task integration

After a run, the panel writes a `TestRunSnapshot` onto the active task
(`passed` / `failed` / `skipped` / `timestamp`) and appends a `tested`
note. The sidebar card then shows e.g. `12/13 tests`.

### Out of scope (for now)

- Coverage reporting
- Parameterized test expansion in the tree
- Debugging integration
- Non-pytest frameworks (unittest, hypothesis-only, etc.)

---

## Review

The Review panel runs the AI review engine on a diff. Open via the
activity bar (Review icon) or from the Git panel.

### Review modes

Diff modes:

- **Working Changes** — unstaged modifications.
- **Staged Changes** — what's about to be committed.
- **Branch vs Main** — every commit on this branch.

IaC security scan modes (run against files, not diffs):

- **🔍 Terraform Security** — `.tf` files, cloud misconfig + IAM.
- **🔍 Kubernetes Security** — manifests, pod security, RBAC.
- **🔍 Dockerfile Security** — container image best practices.
- **🔍 Docker Compose Security** — `docker-compose*.yml` / `compose*.yml`,
  checks for hardcoded secrets, `privileged: true`, host networking,
  docker.sock bind mounts, DB ports on `0.0.0.0`, missing `cap_drop`,
  `:latest` tags, resource limits, and more.
- **🔍 Helm Chart Security** — Chart.yaml, values, templates.

### Running a review

Pick a mode, click **Run review**. The engine:

1. Extracts the diff via `git`.
2. Collects project context (IaC files, recently-modified files, dependencies).
3. Sends everything to the configured provider with a structured prompt.
4. Parses the JSON response into findings.

Output is streamed into the panel.

### Findings

Each finding has:

- **Severity** — critical / high / medium / low / info.
- **Category** — bug / security / performance / breaking / style.
- **File + line range** — clickable to jump into the editor.
- **Explanation** — what's wrong.
- **Suggestion** — how to fix it (with a code snippet when possible).

Findings can be filtered by severity and category.

### Review profiles

You can configure multiple review profiles (same diff, different system
prompt emphasis) in **Settings → Review → Profiles**. Common profiles:

- **Bug risk** — focus on logic bugs, null handling, race conditions.
- **Security** — focus on injection, secrets, auth.
- **Performance** — focus on hot paths, memory, query counts.
- **Breaking change** — focus on API compatibility and migrations.
- **Readability** — focus on naming, structure, comments.

### The AI can see review results

Every time a review finishes, the panel publishes a compact snapshot to
`core.panel_state`. Two things then happen automatically on your next
chat turn:

1. **`--- PANEL STATE ---` block in the system prompt.** The chat's
   system prompt gains a compact block naming the review mode, files
   scanned, finding counts, and the top 5 most severe findings. The
   model can answer "what did the last review find?" without you
   pasting anything.

2. **`get_review_findings` tool.** An auto-approved, read-only tool the
   model can call to drill into findings by severity or file. Arguments:
   - `severity` — `critical` / `high` / `medium` / `low` / `info`, or
     the shorthand `high+` (matches critical + high)
   - `file` — substring match against the finding's file path
   - `limit` — default 20, max 100

**No task is required** for any of this. Run a review in an empty
project with no tasks, ask the chat "show me the criticals", done.

### Task integration

When a task IS active, running a review also logs a note on its
timeline:

- `review_clean` (no findings)
- `review_findings` (some findings)
- `review_failed` (engine error)

### Copy results

Click the **📋 Copy** button in the header bar to copy the full review
as formatted Markdown. The output includes:

- Stats (files reviewed, additions, deletions, finding counts)
- Summary paragraph
- All findings grouped by severity (critical → info), each with:
  - File and line reference
  - Category
  - Description
  - Suggestion (if any)

The button is enabled after a successful review and flashes **✅ Copied!**
as confirmation. Paste into issues, Slack, PRs, or documentation.

### Tips

- **Review branch vs main** right before opening a PR — catches the
  problems that would otherwise generate review comments.
- **Use a tight profile** when you have a specific concern — the default
  "everything" profile can be noisy on large diffs.
- **Don't skip low-severity findings** on security reviews. "Low" on a
  security scan often means "low confidence", not "low impact".
