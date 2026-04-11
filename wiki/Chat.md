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

### Bootstrap mode

Greenfield projects need a lot of shell commands up front — `npm
install`, `pip install -r`, `go mod tidy`, `cargo new`, etc. Approving
each one individually is friction. The **🔓 Bootstrap** button in the
chat panel header relaxes `shell_exec` approval for **15 minutes** so
scaffolding commands run without dialogs.

- Click **🔓 Bootstrap** → the button flips to **🔒 Bootstrap · M:SS**
  with an amber background and a live countdown.
- Click again (or let it expire) to revert.
- **Only `shell_exec`** is relaxed. Everything else that normally
  requires approval (`git_commit`, `db_query`, mutating docker/k8s)
  still prompts.
- The window is tracked via monotonic clock so system-clock changes
  can't extend or shrink it.

### Empty-project directive

When you open a truly empty folder (no `pyproject.toml` / `package.json`
/ `go.mod` / etc. and no source files), the chat system prompt gains
this directive:

> *"This project directory is empty or has no recognisable source
> files. BEFORE calling create_plan or writing any files, you MUST ask
> the user which stack/framework they want."*

This stops the model from silently scaffolding Next.js or Django when
you had a specific stack in mind. Pair it with Bootstrap mode for a
smooth "build me X" experience — answer the stack question, approve
the plan, click 🔓 Bootstrap, let the scaffolding run.

### Sandbox

Shell commands run inside a sandbox rooted at the project directory. The
sandbox enforces:

- **Read confinement** to the project root (symlink escapes blocked).
- **Write confinement** to the project root (tempdirs and `/tmp` allowed).
- **No network** by default.
- **No arbitrary `sudo`**.

See `src/polyglot_ai/core/sandbox.py` for the exact policy.

## Workflows

Workflows are **repeatable multi-step AI investigations** defined in YAML.
Instead of typing a sequence of prompts manually, you define them once and
run them with a single command. Each step is injected into the chat as a
user message — the AI responds using tools (Playwright, shell, K8s,
Docker, DB, Git) as needed, then the engine advances to the next step.

### Running a workflow

| Command | Description |
|---------|-------------|
| `/workflow` | List all available workflows |
| `/workflow seed` | Copy bundled defaults into `.polyglot/workflows/` |
| `/workflow verify-deploy --url https://staging.example.com` | Run a workflow with inputs |

### Bundled workflows

Four workflows ship out of the box:

- **verify-deploy** — Navigate to a URL, take a screenshot, check for
  console/network errors, and summarize pass/fail. Requires Playwright MCP.
- **investigate-failure** — Check CI status, inspect K8s pods and logs,
  review recent commits, and correlate into a root-cause report.
- **reproduce-bug** — Plan reproduction steps, execute them in the browser,
  capture evidence, and write a structured bug report.
- **record-test** — Describe a test scenario in plain English, the AI
  analyzes the website, executes the scenario in the browser step by step,
  generates production-ready Playwright test code (Python or TypeScript),
  and saves it to your project. See [Record Test](#record-test-workflow)
  below for details.

### Writing your own

Create a `.yml` file in `.polyglot/workflows/` inside your project:

```yaml
# .polyglot/workflows/hello-test.yml
name: Hello Test
description: Simple test workflow
inputs:
  - name: greeting
    description: What to say
    default: "Hello World"
steps:
  - name: Greet
    prompt: "Say this greeting to the user: {{greeting}}"
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Display name shown in `/workflow` listing |
| `description` | no | Short summary shown in the listing |
| `inputs` | no | List of input parameters |
| `inputs[].name` | yes | Variable name (used in `{{name}}` placeholders) |
| `inputs[].description` | no | Shown when prompting for missing inputs |
| `inputs[].required` | no | Default `true`. If true and not provided, workflow won't start |
| `inputs[].default` | no | Fallback value when the user doesn't pass `--name value` |
| `steps` | yes | Ordered list of steps |
| `steps[].name` | yes | Step label shown in the chat |
| `steps[].prompt` | yes | Template string — `{{variable}}` placeholders are replaced with input values |

### How it works under the hood

1. `/workflow name --key value` parses the command and loads the YAML
2. Input validation fills defaults and checks required fields
3. For each step: the prompt template is rendered → injected as a user
   message → `_stream_response()` runs the full tool-calling loop → the
   AI responds with tool calls as needed
4. On completion a `workflow_run` note is attached to the active task
5. The chat input is locked during execution (a guard prevents double-send)

Project-local workflows in `.polyglot/workflows/` **override** bundled
defaults with the same filename.

### Record Test workflow

The `record-test` workflow turns plain-English test descriptions into
production-ready Playwright code. The AI autonomously analyzes the target
website, executes the scenario, and saves the test file to your project.

**Example:**
```
/workflow record-test --url https://myapp.com --scenario "login with admin/pass, search for laptop, add cheapest to cart, go to checkout"
```

For TypeScript output:
```
/workflow record-test --url https://myapp.com --scenario "sign up flow" --language typescript
```

**What happens (5 steps):**

| Step | What the AI does |
|------|-----------------|
| 1. Analyze page and plan | Navigates to the URL, screenshots it, inspects the DOM, and adapts the plan to the **actual page** — if the scenario says "login" but the button says "Sign in", it uses "Sign in" |
| 2. Execute scenario | Runs each action with Playwright, taking screenshots. Adapts on the fly — handles modals/popups, scrolls to find elements, tries alternative selectors if needed |
| 3. Capture evidence | Compiles a structured action log with Playwright locators (`get_by_role`, `get_by_label`), wait conditions, and assertions |
| 4. Generate test code | Produces a complete Playwright test file with configurable credentials, base URL, proper fixtures, and robust locators |
| 5. Save to project | Writes the file to your `tests/` directory and tells you how to install dependencies and run it |

**Key feature — adaptive execution:** The AI doesn't follow a script
blindly. It analyzes the actual website, adapts to what it sees, handles
unexpected popups, and retries with alternative selectors when elements
aren't found.

**Provider compatibility:** All providers (OpenAI, Anthropic, Google)
support workflows. For best results with browser automation workflows,
use a strong model — GPT-4o, GPT-4.1, Claude Sonnet 4, or Gemini 2.5
Pro handle multi-step tool calling most reliably.

### Tips

- **Playwright workflows** (verify-deploy, reproduce-bug, record-test)
  need the Playwright MCP server connected in the MCP panel.
- **Keep steps focused.** Each step is a single AI turn — don't try to
  pack too much into one prompt.
- **Use defaults** for optional inputs so workflows are quick to run
  without flags.
- **Stronger models = better automation.** For complex multi-page
  browser scenarios, use GPT-4o/4.1, Claude Sonnet/Opus, or Gemini 2.5
  Pro. Smaller models may struggle with long tool-calling sequences.

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

## Panel awareness (the AI sees what you see)

The chat's system prompt is automatically enriched with two kinds of
cross-panel context — **neither requires a task to be active**:

1. **`ACTIVE TASK` block** — when a task is active: title, kind, state,
   branch, description, checklist, files touched.
2. **`PANEL STATE` block — most recent code review** — after any run in
   the Review panel the chat's next turn sees mode, file list, finding
   counts, top 5 most severe findings, and a nudge to call
   `get_review_findings` for drill-down.

The `get_review_findings` tool is auto-approved and standalone — it
reads from `core.panel_state` and returns JSON filtered by `severity`
(including the `high+` shorthand) and/or `file` substring. See
[Tests and Review](Tests-and-Review.md) for the full contract.

## Task integration

If a **task** is active, the chat panel additionally:

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
