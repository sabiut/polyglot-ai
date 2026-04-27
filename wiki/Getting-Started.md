# Getting Started

This page walks you through installing Polyglot AI, launching it for the
first time, configuring an AI provider, and opening your first project.

## Requirements

- **Python 3.11+**
- **Qt 6** (installed automatically via PyQt6)
- A terminal (bash, zsh, or fish) — only needed so the integrated terminal can use your preferred shell
- An API key for at least one AI provider — OpenAI, Anthropic, or Google
- Optional: `git`, `gh`, `docker`, `kubectl`, `pytest`, a database client. Panels that need these will tell you when they're missing.

## Install

Clone the repo and install in a virtualenv:

```bash
git clone https://github.com/<you>/polyglot-ai.git
cd polyglot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

> The project uses `pyproject.toml` with an entry point called `polyglot-ai`.
> After `pip install -e .` the command is on your `PATH` inside the venv.

### Alternative: Nix devshell

If you have [Nix](https://nixos.org/download.html) with flakes enabled,
the repo ships a `flake.nix` with everything wired up — Qt6, Python 3.11,
Node.js (for `npx` MCP servers), `uv` (for `uvx`), `ruff`, `pre-commit`,
`gh`, and the project itself in editable mode:

```bash
git clone https://github.com/<you>/polyglot-ai.git
cd polyglot-ai
nix develop          # drops you into a fully-set-up shell
polyglot-ai          # launches the app
```

The first `nix develop` creates `.venv` and runs `pip install -e ".[dev]"`
once. Later shells reuse it. With [direnv](https://direnv.net) +
[nix-direnv](https://github.com/nix-community/nix-direnv) the shell
auto-activates on `cd` (the repo includes an `.envrc`).

## Launch

From the project root:

```bash
.venv/bin/polyglot-ai
```

or equivalently:

```bash
.venv/bin/python -m polyglot_ai
```

The first launch creates the app's config directory at
`~/.config/polyglot-ai/` (containing the main database, the task database,
settings, and audit log).

## First-run onboarding

On the very first launch, an **Onboarding** wizard appears. It asks you for
an OpenAI API key (the quickest way to get up and running). Skip it if you
prefer to use a different provider — you can set keys later from
**Settings → Providers**.

Keys are stored in your OS keyring via the `keyring` package, **not** in
plaintext config. If your OS doesn't have a keyring backend, the app falls
back to an encrypted file in the config directory.

## Set up an AI provider

Open **Settings** (`Ctrl+,`) → **Providers**. For each provider you want to
enable:

1. Paste your API key.
2. Click **Save**.
3. Click **Test connection**. You should see a green status.

Supported providers out of the box:

| Provider    | Notes                                              |
|-------------|----------------------------------------------------|
| OpenAI      | API key or OpenAI OAuth (subscription login).      |
| Anthropic   | API key.                                           |
| Google      | Gemini API key.                                    |

Additional providers can be added via the MCP client — see
**[MCP Servers](MCP-Servers)**.

## Pick a model

In the chat panel, the model picker at the top of the input row lists every
model available from your configured providers. Your most-used model is
remembered between launches. A short "desc" for each model gives you a hint
about which one to pick for the task at hand.

## Open a project

**File → Open Project** → pick a folder.

When you open a project:

- The **File Explorer** roots itself at the folder.
- The **Search** panel indexes it (shows a progress bar in the status bar).
- **Git**, **Tests**, **CI/CD** panels scope themselves to the repo.
- The **Task Manager** switches to that project's task list and
  auto-activates the most recent ACTIVE/PLANNING/REVIEW task, if any.
- The **Terminal** restarts in the project directory.
- `session.last_project` is saved so next launch auto-restores it.

You can switch projects at any time without restarting the app — everything
re-scopes on the fly.

## Your first chat

1. Press `Ctrl+Shift+A` to show the chat panel (it's open by default).
2. Pick a model in the top dropdown.
3. Type a message. Use `@` to reference a file in your project —
   autocomplete appears as you type.
4. Drag-and-drop a file from your OS file manager onto the chat to attach
   it.
5. Press `Enter` to send. Stop a stream in progress with the red stop button.

The AI has access to a set of built-in tools (file read/write, run
commands in a sandbox, search, git, etc.), plus any MCP tools you have
connected. It will ask for approval before running destructive actions.

## Your first task

Press `Ctrl+Shift+J` to open the **Tasks** sidebar, then click **+**:

- **Kind**: what you're doing (Feature, Bugfix, Incident, Refactor, Explore, Chore).
- **Title**: one line, e.g. "Add CSV export to user reports".
- **Description**: optional. Fed into the system prompt so the AI has context.

See **[Tasks and Today](Tasks-and-Today)** for the full workflow.

## What's next?

- Learn the chat panel in detail: **[Chat](Chat)**
- Wire up git: **[Git and PRs](Git-and-PR)**
- Run your test suite from the app: **[Tests](Tests-and-Review#tests)**
- Set up automatic code review: **[Review](Tests-and-Review#review)**
