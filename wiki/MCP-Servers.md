# MCP Servers

**MCP** (Model Context Protocol) is an open standard for letting language
models talk to external tools and data sources via a uniform interface.
Polyglot AI ships with an MCP client so you can plug any MCP-compatible
server into the chat without writing code.

Open the MCP sidebar with `Ctrl+Shift+M`.

## What you can do with MCP

- Give the AI filesystem access via `mcp-server-filesystem`.
- Let it query SQLite/Postgres directly via DB MCP servers.
- Wire up Slack, Gmail, Linear, Notion, Jira, Google Drive (where the
  hosted MCP versions are available).
- Add the `sequentialthinking` server for structured chain-of-thought.
- Connect internal tools your team has written against the MCP spec.

Once a server is connected, its tools appear in the chat's tool list
automatically.

## Adding a server

Two ways:

### 1. From the MCP sidebar
- Click **+ Add server**.
- Fill in: name, command, args, environment variables.
- Save.

### 2. From Settings
- **Settings → MCP → Servers**.
- Same dialog.

### Stdio vs HTTP

Polyglot AI supports stdio MCP servers today (most reference servers).
Streamable HTTP with OAuth is on the roadmap — once shipped, you'll be
able to click **Sign in with Google / Slack / …** to connect hosted
servers directly.

## Environment variables and secrets

When adding a server, you can set env vars inline. The client classifies
them into two buckets:

- **Secret-looking** (names like `*_API_KEY`, `*_TOKEN`, `*_SECRET`) are
  stored in the OS keyring and never written to the config file.
- **Normal** env vars are saved in `~/.config/polyglot-ai/mcp.json`.

On load, keyring secrets are re-injected into the process env before the
server is spawned.

## Connection lifecycle

When you save a server config, the client:

1. Validates the command exists and is on the allowlist for its known
   binary (prevents arbitrary shell injection via the config file).
2. Spawns the subprocess and performs the MCP handshake.
3. Fetches the tool definitions.
4. Fires a connection-change listener, which refreshes the chat panel's
   tool list on the GUI thread.

If the handshake fails, an error is shown in the sidebar with the
stderr output captured from the subprocess.

### Reconnect

Each server row has a **Reconnect** button. The client tears down the
transport cleanly and re-runs the handshake.

## Tool approval

MCP tools are untrusted by default — every call requires user approval
unless you've marked a specific tool as auto-approve for the session. See
**[Chat → Tools → Approval policy](Chat#approval-policy)**.

## Browsing tools

In the MCP sidebar, expand a server to see:

- Tool name
- Short description
- Input schema
- Example invocation (click to send it to the chat)

## Where the config lives

```
~/.config/polyglot-ai/mcp.json          ← non-secret server config
OS keyring entries                       ← secret env vars
```

The file is created with permissions `0600` (owner-only read/write).
The client warns if permissions drift looser than that.

## Removing a server

Click **Delete** in the server row. Secrets in the keyring for that
server are cleaned up too.

## Tips

- **Start with the filesystem and sequentialthinking servers** — they
  give immediate value.
- **Don't connect untrusted MCP servers** — they effectively run with
  your user's permissions. Treat them like any other subprocess.
- **Use env vars for secrets**, not command args — args are visible in
  the process table.
