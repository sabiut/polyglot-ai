"""MCP (Model Context Protocol) client integration.

Connects to external MCP servers to discover and use their tools,
extending the AI assistant's capabilities beyond built-in tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Pre-built MCP Server Catalog ─────────────────────────────────
# Users can connect these with one click from the MCP marketplace.

# ── Pre-built MCP Server Catalog ─────────────────────────────────
# All packages are pinned to specific versions to prevent supply-chain attacks.
# WARNING: MCP servers run as local code with your user permissions.
# Update versions only after verifying package integrity and publisher.

MCP_CATALOG = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "icon": "📁",
        "description": "Secure file access with configurable permissions",
        "command": "npx",
        # Pinned: auto-updating on every launch is a supply-chain risk.
        # Bump this version intentionally after testing a newer release.
        "args": ["-y", "@modelcontextprotocol/server-filesystem@2026.1.14"],
        "config_fields": [
            {
                "key": "path",
                "label": "Allowed Directory",
                "type": "directory",
                "description": "Directory the server can access",
            },
        ],
    },
    {
        "id": "git",
        "name": "Git",
        "icon": "🔀",
        "description": "Read, search, and manipulate Git repositories",
        "command": "uvx",
        "args": ["mcp-server-git==2026.1.14"],
        "config_fields": [],
    },
    {
        "id": "memory",
        "name": "Memory",
        "icon": "🧠",
        "description": "Persistent knowledge graph for long-term memory",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory@2026.1.26"],
        "config_fields": [],
    },
    {
        "id": "github",
        "name": "GitHub",
        "icon": "🐙",
        "description": "Manage GitHub repositories, issues, and PRs",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
        "config_fields": [
            {
                "key": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "label": "GitHub Token",
                "type": "password",
                "description": "Personal access token from github.com/settings/tokens",
            },
        ],
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "icon": "🌐",
        "description": "Fetch and convert web content for AI usage (requires uvx)",
        "command": "uvx",
        "args": ["mcp-server-fetch==2025.4.7"],
        "config_fields": [],
    },
    {
        "id": "postgres",
        "name": "PostgreSQL",
        "icon": "🗄️",
        "description": "Query PostgreSQL databases (read-only)",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres@2025.1.14"],
        "config_fields": [
            {
                "key": "connection_string",
                "label": "Connection String",
                "type": "password",
                "description": "postgresql://user:pass@host:5432/dbname (stored securely, passed via env)",
            },
        ],
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "icon": "💭",
        "description": "Dynamic problem-solving through thought sequences",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@2025.12.18"],
        "config_fields": [],
    },
    {
        "id": "playwright",
        "name": "Playwright",
        "icon": "🎭",
        "description": "Browser automation and web interaction",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@0.0.70", "--headless"],
        "config_fields": [],
    },
    {
        "id": "gitlab",
        "name": "GitLab",
        "icon": "🦊",
        "description": "Manage GitLab repositories and workflows",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-gitlab@2025.4.25"],
        "config_fields": [
            {
                "key": "GITLAB_PERSONAL_ACCESS_TOKEN",
                "label": "GitLab Token",
                "type": "password",
                "description": "Personal access token from gitlab.com/-/user_settings/personal_access_tokens",
            },
        ],
    },
    {
        "id": "mysql",
        "name": "MySQL",
        "icon": "🐬",
        "description": "Query MySQL databases",
        "command": "npx",
        "args": ["-y", "@benborla29/mcp-server-mysql@0.1.1"],
        "config_fields": [
            {
                "key": "MYSQL_HOST",
                "label": "Host",
                "type": "text",
                "description": "MySQL server hostname (e.g. localhost)",
            },
            {
                "key": "MYSQL_USER",
                "label": "User",
                "type": "text",
                "description": "MySQL username",
            },
            {
                "key": "MYSQL_PASSWORD",
                "label": "Password",
                "type": "password",
                "description": "MySQL password (stored securely in keyring)",
            },
            {
                "key": "MYSQL_DATABASE",
                "label": "Database",
                "type": "text",
                "description": "Database name to connect to",
            },
        ],
    },
]


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str  # e.g. "python", "node", "npx"
    args: list[str] = field(default_factory=list)  # e.g. ["-m", "my_server"]
    env: dict[str, str] | None = None
    enabled: bool = True


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""

    server_name: str
    name: str
    description: str
    input_schema: dict


class MCPClient:
    """Manages connections to MCP servers and exposes their tools."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._sessions: dict[str, Any] = {}  # server_name -> ClientSession
        self._transports: dict[str, Any] = {}  # server_name -> transport context
        self._tools: dict[str, MCPTool] = {}  # qualified_name -> MCPTool
        self._connected: set[str] = set()
        #: Callables invoked (sync) after any connect/disconnect completes
        #: so UI components can refresh their cached tool lists. Signature:
        #: ``callback() -> None``. Exceptions are logged, not re-raised.
        self._connection_change_listeners: list = []

    def add_connection_change_listener(self, callback) -> None:
        """Register a listener called after each connect/disconnect.

        Used by the chat panel to refresh its snapshot of available tool
        definitions when MCP servers come online or go offline.

        IMPORTANT: listeners are invoked on whatever thread the connect
        or disconnect call is running on. If your listener touches Qt
        widgets, the caller is responsible for marshalling the work
        onto the GUI thread (e.g. via ``QTimer.singleShot(0, cb)`` or
        a queued ``pyqtSignal``).
        """
        self._connection_change_listeners.append(callback)

    def _notify_connection_change(self) -> None:
        for cb in list(self._connection_change_listeners):
            try:
                cb()
            except Exception:
                logger.exception("MCP connection-change listener raised")

    def add_server(self, config: MCPServerConfig) -> None:
        """Register an MCP server configuration."""
        self._servers[config.name] = config
        logger.info(
            "Registered MCP server: %s (%s %s)", config.name, config.command, " ".join(config.args)
        )

    def remove_server(self, name: str) -> None:
        """Remove and disconnect an MCP server."""
        if name in self._connected:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.disconnect(name))
            except RuntimeError:
                # No running loop — skip async disconnect, cleanup will
                # happen on app shutdown via disconnect_all()
                logger.debug("No event loop for disconnect of %s; deferred to shutdown", name)
        self._servers.pop(name, None)
        # Remove its tools
        to_remove = [k for k, v in self._tools.items() if v.server_name == name]
        for k in to_remove:
            del self._tools[k]

    async def connect(self, server_name: str) -> bool:
        """Connect to an MCP server and discover its tools."""
        config = self._servers.get(server_name)
        if not config or not config.enabled:
            return False

        # Validate command and args against allowlist
        from polyglot_ai.core.security import validate_mcp_command

        allowed, reason = validate_mcp_command(config.command, args=config.args)
        if not allowed:
            logger.warning("MCP server '%s' blocked: %s", server_name, reason)
            return False

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # Resolve command — check if it exists on PATH, fall back to venv
            command = config.command
            if command in ("uvx", "uv"):
                import shutil
                import sys

                if not shutil.which(command):
                    # Try the venv's bin directory
                    venv_cmd = Path(sys.executable).parent / command
                    if venv_cmd.exists():
                        command = str(venv_cmd)
                    else:
                        logger.warning("'%s' not found on PATH or in venv", command)

            # Build a minimal environment — don't inherit everything from parent
            import os as _os

            safe_env = {}
            # Inherit only essential system env vars
            for k in (
                "PATH",
                "HOME",
                "USER",
                "LANG",
                "LC_ALL",
                "TERM",
                "SHELL",
                "TMPDIR",
                "XDG_RUNTIME_DIR",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "NODE_PATH",
            ):
                if k in _os.environ:
                    safe_env[k] = _os.environ[k]
            # Add configured env vars (secrets restored from keyring)
            if config.env:
                safe_env.update(config.env)

            params = StdioServerParameters(
                command=command,
                args=config.args,
                env=safe_env,
            )

            # Create the stdio transport with a 3-minute timeout
            # (npx/uvx can be slow on first run — downloads packages)
            import asyncio as _asyncio

            try:
                transport_ctx = stdio_client(params)
                transport = await _asyncio.wait_for(transport_ctx.__aenter__(), timeout=180)
            except _asyncio.TimeoutError:
                logger.error(
                    "MCP server '%s' startup timed out after 180s. "
                    "First-run package install may take longer — try again.",
                    server_name,
                )
                return False
            read_stream, write_stream = transport

            # Create session — newer MCP SDK versions require async context
            # manager entry for the session as well.
            session_ctx = ClientSession(read_stream, write_stream)
            try:
                session = await session_ctx.__aenter__()
            except Exception:
                try:
                    await transport_ctx.__aexit__(None, None, None)
                except _asyncio.CancelledError:
                    raise
                except Exception as cleanup_err:
                    logger.warning(
                        "MCP '%s': transport cleanup failed after session enter error: %s",
                        server_name,
                        cleanup_err,
                    )
                raise

            try:
                await _asyncio.wait_for(session.initialize(), timeout=60)
            except _asyncio.TimeoutError:
                logger.error("MCP server '%s' initialize timed out", server_name)
                await self._cleanup_ctxs(server_name, session_ctx, transport_ctx)
                return False
            except Exception:
                await self._cleanup_ctxs(server_name, session_ctx, transport_ctx)
                raise

            self._sessions[server_name] = session
            self._transports[server_name] = (transport_ctx, session_ctx)
            self._connected.add(server_name)

            # Discover tools
            tools_result = await session.list_tools()
            for tool in tools_result.tools:
                qualified_name = f"mcp_{server_name}_{tool.name}"
                self._tools[qualified_name] = MCPTool(
                    server_name=server_name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                )

            logger.info(
                "Connected to MCP server '%s': %d tools discovered",
                server_name,
                len(tools_result.tools),
            )
            self._notify_connection_change()
            return True

        except ImportError:
            logger.warning("MCP SDK not installed. Run: pip install mcp")
            return False
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            logger.error(
                "Failed to connect to MCP server '%s': %s", server_name, sanitize_error(str(e))
            )
            return False

    async def _cleanup_ctxs(
        self,
        server_name: str,
        session_ctx,
        transport_ctx,
    ) -> None:
        """Best-effort cleanup of an MCP session+transport pair.

        Logs (not swallows) each cleanup failure so orphaned subprocesses,
        leaked fds, and anyio cancel-scope errors leave a diagnostic trail.
        Always attempts both closes even if the first raises.
        """
        import asyncio as _asyncio

        try:
            await session_ctx.__aexit__(None, None, None)
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("MCP '%s': session cleanup failed: %s", server_name, e)
        try:
            await transport_ctx.__aexit__(None, None, None)
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("MCP '%s': transport cleanup failed: %s", server_name, e)

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from an MCP server."""
        import asyncio as _asyncio

        if server_name in self._sessions:
            try:
                self._sessions.pop(server_name)
                contexts = self._transports.pop(server_name, None)
                if contexts:
                    # Now a tuple of (transport_ctx, session_ctx) — exit both
                    if isinstance(contexts, tuple):
                        transport_ctx, session_ctx = contexts
                        await self._cleanup_ctxs(server_name, session_ctx, transport_ctx)
                    else:
                        # Legacy single-context path
                        try:
                            await contexts.__aexit__(None, None, None)
                        except _asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.warning(
                                "MCP '%s': legacy context cleanup failed: %s",
                                server_name,
                                e,
                            )
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                from polyglot_ai.core.security import sanitize_error

                logger.warning(
                    "Error disconnecting MCP server '%s': %s", server_name, sanitize_error(str(e))
                )
            self._connected.discard(server_name)

            # Remove its tools
            to_remove = [k for k, v in self._tools.items() if v.server_name == server_name]
            for k in to_remove:
                del self._tools[k]
            logger.info("Disconnected from MCP server: %s", server_name)
            self._notify_connection_change()

    async def connect_all(self) -> None:
        """Connect to all enabled servers, notifying listeners when done."""
        for name, config in self._servers.items():
            if config.enabled and name not in self._connected:
                await self.connect(name)
        # Fire once at the end of the batch so listeners don't get a storm
        # of refreshes mid-connect. Individual connect()/disconnect() calls
        # also fire so sidebar / chat panel reflect state immediately.
        self._notify_connection_change()

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for name in list(self._connected):
            await self.disconnect(name)

    def get_tool_definitions(self) -> list[dict]:
        """Get OpenAI function-calling format definitions for all MCP tools."""
        definitions = []
        for qualified_name, tool in self._tools.items():
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": qualified_name,
                        "description": f"[MCP:{tool.server_name}] {tool.description}",
                        "parameters": tool.input_schema
                        or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            )
        return definitions

    async def call_tool(self, qualified_name: str, arguments: dict) -> str:
        """Call an MCP tool by its qualified name."""
        tool = self._tools.get(qualified_name)
        if not tool:
            return f"Error: Unknown MCP tool '{qualified_name}'"

        session = self._sessions.get(tool.server_name)
        if not session:
            return f"Error: MCP server '{tool.server_name}' is not connected"

        try:
            result = await session.call_tool(tool.name, arguments=arguments)
            # Extract text content from result
            if hasattr(result, "content") and result.content:
                texts = []
                for part in result.content:
                    if hasattr(part, "text"):
                        texts.append(part.text)
                    else:
                        texts.append(str(part))
                output = "\n".join(texts)
            else:
                output = str(result)

            # Redact secrets from tool output — two passes:
            # 1. Pattern-based credential redaction (Bearer, api_key, etc.)
            # 2. Content-based secret scanning (AWS keys, private keys, etc.)
            from polyglot_ai.core.security import (
                redact_secrets_in_content,
                redact_sensitive_output,
            )

            output = redact_sensitive_output(output)
            output = redact_secrets_in_content(output)
            return output
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            logger.error(
                "MCP tool call failed: %s/%s: %s",
                tool.server_name,
                tool.name,
                sanitize_error(str(e)),
            )
            return "Error calling MCP tool. See logs for details."

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to an MCP server."""
        return tool_name in self._tools

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connected)

    @property
    def available_tools(self) -> dict[str, MCPTool]:
        return dict(self._tools)

    def get_server_configs(self) -> list[MCPServerConfig]:
        return list(self._servers.values())

    def install_from_catalog(
        self, catalog_id: str, config_values: dict | None = None
    ) -> MCPServerConfig:
        """Install a server from the built-in catalog and save to config.

        Returns the MCPServerConfig that was created.
        """
        entry = next((e for e in MCP_CATALOG if e["id"] == catalog_id), None)
        if not entry:
            raise ValueError(f"Unknown catalog server: {catalog_id}")

        # Build env from config values (e.g. API tokens)
        env: dict[str, str] = {}
        args = list(entry["args"])
        if config_values:
            for cf in entry.get("config_fields", []):
                val = config_values.get(cf["key"], "")
                if val:
                    if cf["type"] == "directory":
                        # Filesystem server takes path as trailing arg
                        args.append(val)
                    elif cf["key"] == "connection_string":
                        # SECURITY: pass connection strings via env var, NOT
                        # as command-line args (visible in `ps` output).
                        env["DATABASE_URL"] = val
                    else:
                        # API tokens go in env
                        env[cf["key"]] = val

        config = MCPServerConfig(
            name=catalog_id,
            command=entry["command"],
            args=args,
            env=env if env else None,
            enabled=True,
        )
        self.add_server(config)
        self._save_config()
        return config

    def uninstall_server(self, server_name: str) -> None:
        """Remove a server and save config."""
        self.remove_server(server_name)
        self._save_config()

    # Env var names that are explicitly non-secret (safe to store in plaintext)
    _NON_SECRET_ENV_KEYS = frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TERM",
            "NODE_ENV",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "SHELL",
            "DISPLAY",
            "XDG_RUNTIME_DIR",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LOG_LEVEL",
            "DEBUG",
            "VERBOSE",
            "CI",
            "PORT",
            "HOST",
        }
    )

    # Patterns that indicate an env var value is likely a secret
    _SECRET_NAME_PATTERNS = (
        "_KEY",
        "_TOKEN",
        "_SECRET",
        "_PASSWORD",
        "_CREDENTIAL",
        "_AUTH",
        "_PASS",
        "API_KEY",
        "ACCESS_KEY",
        "PRIVATE",
        "BEARER",
        "CONNECTION_STRING",
        "DATABASE_URL",
        "DB_URL",
    )

    @classmethod
    def _is_secret_env(cls, key: str) -> bool:
        """Determine if an env var should be treated as a secret.

        Uses deny-by-default: anything not explicitly non-secret is
        stored in keyring if its name matches secret-like patterns.
        """
        upper = key.upper()
        if upper in cls._NON_SECRET_ENV_KEYS:
            return False
        # Check against secret-like name patterns
        for pattern in cls._SECRET_NAME_PATTERNS:
            if pattern in upper:
                return True
        # Default: treat as secret if it looks like it holds a credential
        # (contains alphanumeric value longer than 16 chars — heuristic)
        return True

    def _save_config(self) -> None:
        """Persist current server configs to disk. Secrets go to keyring."""
        import keyring
        from polyglot_ai.core.security import secure_write

        config_path = Path.home() / ".config" / "polyglot-ai" / "mcp_servers.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict = {"servers": {}}
        for name, cfg in self._servers.items():
            safe_env = {}
            for k, v in (cfg.env or {}).items():
                if self._is_secret_env(k) and v:
                    # Store in keyring, save placeholder in JSON
                    keyring.set_password("polyglot-ai-mcp", f"{name}/{k}", v)
                    safe_env[k] = "__KEYRING__"
                else:
                    safe_env[k] = v
            data["servers"][name] = {
                "command": cfg.command,
                "args": cfg.args,
                "env": safe_env,
                "enabled": cfg.enabled,
            }
        secure_write(config_path, json.dumps(data, indent=2))
        logger.info("Saved MCP config to %s", config_path)


#: Seeded on first run: catalog servers with no required config_fields
#: (no API keys, no OAuth). sequential-thinking and memory need `npx` (Node);
#: fetch needs `uvx` (uv). If either runtime is missing the corresponding
#: server simply fails to connect and the rest still work.
_DEFAULT_SEED_SERVER_IDS = ("sequential-thinking", "memory", "fetch")


def _seed_default_config(config_path: Path) -> list[MCPServerConfig]:
    """Create a default MCP config on first run and return the seeded servers."""
    from polyglot_ai.core.security import secure_write

    servers: list[MCPServerConfig] = []
    seed_dict: dict[str, dict] = {}

    for server_id in _DEFAULT_SEED_SERVER_IDS:
        entry = next((e for e in MCP_CATALOG if e["id"] == server_id), None)
        if entry is None:
            continue
        # Skip any server that requires config fields (e.g. API keys) —
        # seeding should never require interaction.
        if entry.get("config_fields"):
            continue
        servers.append(
            MCPServerConfig(
                name=server_id,
                command=entry["command"],
                args=list(entry["args"]),
                env={},
                enabled=True,
            )
        )
        seed_dict[server_id] = {
            "command": entry["command"],
            "args": list(entry["args"]),
            "env": {},
            "enabled": True,
        }

    if not seed_dict:
        return []

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        secure_write(config_path, json.dumps({"servers": seed_dict}, indent=2))
        logger.info(
            "Seeded default MCP config at %s with: %s",
            config_path,
            ", ".join(seed_dict.keys()),
        )
        return servers
    except OSError as e:
        # Write failed — return [] so the caller sees no seeded servers.
        # Retrying on every launch would be confusing; the user can
        # manually add servers from the marketplace.
        logger.error(
            "Could not persist default MCP config to %s (%s). "
            "New users will need to add servers from Settings → MCP Marketplace.",
            config_path,
            e,
        )
        return []


def load_mcp_config(config_path: Path | None = None) -> list[MCPServerConfig]:
    """Load MCP server configurations from a JSON file.

    Default path: ~/.config/polyglot-ai/mcp_servers.json

    Format:
    {
        "servers": {
            "my-server": {
                "command": "python",
                "args": ["-m", "my_mcp_server"],
                "env": {"API_KEY": "..."},
                "enabled": true
            }
        }
    }
    """
    if config_path is None:
        config_path = Path.home() / ".config" / "polyglot-ai" / "mcp_servers.json"

    if not config_path.exists():
        # First run: seed a default config with safe, no-auth servers so new
        # users get a useful experience out of the box.
        return _seed_default_config(config_path)

    # Validate config file security before reading (may contain keyring refs)
    from polyglot_ai.core.security import check_secure_file

    secure, reason = check_secure_file(config_path)
    if not secure:
        if config_path.is_symlink():
            logger.error("Refusing to read MCP config: symlinked file %s", config_path)
            return []
        # Try to fix permissions on regular files we own
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
        secure, reason = check_secure_file(config_path)
        if not secure:
            logger.warning("MCP config file has insecure permissions: %s", reason)

    try:
        import keyring

        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = []
        for name, cfg in data.get("servers", {}).items():
            env = cfg.get("env") or {}
            # Restore secrets from keyring
            for k, v in env.items():
                if v == "__KEYRING__":
                    secret = keyring.get_password("polyglot-ai-mcp", f"{name}/{k}")
                    env[k] = secret or ""
            servers.append(
                MCPServerConfig(
                    name=name,
                    command=cfg.get("command", ""),
                    args=cfg.get("args", []),
                    env=env,
                    enabled=cfg.get("enabled", True),
                )
            )
        return servers
    except Exception as e:
        from polyglot_ai.core.security import sanitize_error

        logger.error("Failed to load MCP config from %s: %s", config_path, sanitize_error(str(e)))
        return []
