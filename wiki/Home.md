# Polyglot AI — User Guide

Welcome to the **Polyglot AI** wiki. Polyglot AI is an AI-native desktop
workbench for software, infrastructure, and data teams. It bundles a
multi-provider AI chat, a code editor, a terminal, and first-class panels
for git, tests, CI/CD, Docker, Kubernetes, databases, MCP servers, and
diff review — all stitched together with a task-based workflow so your
work stays in context.

This wiki is the full user guide. Pick a section below.

---

## Contents

### Getting started
- **[Getting Started](Getting-Started)** — install, first launch, onboarding wizard, opening a project

### AI
- **[Chat](Chat)** — multi-provider chat, conversations, tools, attachments, @mentions, plans
- **[MCP Servers](MCP-Servers)** — connect Model Context Protocol servers, browse their tools
- **[Review](Tests-and-Review#review)** — AI code review on diffs, branches, and PRs

### Coding
- **[Editor, Terminal, Files, Search](Editor-Terminal-Files)** — the code workspace
- **[Git and PRs](Git-and-PR)** — commits, branches, PR description generator, diff review
- **[Tests](Tests-and-Review#tests)** — pytest explorer with live output and AI fix

### Infrastructure
- **[CI / Docker / Kubernetes](CI-Docker-Kubernetes)** — inspect runs, containers, and clusters
- **[Database](Database)** — multi-engine explorer, query runner, schema browser

### Workflow
- **[Tasks and Today](Tasks-and-Today)** — the workflow layer that ties everything together
- **[Settings and Keyboard Shortcuts](Settings-and-Shortcuts)**
- **[Troubleshooting and FAQ](Troubleshooting-FAQ)**

---

## What makes Polyglot AI different

- **Multi-provider by default.** OpenAI, Anthropic, and Google are wired in
  out of the box. Keys are stored in your OS keyring, not in plaintext.
- **Panels, not just a chat box.** Each domain (git, tests, CI, k8s, etc.)
  has a dedicated panel that the AI can drive, not just a blob of text.
- **The AI sees your panels.** Run a security review in the Review panel
  and the chat's next turn automatically sees the finding counts and top
  issues — no task required, no copy/paste. Drill into specifics via the
  `get_review_findings` tool. See **[Tests and Review](Tests-and-Review)**.
- **Tasks tie it together (optionally).** Create a task for the work
  you're doing and every panel re-scopes to it. Or don't — everything
  works standalone.
- **MCP support.** Bring your own tools via any Model Context Protocol
  server — the AI can use them without code changes.
- **Privacy first.** Nothing leaves your machine except the explicit API
  calls you make to your chosen AI provider.

## Recent additions

- **🔍 Docker Compose Security** review mode — scans `docker-compose*.yml`
  for hardcoded secrets, privileged containers, docker.sock mounts, DB
  ports on `0.0.0.0`, missing `cap_drop`, and more.
- **Panel state in the system prompt** — the chat can now see the most
  recent review without the user pasting anything.
- **🔓 Bootstrap mode** in the chat header — relaxes `shell_exec` approval
  for 15 minutes so scaffolding commands (`npm install`, `pip install`,
  `go mod tidy`) don't prompt per command.
- **Empty-project directive** — when you open a blank folder, the chat
  is forced to ask which stack you want before scaffolding anything.
- **Inline quick-create** for tasks — click `+` in the Tasks sidebar,
  type a title, press Enter. No modal dialog.
- **Non-modal Task Detail window** with a ⛶ maximize button — keep it
  open alongside chat, review, and git while you work.
- **`Task:` command palette entries** — every task operation is
  reachable via `Ctrl+Shift+P`.
- **Today panel is now alerts-only** — the duplicate task list was
  removed; use the Tasks sidebar or `🗂 View all tasks` quick action.

---

## Quick tour

1. **Open a project**: `File → Open Project`. The file explorer, git panel,
   tests panel, CI panel, and task manager all re-scope to the new project.
2. **Chat**: press `Ctrl+Shift+A` and ask anything. Use `@` to reference a
   file, drag-and-drop a file onto the chat to attach it.
3. **Create a task** (optional): press `Ctrl+Shift+J`, click **+**, type
   what you're working on, press Enter. From now on commits, test runs,
   and PRs are all linked to it. Skip this step entirely if you just
   want one-shot chat or a single review.
4. **Run tests**: press `Ctrl+Shift+T`. Click a failing test to jump to the
   assertion, or use **Fix with AI** to hand the failure back to the chat.
5. **Open a PR**: in the git panel, click **Generate PR description** and
   then **Create PR**. The task moves to REVIEW automatically.

For a deeper walkthrough, start with **[Getting Started](Getting-Started)**.

---

## Layout at a glance

```
┌──┬──────────────┬───────────────────┬────────────┐
│A │              │                   │            │
│c │   Sidebar    │    Editor /       │    Chat    │
│t │  (selected   │    Preview /      │   Panel    │
│i │   panel)     │    Notebook       │            │
│v │              │                   │            │
│i │              │                   │            │
│t │              ├───────────────────┤            │
│y │              │     Terminal      │            │
└──┴──────────────┴───────────────────┴────────────┘
```

- **Activity bar** (far left): switches which panel is showing in the sidebar.
- **Sidebar**: the currently selected panel (Today, Tasks, Files, Search,
  Git, MCP, Database, Docker, Kubernetes, Tests).
- **Editor**: tabbed code editor + previews + notebook.
- **Terminal**: integrated shell at the bottom. Toggle with `` Ctrl+` ``.
- **Chat**: always-available AI panel on the right. Toggle with `Ctrl+Shift+A`.

---

## License and project status

Polyglot AI is under active development. See the repository README for
license and contribution information. The wiki tracks the current release.
