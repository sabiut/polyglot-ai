# Editor, Terminal, Files, Search

The code workspace — where you actually write, read, and navigate your project.

## Editor panel

The editor is a tabbed code editor based on `QPlainTextEdit` with syntax
highlighting, line numbers, and AI-powered inline completions.

### Features
- **Tabs** — multiple open files. `Ctrl+W` to close, `Ctrl+Tab` to cycle.
- **Syntax highlighting** — Python, JS/TS, Go, Rust, HTML/CSS, Markdown, YAML, JSON, SQL, shell, and more.
- **Line numbers** — always visible, click to place cursor.
- **Find / Replace** — `Ctrl+F` to find, `Ctrl+H` to replace. Supports regex.
- **Go to line** — `Ctrl+G`.
- **Save** — `Ctrl+S`. Save-all: `Ctrl+Shift+S`.
- **Preview** — Markdown files and HTML files get a preview tab.
- **Notebook support** — `.ipynb` files open in a dedicated notebook tab.

### Inline completions

When enabled in **Settings → Editor → Inline completions**, the editor
requests a short completion from the active AI provider as you type. A
greyed-out suggestion appears; press `Tab` to accept, `Esc` to dismiss.

### Refactoring preview

Ask the AI to refactor a file (via chat) and you get a diff preview in a
dedicated tab. Accept applies the change, reject drops it. The diff viewer
supports side-by-side and unified modes.

## Terminal panel

An integrated terminal at the bottom of the window. Toggle with `` Ctrl+` ``.
It's a real PTY-backed shell (uses `pty.fork()`), so interactive programs
like vim, ssh, htop, and `docker attach` work.

### Basics
- **Shell** — configurable in **Settings → Terminal**; defaults to
  `/bin/bash`. If the configured shell doesn't exist, the terminal
  prints a red error line instead of sitting blank.
- **Starting directory** — when a project is open, the terminal starts
  in that directory.
- **Opening a different project** sends `cd <newpath>` to the running
  shell rather than tearing it down — any running process, scrollback,
  and shell history survive the switch. The `cd` command is submitted
  with a leading space so bash/zsh `HISTCONTROL=ignorespace` keeps it
  out of your recall history.
- **Scrollback** — 5,000 lines of history. A slim indicator on the
  right edge shows your position whenever the buffer has grown.
- **Rendering** — ANSI/VT100 via pyte, with full 16-color, 256-color,
  and SGR (bold/italic/underline/reverse) support. Honors the app's
  dark/light theme for default foreground and background.

### Copy / paste
| Gesture | What it does |
|---|---|
| Click-drag | Select text; auto-copies to clipboard on release |
| Double-click | Select word; auto-copies |
| Triple-click | Select whole line; auto-copies |
| `Ctrl+Shift+C` | Copy current selection (or full visible screen if none) |
| `Ctrl+Shift+V` | Paste clipboard (bracket-paste mode — newlines don't auto-execute) |
| Middle-click | Paste X11 primary selection (falls back to clipboard on Wayland) |
| Drag-drop file | Pastes the shell-quoted absolute path at the cursor |
| Right-click → **Copy All (with Scrollback)** | Copies the full history buffer + visible screen |

### Navigation and scrollback
| Gesture | Action |
|---|---|
| Mouse wheel | Scroll the scrollback view by 3 lines per tick |
| `Shift+PageUp` / `Shift+PageDown` | Page through scrollback without sending keys to the shell |
| Any other keystroke | Snaps the view back to the latest output |

### Keyboard mappings
- **Arrows / Home / End / PageUp / PageDown / Insert / Delete** — standard VT220 escape sequences
- **F1–F4** — SS3-encoded (standard VT220)
- **F5–F12** — CSI-encoded (xterm-compatible). Works with `htop`, `mc`, `nano`.
- **Ctrl+C / Ctrl+D / Ctrl+Z / Ctrl+L** — standard terminal control
- **Tab** — always sent to the shell (Qt focus-navigation is suppressed)

### Font zoom
| Shortcut | Action |
|---|---|
| `Ctrl+=` (or `Ctrl++`) | Zoom in one size |
| `Ctrl+-` | Zoom out one size |
| `Ctrl+0` | Reset to default (11pt) |

Zoom range is clamped 7pt–24pt. Changing the font recalculates the
grid dimensions and resizes the PTY, so programs running inside the
shell see the updated geometry immediately (try it with `htop`).

### Integration
- **`Ctrl+click` a URL** in the visible output to open it in your
  default browser. Matches `http(s)://…` and `file://…`.
- **Right-click → Send selection to AI…** takes whatever you've
  highlighted, frames it as terminal-output context, and prefills the
  chat input with a prompt asking the AI to explain/debug it.
  Great for stack traces, build errors, cryptic exit codes.
- **Visual bell** — a brief red flash on genuine BEL (`\a`). Only
  fires for real bells; the `\x07` used as an OSC string terminator
  (e.g. bash's title-setting `PROMPT_COMMAND`) is handled silently by
  the parser and does NOT flash.

### Cursor
- **Block cursor** with blink while the terminal has focus
- **Hollow outline** when unfocused — the position is still visible
  but the blink pauses so you're not distracted by motion in a panel
  you're not typing into

## File Explorer

Open with `Ctrl+Shift+E` or the first activity-bar icon (after Today/Tasks).

- **Tree view** of the project root. Respects `.gitignore` by default.
- **Double-click** a file to open it in the editor.
- **Right-click** for a context menu: new file, new folder, rename, delete, reveal in terminal, copy path.
- **Drag and drop** a file into the chat panel to attach it.
- **New file** shortcut: right-click → New file, or `Ctrl+N` when the explorer is focused.

The tree watches the filesystem and refreshes automatically when files
change outside the app.

## Search panel

Open with `Ctrl+Shift+F`.

- **Content search** across the project using ripgrep (falls back to a
  Python implementation if `rg` isn't installed).
- **Glob filters** for include/exclude (e.g. `*.py`, `!tests/**`).
- **Case sensitivity**, **whole word**, **regex** toggles.
- **Results tree** — click to jump to the file and line.
- **Replace** (batch) — preview replacements, then apply.

A semantic search index is built in the background when a project opens
(via the RAG indexer). The AI uses this for @-mention suggestions and for
"relevant files" context.

## Tips

- Put `.gitignore` in the project root — both the file explorer and search
  respect it.
- Use the **command palette** (`Ctrl+Shift+P`) for anything you can't find
  in a menu.
- Drag files out of the explorer into the chat to attach them without
  pasting content.
