# Polyglot AI

AI-powered coding assistant for Linux — multi-provider desktop IDE with OpenAI, Anthropic, Google, and xAI support.

![Polyglot AI Screenshot](docs/images/screenshot.png)

## Features

- **Multi-provider AI chat** — OpenAI, Anthropic (Claude), Google (Gemini), xAI (Grok) with streaming responses
- **Integrated code editor** — Syntax highlighting via QScintilla, multi-tab editing
- **Built-in terminal** — Full PTY terminal emulator
- **AI tool calling** — File read/write/search, shell execution, git operations
- **Command palette** — Quick actions with Ctrl+Shift+P
- **Git panel** — Branch view, staging, commits
- **Code review** — AI-powered diff review with inline comments
- **Plan mode** — Structured step-by-step development plans
- **Prompt templates** — Built-in templates for code review, debugging, refactoring
- **RAG indexing** — TF-IDF project indexer for context-aware responses
- **Inline completions** — AI-powered code suggestions
- **Token usage dashboard** — Track costs across providers
- **MCP integration** — Connect external tools via Model Context Protocol
- **Session restore** — Save and restore workspace state
- **Conversation branching** — Fork conversations to explore alternatives

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

- Python 3.11+
- Linux (X11 or Wayland)
- Qt 6.6+

## License

LGPL-3.0-or-later
