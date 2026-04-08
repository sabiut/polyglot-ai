# Chat

The Chat panel is the AI surface of Polyglot AI. It lives in a dockable
panel on the right side of the window and is available from anywhere in the
app. Toggle it with `Ctrl+Shift+A`.

## Anatomy

```
┌─────────────────────────────────────────────┐
│ AI ASSISTANT                        [+ New] │  ← header
├──────────────┬──────────────────────────────┤
│              │                              │
│ Conversation │         Messages             │
│   sidebar    │          area                │
│              │                              │
│  [search]    │                              │
│  [All|Work|  │                              │
│   Personal|  │                              │
│   Research]  │                              │
│              │                              │
│  • Conv 1    │                              │
│  • Conv 2    │                              │
│  • Conv 3    ├──────────────────────────────┤
│              │  model ▾   attach  send (▶) │  ← input
└──────────────┴──────────────────────────────┘
```

## Providers and models

The model dropdown aggregates every model exposed by your connected
providers. Built-in providers:

- **OpenAI** (API key or OpenAI OAuth subscription login)
- **Anthropic** (API key)
- **Google** (Gemini API key)

Set keys in **Settings → Providers**. Keys are stored in the OS keyring.

### Switching model mid-conversation

You can change the model at any time. The same conversation continues with
the new model. This is useful for starting broad with a powerful model, then
dropping to a cheaper one for follow-up detail work.

## Conversations

Every message exchange is part of a **conversation** that is persisted in
the main app database. The sidebar on the left of the chat panel lists your
conversations, grouped by category.

### Sidebar actions
- **Search** — fuzzy search over titles and message contents.
- **Category filter** — All / Work / Personal / Research. Right-click a
  conversation to assign it a category.
- **+ New** — start a fresh conversation. If a task is active, the new
  conversation is bound to the task on first save.
- **Right-click** a conversation for: rename, pin, delete, export to
  markdown.

### Branching

You can **branch** a conversation from any assistant reply (right-click the
message → *Branch from here*). A new conversation is created, seeded with
every message up to and including the branch point. Use this to explore
"what if I asked it differently" without losing your current thread.

### Session restore

When you close the app, the currently open conversation is remembered. Next
launch the same conversation is loaded. To disable, turn off
**Settings → Chat → Restore last conversation**.

## Messages

Messages are rendered with full markdown (code blocks, tables, lists,
links) plus syntax highlighting. Long outputs are streamed token-by-token.

### Message actions

Hover over a message and you'll see a toolbar:

- **Copy** — copy the message content (raw markdown).
- **Copy code block** — each code block has its own copy button.
- **Retry** (on user messages) — resend, replacing the assistant reply.
- **Edit** (on user messages) — edit and re-send.
- **Branch from here** — see above.
- **Delete** — remove the message (pair of user + assistant).

### Stop streaming

The send button turns into a red stop button while the model is streaming.
Press it to cancel. The partial reply is kept.

## Context: @mentions, attachments, drag-and-drop

### @-mentions

Type `@` in the input to bring up the file picker. Start typing a filename
and it fuzzy-matches against every file in the project. Picking a file
inserts a reference — the file's content is attached to the next message
the AI sees (without pasting it in the chat UI).

### Drag and drop

Drag any file from your OS file manager (or from the file explorer panel)
into the chat area. A drop overlay appears. Release to attach. Images are
sent as vision inputs if the model supports them.

### Manual attach

Click the paperclip icon to open a file picker.

### Clearing attachments

Attachments show as chips above the input row. Click the `×` on a chip to
remove one, or press `Esc` to clear all pending attachments.

## Tools

The AI can invoke **tools** to take action. Tools come from three places:

1. **Built-in tools**: file read/write, listing, grep, ripgrep, run shell
   commands in the sandbox, git operations, fetch, think, etc.
2. **MCP tools**: whatever the servers in `Settings → MCP` expose. See
   **[MCP Servers](MCP-Servers)**.
3. **Project-aware tools**: wired in once a project is open
   (ToolRegistry) — these know the project root and refuse operations
   outside it.

### Approval policy

Destructive or sensitive tools require approval. A modal dialog shows the
tool name, arguments, and a preview. You can:

- **Approve once** — run this invocation, but ask again next time.
- **Approve always for this session** — skip for the rest of the session.
- **Reject** — tell the AI the tool call was denied (it will continue
  reasoning).

The policy engine is configurable per project in **Settings → Tool policy**.

### Sandbox

Shell commands run inside a sandbox rooted at the project directory. The
sandbox enforces:

- **Read confinement** to the project root (symlink escapes blocked).
- **Write confinement** to the project root (tempdirs and `/tmp` allowed).
- **No network** by default.
- **No arbitrary `sudo`**.

See `src/polyglot_ai/core/sandbox.py` for the exact policy.

## Plans

For complex asks, the AI can emit a **plan** — a checklist of steps the
chat panel will execute in order. The plan appears in a dedicated **Plan**
sidebar panel with:

- Step name + description
- Status: pending / running / done / failed
- Per-step output

You can pause, resume, or cancel a plan from its header. Failed steps can
be retried without re-running the successful ones.

## MCP integration

When MCP is connected, the chat panel automatically includes the MCP tool
definitions in the tool list it sends to the model. Reconnects trigger a
refresh on the GUI thread so tools become available on the next message.

See **[MCP Servers](MCP-Servers)**.

## Task integration

If a **task** is active, the chat panel:

- Injects a task block into the system prompt (title, kind, description).
- On first persist, writes the conversation's ID to the task's
  `chat_session_id` so activating the task again reloads this conversation.
- Records every assistant reply on the task timeline (a lightweight
  `ai_response` note).

See **[Tasks and Today](Tasks-and-Today)**.

## Exporting

Right-click a conversation → **Export**. Saves to markdown with:

- Title, created-at timestamp, model used
- Full message history
- Tool calls collapsed as code blocks
- Attachments listed by filename

## Usage tracking

Every completion writes token counts and cost to the usage database. See
**[Settings → Usage dashboard](Settings-and-Shortcuts#usage-dashboard)** to
see spend per day, per provider, per model.

## Tips

- **Use `@`** rather than pasting file contents — it keeps the chat UI
  readable and avoids duplicated context.
- **Start a new conversation per task**, or let the task workflow do it for
  you. Chat panels with 500+ messages get slow and run up the token bill.
- **Use branching** to explore alternatives instead of editing and losing
  history.
- **Pin long-running conversations** so they don't get pushed down the list.
- **Set a category** per conversation so the filter buttons actually help.
