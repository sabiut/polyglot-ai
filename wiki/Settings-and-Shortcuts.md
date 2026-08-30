# Settings and Keyboard Shortcuts

## Settings dialog

Open with `Ctrl+,`. Settings are persisted in the app's local database
and take effect immediately.

The dialog has five sections: **Accounts**, **Editor**, **AI**,
**Terminal**, and **MCP Servers**.

### Sections

#### Accounts & API Keys
One row per AI provider (OpenAI, Anthropic, Google, DeepSeek) with an
API key field and a **Test** button — keys are stored securely in your
system keyring.

OpenAI and Anthropic also offer subscription OAuth sign-in (works with
ChatGPT Plus/Pro/Business/Enterprise and Claude Pro/Max/Team plans).

#### Editor
- **Theme** — Dark / Light. A toggle is also available in the View menu.
- **Font family** / **font size**.
- **Tab width**.
- **Word wrap**.
- **Line numbers**.
- **Inline AI completions** — on/off.

Editor changes apply to tabs opened after saving.

#### Terminal
- **Shell** — path to your preferred shell (`/bin/bash`, `/usr/bin/zsh`, …).
- **Font size**.

#### AI
- **Default model**.
- **Temperature** / **max tokens**.
- **System prompt** — extra instructions appended to every conversation.
- **Notifications** — enable/disable desktop notifications, and set the
  threshold (in seconds) below which a finished AI response doesn't
  trigger one.

#### MCP Servers
See **[MCP Servers](MCP-Servers)**. Add / edit / remove server configs,
set environment variables, reconnect.

### Usage dashboard

Not in the settings dialog — the **Usage** tab in the right-side panel
shows token spend, backed by the usage table in the main app DB.

---

## Environment variables

- **`POLYGLOT_AI_DISABLE_UPDATE_CHECK`** — when set (to any value),
  skips the automatic launch-time update check. Normally the app checks
  for a new release in the background about 8 seconds after launch
  (results are cached for 24 hours) and shows a one-time toast if one is
  available. Intended for distro packagers who ship updates through
  their own repos, and for test suites. The manual
  **Help → Check for Updates…** action still works either way — it
  forces a fresh check and always reports back with a dialog.

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
| `Ctrl+F` | Find |
| `Ctrl+H` | Replace |
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
| `Enter` | Commit (in commit message field) |

### Terminal

See **[Terminal panel](Editor-Terminal-Files#terminal-panel)** for full details.

| Shortcut | Action |
|---|---|
| `` Ctrl+` `` | Toggle terminal visibility |
| `Ctrl+Shift+C` | Copy selection (or full visible screen) |
| `Ctrl+Shift+V` | Paste clipboard (bracket-paste mode) |
| Middle-click | Paste X11 primary selection |
| `Ctrl+=` / `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |
| `Ctrl+0` | Reset zoom to 11pt |
| `Shift+PageUp` / `Shift+PageDown` | Scroll scrollback one page |
| `Ctrl+C` / `Ctrl+D` / `Ctrl+Z` / `Ctrl+L` | Standard terminal control characters |
| Double-click | Select word + copy |
| Triple-click | Select line + copy |
| Drag-drop file | Paste shell-quoted absolute path |
| `Ctrl+click` URL | Open in default browser |
| Right-click | Copy / Paste / Select All / Copy All (with Scrollback) / Send selection to AI… / Zoom |

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
