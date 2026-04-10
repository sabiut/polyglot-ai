# Settings and Keyboard Shortcuts

## Settings dialog

Open with `Ctrl+,`. Settings are persisted to
`~/.config/polyglot-ai/settings.json` and take effect immediately.

### Sections

#### General
- **Theme** — Dark / Light. Toggle also available in the View menu.
- **Restore last project on startup** — default on.
- **Restore last conversation** — default on.
- **Onboarding done** — set automatically; toggle off to re-run the
  onboarding wizard.

#### Providers
One row per AI provider (OpenAI, Anthropic, Google). Each row has:
- API key field (paste once, stored in keyring).
- Test connection button.
- Default model dropdown.

OpenAI also has a **Sign in with OpenAI** button for subscription OAuth.

#### Editor
- **Font family** / **font size**.
- **Tab width**.
- **Insert spaces** (vs tabs).
- **Show whitespace**.
- **Inline completions** — on/off.
- **Auto-save on focus loss**.

#### Terminal
- **Shell** — path to your preferred shell (`/bin/bash`, `/usr/bin/zsh`, …).
- **Font family** / **font size**.
- **Scrollback lines**.

#### Chat
- **Default model**.
- **Max context messages** — how many past messages to include.
- **Restore last conversation on startup**.
- **Streaming** — default on.

#### MCP
See **[MCP Servers](MCP-Servers)**. Add / edit / remove server configs,
set environment variables, reconnect.

#### Tool policy
Configure which tools auto-approve and which always require prompting.
Per-project policies can be saved.

#### Review
Manage **review profiles** — bug risk, security, performance, breaking
change, readability.

#### Database
Manage database connections. Credentials are stored in the OS keyring.

#### Usage dashboard
Not a setting, but accessible here: a view of token spend per day, per
provider, per model. Backed by the usage table in the main app DB.

---

## Keyboard shortcuts

### View / navigation

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+H` | Today panel |
| `Ctrl+Shift+J` | Tasks sidebar |
| `Ctrl+Shift+E` | File explorer |
| `Ctrl+Shift+F` | Search |
| `Ctrl+Shift+G` | Git |
| `Ctrl+Shift+M` | MCP servers |
| `Ctrl+Shift+D` | Database |
| `Ctrl+Shift+K` | Docker |
| `Ctrl+Shift+8` | Kubernetes |
| `Ctrl+Shift+T` | Tests |
| `Ctrl+Shift+I` | CI/CD inspector |
| `Ctrl+Shift+A` | Toggle AI chat |
| `` Ctrl+` `` | Toggle terminal |
| `Ctrl+Shift+P` | Command palette |
| `Ctrl+,` | Settings |

### Editor

| Shortcut | Action |
|---|---|
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save all |
| `Ctrl+W` | Close tab |
| `Ctrl+Tab` | Next tab |
| `Ctrl+F` | Find |
| `Ctrl+H` | Replace |
| `Ctrl+G` | Go to line |
| `Ctrl+N` | New file (explorer focused) |

### Chat

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+N` | New conversation |
| `Enter` | Send message |
| `Shift+Enter` | Newline in input |
| `Esc` | Clear pending attachments / dismiss completion |

### Git

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Commit (in commit message field) |
| `Ctrl+Shift+Enter` | Commit and push |

### Tasks

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+J` | Open Tasks sidebar |
| `Ctrl+Shift+H` | Open Today panel |
| Click `+` in sidebar | Toggle inline quick-create row |
| `Enter` (in quick-create) | Create task with default kind |
| `Esc` (in quick-create) | Cancel quick-create |
| Double-click a card | Open Task Detail window (non-modal) |
| Right-click a card | Context menu |
| `⛶` in detail title row | Toggle maximize of the detail window |

### Command palette task entries

`Ctrl+Shift+P` → type `Task:` to see all of these:

| Entry | What it does |
|---|---|
| **Task: New** | Show Tasks sidebar, focus the inline quick-create row |
| **Task: New (with kind/description)** | Open the full new-task dialog |
| **Task: Switch Active…** | Pick a task to make active from a list |
| **Task: Open Active Task Detail** | Open the detail window for the active task |
| **Task: Mark Active as Done** | Transition the active task → DONE |
| **Task: Block Active Task…** | Prompt for a blocker reason and mark BLOCKED |
| **Task: Show Tasks Panel** | Reveal the Tasks sidebar |
| **Task: Show Today Panel** | Reveal the Today panel |

> There's no direct shortcut for *Task: New* because `Ctrl+Shift+T`
> is the Tests panel. Bind one in your own config if you want, or use
> the palette.

### Chat panel header

| Button | Action |
|---|---|
| **🔓 Bootstrap** | Relax `shell_exec` approval for 15 min (scaffolding) — see [Chat › Bootstrap mode](Chat.md#bootstrap-mode) |
| **+ New** | New conversation |

## Tips

- Most shortcuts can be discovered via the menu bar — the accelerator is
  shown next to each action.
- The activity bar also shows shortcuts in its tooltips.
