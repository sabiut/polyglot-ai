# Troubleshooting and FAQ

## Where does the app store its data?

```
~/.config/polyglot-ai/
├── polyglot.db          ← main app DB: conversations, usage, settings
├── tasks.sqlite         ← task workflow DB
├── settings.json        ← UI and editor settings
├── mcp.json             ← MCP server configs (non-secret parts)
└── audit.log            ← security-sensitive actions
```

API keys and secret env vars live in your OS keyring, not in any file
in this directory.

## Backup and restore

- Close the app.
- Copy the whole `~/.config/polyglot-ai/` directory somewhere safe.
- To restore, put it back and re-launch.

Keyring secrets are **not** in that directory. Use
`Settings → Providers → Export keys` if you need to migrate them, or
re-enter them by hand on the new machine.

## Reset

- **Reset tasks only** — delete `tasks.sqlite`.
- **Reset everything except secrets** — delete the whole config dir.
- **Nuke keyring entries** — use your OS keyring manager to remove the
  `polyglot-ai` entries.

---

## Common issues

### The app doesn't launch

- Run from a terminal to see the error: `.venv/bin/polyglot-ai`.
- Check Python version: must be 3.11+.
- Check PyQt6 installed: `pip show PyQt6`.

### "No module named 'polyglot_ai'"

You're running from outside the virtualenv. Activate it:

```bash
source .venv/bin/activate
```

Or call the venv's Python directly: `.venv/bin/python -m polyglot_ai`.

### Chat shows "No providers configured"

Open **Settings → Providers** and set an API key for at least one
provider. Click **Test connection**.

### Chat reply cuts off

- Check the model's context limit vs your conversation length.
- Try a more capable model.
- Start a new conversation if this one is huge.

### MCP server won't connect

- Click the server row in the sidebar — the error from stderr is shown.
- Common causes: binary not on `PATH`, wrong args, missing env var,
  server crashing at startup.
- Try running the exact command in a terminal to see what happens.

### Git panel shows "Not a git repo"

The project root isn't a git worktree. Run `git init` in a terminal or
re-open a folder that is one.

### Tests panel empty

Pytest collection failed. Expand the output pane at the bottom of the
panel to see the collection error. Usually a missing test dependency or
an `ImportError` in a `conftest.py`.

### CI panel empty

- `gh` isn't installed.
- `gh auth login` hasn't been run.
- The current project isn't a GitHub repo.

### "Permission denied" on Docker panel

Your user isn't in the `docker` group. On Linux:

```bash
sudo usermod -aG docker $USER
# then log out and back in
```

### Kubernetes panel shows no contexts

`kubectl` isn't installed or `~/.kube/config` is missing/empty.

### Task didn't record my commit

- Did you commit via the Git panel? Commits made from an external
  terminal are not seen.
- Was a task active at the time? Check the Tasks sidebar — the active
  task has a blue border.

### Chat didn't switch when I clicked a different task

- If the task has no `chat_session_id` yet, the chat panel leaves the
  current conversation alone. The next new conversation will bind to it.
- If the task *does* have one, switching should work. Try clicking the
  task again.

### Task Detail dialog closes after I click "Mark done"

Intentional — see the note in [Tasks and Today](Tasks-and-Today). Reopen
by double-clicking the card.

---

## FAQ

### Is my data sent anywhere?

Only the exact API requests you make to the AI provider you configured.
Nothing else leaves your machine. There is no telemetry, analytics, or
background upload.

### Can I use a local model?

Yes, via an MCP server or an OpenAI-compatible endpoint. Set the base URL
in the OpenAI provider config to your local server (Ollama, LM Studio,
vLLM, llama.cpp server, …).

### Can I use multiple projects at once?

One project per window. You can open multiple windows — each has its own
task manager scope. Chat conversations are shared across windows.

### Does it work on Windows / macOS?

The app targets Linux first but uses PyQt6 (cross-platform) and avoids
Linux-only APIs where possible. Terminal, Docker, and Kubernetes panels
are lightly tested on macOS. Windows support is best-effort — some panels
may need path adjustments.

### Where do I file bugs or feature requests?

The project's GitHub issues. Use the templates for bug and feature.

### How do I contribute?

See `CONTRIBUTING.md` in the repo root. The short version:

1. Fork, branch, commit.
2. Run the tests: `.venv/bin/pytest`.
3. Open a PR. The review engine will comment on your diff automatically.

### Is there a plugin system?

Not yet. MCP is the nearest thing — custom MCP servers give you full
tool extensibility without touching the app itself. A first-party plugin
API is on the roadmap.

### What's the "plan" feature in the Tasks detail dialog?

The model carries an ordered list of plan steps; the UI renders them
read-only. An AI plan generator to populate them automatically is the
next thing on the workflow roadmap. Until then the section stays empty
unless you populate it yourself via the scripting API.

### Can I script the app?

Yes — anything in `polyglot_ai.core.*` is importable. Example:

```python
from polyglot_ai.core.task_store import TaskStore
from polyglot_ai.core.tasks import TaskState

store = TaskStore()
for t in store.list_tasks("/path/to/project", state_filter=[TaskState.ACTIVE]):
    print(t.title, t.branch, t.pr_url)
```

UI modules depend on a running Qt event loop and aren't safe to import
from arbitrary scripts.
