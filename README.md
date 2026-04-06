# Polyglot AI

AI-powered coding assistant for Linux — multi-provider desktop IDE with OpenAI, Anthropic, Google, and xAI support.

![Polyglot AI Screenshot](docs/images/screenshot.png)

## Features

### Core
- **Multi-provider AI chat** — OpenAI, Anthropic (Claude), Google (Gemini), xAI (Grok) with streaming responses
- **Integrated code editor** — Syntax highlighting via QScintilla, multi-tab editing
- **Built-in terminal** — Full PTY terminal emulator
- **AI tool calling** — File read/write/search, shell execution, git operations
- **Command palette** — Quick actions with Ctrl+Shift+P
- **Plan mode** — Structured step-by-step development plans
- **Prompt templates** — Built-in templates for code review, debugging, refactoring
- **RAG indexing** — TF-IDF project indexer for context-aware responses
- **Inline completions** — AI-powered code suggestions
- **Token usage dashboard** — Track costs across providers
- **Session restore & branching** — Save workspace state, fork conversations

### Code review
- **Diff review** — AI-powered review of working changes, staged changes, or branch-vs-main
- **IaC security scans** — One-click security reviews for:
  - 🔍 Terraform (`.tf`, `.tfvars`, `.hcl`)
  - 🔍 Kubernetes manifests (real YAML parsing, not substring matching)
  - 🔍 Dockerfiles
  - 🔍 Helm charts (Chart.yaml, values.yaml, templates)
- **Structured findings** — Severity, category, file:line, suggested fix

### DevOps panels
- **Git panel** — Branch view, staging, commits
- **CI/CD panel** — GitHub Actions workflow runs, job status, live log streaming
- **Docker panel** — Containers, images, logs, start/stop/restart/remove with approval
- **Kubernetes panel** — Pods, deployments, services, logs, scale/delete/apply with approval
- **Database panel** — Direct PostgreSQL / MySQL / SQLite connections with schema explorer and SQL runner

### MCP (Model Context Protocol)
- **Built-in MCP marketplace** — One-click install of Filesystem, Git, Memory, GitHub, Fetch, Sequential Thinking, Playwright, GitLab, MySQL, and more
- **Smart defaults** — On first run, Polyglot AI auto-seeds `sequential-thinking`, `memory`, and `fetch` (no-auth) so deep reasoning works out of the box
- **MCP sidebar** — Live connection status, tool counts, inline connect/disconnect, search filter
- **Sequential-thinking integration** — When the tool is available, the AI is prompted to reason step-by-step on complex tasks

## Install

### From release (recommended)

Download the latest release from [Releases](https://github.com/sabiut/polyglot-ai/releases):

| Format | Platform | Install |
|--------|----------|---------|
| `.deb` | Ubuntu/Debian | `sudo dpkg -i polyglot-ai_*.deb` |
| `.rpm` | Fedora/RHEL | `sudo rpm -i polyglot-ai-*.rpm` |
| `.AppImage` | Any Linux | `chmod +x Polyglot_AI-*.AppImage && ./Polyglot_AI-*.AppImage` |
| `.whl` | pip | `pip install polyglot_ai-*.whl` |

### From source

```bash
git clone https://github.com/sabiut/polyglot-ai.git
cd polyglot-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
polyglot-ai
```

## Configuration

1. Launch the app and open **Settings** (gear icon)
2. Add your API key for at least one provider:
   - OpenAI API key
   - Anthropic API key
   - Google AI API key
   - xAI API key
3. Or sign in with your existing subscription (OpenAI/Claude)

## Requirements

### Core
- Python 3.11+
- Linux (X11 or Wayland)
- Qt 6.6+

### Optional runtimes for MCP servers
Most MCP servers are distributed as Node.js or Python packages. Install whichever runtimes you need for the servers you want to use:

| Runtime | Install | Used by |
|---|---|---|
| **Node.js 20+** (`npx`) | [nodejs.org](https://nodejs.org/) or `sudo apt install nodejs npm` | sequential-thinking, memory, filesystem, github, gitlab, playwright, mysql |
| **uv** (`uvx`) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | fetch, git |

If either runtime is missing, the corresponding MCP servers simply fail to connect — the rest still work. The three defaults seeded on first run (`sequential-thinking`, `memory`, `fetch`) require both `npx` and `uvx`.

### Optional CLIs for DevOps panels
These are only needed if you want to use the corresponding panel:

| Tool | Install | Panel |
|---|---|---|
| `git` | system package | Git panel, CI/CD panel |
| `gh` (GitHub CLI) | [cli.github.com](https://cli.github.com/) | CI/CD panel (GitHub Actions) |
| `docker` | [docs.docker.com](https://docs.docker.com/engine/install/) | Docker panel |
| `kubectl` | [kubernetes.io/docs/tasks/tools](https://kubernetes.io/docs/tasks/tools/) | Kubernetes panel |

## License

LGPL-3.0-or-later
