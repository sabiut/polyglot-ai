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

- Spawns your preferred shell (configurable in **Settings → Terminal**).
- Starts in the project directory when a project is open.
- Each new project open restarts the terminal in the new directory.
- Supports ANSI colours, scrollback, copy/paste (`Ctrl+Shift+C` / `Ctrl+Shift+V`).
- You can run multiple terminals via the `+` button.

The terminal is a real PTY, so interactive commands (vim, ssh, docker
attach, etc.) work.

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
