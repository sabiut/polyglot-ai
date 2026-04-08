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
- **Tasks tie it together.** Create a task for the work you're doing and
  every panel re-scopes to it. When you come back tomorrow you can pick up
  the branch, the conversation, the test history, and the PR in one click.
- **MCP support.** Bring your own tools via any Model Context Protocol
  server — the AI can use them without code changes.
- **Privacy first.** Nothing leaves your machine except the explicit API
  calls you make to your chosen AI provider.

---

## Quick tour

1. **Open a project**: `File → Open Project`. The file explorer, git panel,
   tests panel, CI panel, and task manager all re-scope to the new project.
2. **Chat**: press `Ctrl+Shift+A` and ask anything. Use `@` to reference a
   file, drag-and-drop a file onto the chat to attach it.
3. **Create a task**: press `Ctrl+Shift+J`, click **+**, type what you're
   working on. From now on commits, test runs, and PRs are all linked to it.
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
