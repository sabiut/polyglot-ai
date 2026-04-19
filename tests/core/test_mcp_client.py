"""Tests for ``MCPClient`` — MCP server registration, tool discovery, and tool calls.

We do not exercise the real MCP stdio transport here — that would need
a real subprocess and network-like timing. Instead, we inject a fake
session object that implements the two methods the client actually
calls: ``initialize`` (during connect) and ``call_tool`` (during tool
invocation). The goal is to lock in the parts of ``mcp_client`` that
have no coverage today:

* tool-definition building (OpenAI function-calling shape)
* tool-call text extraction from MCP content blocks
* secret redaction on tool output
* error sanitisation on tool-call failure
* config loading — missing / malformed / well-formed files
* ``_is_secret_env`` classification (deny-by-default)
* ``install_from_catalog`` placement of directory / connection string / token values
* connection-change listener isolation (one bad listener can't break others)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polyglot_ai.core.mcp_client import (
    MCP_CATALOG,
    MCPClient,
    MCPServerConfig,
    MCPTool,
    load_mcp_config,
)


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeToolPart:
    """Mimics an MCP content part with a ``.text`` attribute."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeToolResult:
    """Mimics an MCP ``CallToolResult`` object."""

    def __init__(self, parts: list) -> None:
        self.content = parts


class _FakeSession:
    """Captures ``call_tool`` invocations and replays a scripted result.

    ``call_tool`` can be replaced per test with a raising function to
    exercise the error path.
    """

    def __init__(self, result=None, raise_exc: Exception | None = None) -> None:
        self._result = result
        self._raise = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if self._raise is not None:
            raise self._raise
        return self._result


# ── Helpers ──────────────────────────────────────────────────────────


def _make_client_with_tool(
    *,
    session: _FakeSession,
    server_name: str = "srv",
    tool_name: str = "do_thing",
    input_schema: dict | None = None,
) -> tuple[MCPClient, str]:
    """Build a client with one tool already registered against ``session``."""
    client = MCPClient()
    qualified = f"mcp_{server_name}_{tool_name}"
    client._tools[qualified] = MCPTool(
        server_name=server_name,
        name=tool_name,
        description="does a thing",
        input_schema=input_schema or {"type": "object", "properties": {}},
    )
    client._sessions[server_name] = session
    client._connected.add(server_name)
    return client, qualified


# ── get_tool_definitions ────────────────────────────────────────────


def test_get_tool_definitions_uses_openai_function_shape():
    """Definitions must carry the ``type: function`` envelope so they
    drop straight into any provider's ``tools=[]`` argument."""
    client = MCPClient()
    client._tools["mcp_a_foo"] = MCPTool(
        server_name="a",
        name="foo",
        description="foo desc",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )

    defs = client.get_tool_definitions()

    assert len(defs) == 1
    d = defs[0]
    assert d["type"] == "function"
    fn = d["function"]
    assert fn["name"] == "mcp_a_foo"
    # The description is prefixed with the server tag so the model can
    # see which server owns the tool.
    assert fn["description"].startswith("[MCP:a]")
    assert "foo desc" in fn["description"]
    assert fn["parameters"] == {"type": "object", "properties": {"x": {"type": "string"}}}


def test_get_tool_definitions_falls_back_when_schema_missing():
    """A tool with an empty input_schema must still produce a valid
    JSON Schema — providers reject ``parameters`` missing ``type``."""
    client = MCPClient()
    client._tools["mcp_a_foo"] = MCPTool(
        server_name="a",
        name="foo",
        description="",
        input_schema={},
    )
    defs = client.get_tool_definitions()
    assert defs[0]["function"]["parameters"] == {"type": "object", "properties": {}}


# ── call_tool ───────────────────────────────────────────────────────


async def test_call_tool_unknown_name_returns_error_string():
    client = MCPClient()
    result = await client.call_tool("mcp_nope_nothing", {})
    assert "Unknown" in result


async def test_call_tool_disconnected_server_returns_error():
    client = MCPClient()
    client._tools["mcp_srv_foo"] = MCPTool(
        server_name="srv", name="foo", description="", input_schema={}
    )
    # No session registered → treat as not connected
    result = await client.call_tool("mcp_srv_foo", {})
    assert "not connected" in result.lower()


async def test_call_tool_joins_multi_part_text_content():
    """MCP returns content as a list of parts; join with newlines."""
    session = _FakeSession(result=_FakeToolResult([_FakeToolPart("hello"), _FakeToolPart("world")]))
    client, qualified = _make_client_with_tool(session=session)

    out = await client.call_tool(qualified, {"a": 1})

    assert out == "hello\nworld"
    assert session.calls == [("do_thing", {"a": 1})]


async def test_call_tool_stringifies_non_text_parts():
    """A part without ``.text`` must be str()-ed, not dropped silently."""
    weird = SimpleNamespace(some_field="xyz")  # no .text
    session = _FakeSession(result=_FakeToolResult([_FakeToolPart("foo"), weird]))
    client, qualified = _make_client_with_tool(session=session)

    out = await client.call_tool(qualified, {})

    assert "foo" in out
    assert "xyz" in out or "some_field" in out  # from str(SimpleNamespace)


async def test_call_tool_redacts_bearer_tokens_in_output():
    """Tool output that echoes a bearer token must not leak it."""
    leak = "Request: Authorization: Bearer sk-abcdef0123456789abcdef0123456789abcdef"
    session = _FakeSession(result=_FakeToolResult([_FakeToolPart(leak)]))
    client, qualified = _make_client_with_tool(session=session)

    out = await client.call_tool(qualified, {})

    # redact_sensitive_output should mask the credential. Exact token
    # format is redacted away; the string must not still contain the
    # raw secret.
    assert "sk-abcdef0123456789abcdef0123456789abcdef" not in out


async def test_call_tool_error_message_is_generic_and_logged():
    """On session failure, user-visible message is generic (no leaks)."""
    session = _FakeSession(raise_exc=RuntimeError("postgres://user:hunter2@db:5432/prod failed"))
    client, qualified = _make_client_with_tool(session=session)

    out = await client.call_tool(qualified, {})

    # The generic message goes to the user; full error is in logs only.
    assert "Error calling MCP tool" in out
    assert "hunter2" not in out
    assert "postgres://" not in out


# ── is_mcp_tool / connected_servers / available_tools ───────────────


def test_is_mcp_tool_true_only_for_registered_tools():
    client = MCPClient()
    client._tools["mcp_a_foo"] = MCPTool(
        server_name="a", name="foo", description="", input_schema={}
    )
    assert client.is_mcp_tool("mcp_a_foo") is True
    assert client.is_mcp_tool("mcp_b_bar") is False
    assert client.is_mcp_tool("file_read") is False


def test_available_tools_and_connected_servers_return_copies():
    """Callers must not be able to mutate internal state via getters."""
    client = MCPClient()
    client._tools["mcp_a_foo"] = MCPTool(
        server_name="a", name="foo", description="", input_schema={}
    )
    client._connected.add("a")

    tools_view = client.available_tools
    servers_view = client.connected_servers
    tools_view.clear()
    servers_view.clear()

    # Internal state survives external mutation
    assert "mcp_a_foo" in client._tools
    assert "a" in client._connected


# ── Connection change listeners ─────────────────────────────────────


def test_listener_exception_does_not_block_other_listeners():
    """One bad listener must not poison the rest of the dispatch."""
    client = MCPClient()
    called: list[str] = []

    def boom():
        raise RuntimeError("listener bug")

    def ok():
        called.append("ok")

    client.add_connection_change_listener(boom)
    client.add_connection_change_listener(ok)

    client._notify_connection_change()  # should not raise

    assert called == ["ok"]


# ── _is_secret_env ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,expected",
    [
        # Obvious secrets
        ("GITHUB_PERSONAL_ACCESS_TOKEN", True),
        ("API_KEY", True),
        ("DATABASE_URL", True),
        ("DB_URL", True),
        ("STRIPE_SECRET", True),
        ("MY_BEARER", True),
        ("FOO_PASSWORD", True),
        # Known non-secrets
        ("PATH", False),
        ("HOME", False),
        ("LOG_LEVEL", False),
        ("NODE_ENV", False),
        ("PORT", False),
        # Unknown keys default to SECRET (deny-by-default)
        ("WHATEVER_THIS_IS", True),
    ],
)
def test_is_secret_env_classification(key, expected):
    assert MCPClient._is_secret_env(key) is expected


# ── install_from_catalog ────────────────────────────────────────────


def test_install_from_catalog_unknown_id_raises():
    client = MCPClient()
    with pytest.raises(ValueError):
        client.install_from_catalog("does-not-exist")


def test_install_from_catalog_directory_field_becomes_trailing_arg(tmp_path, monkeypatch):
    """Filesystem server takes its allowed directory as a positional arg."""
    # Redirect config file writes away from the real user config.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    client = MCPClient()
    cfg = client.install_from_catalog("filesystem", {"path": "/tmp/allowed"})

    assert cfg.name == "filesystem"
    # The last arg is the directory value
    assert cfg.args[-1] == "/tmp/allowed"
    # And it did NOT end up in env
    assert not cfg.env or "path" not in cfg.env


def test_install_from_catalog_connection_string_goes_into_database_url_env(
    tmp_path, monkeypatch
):
    """Security-critical: connection strings must go via env (not CLI args
    where they'd be visible in ``ps`` output)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Stub keyring so _save_config doesn't hit the real OS keyring
    import polyglot_ai.core.mcp_client as mcp_mod

    class _FakeKeyring:
        def __init__(self):
            self.stored = {}

        def set_password(self, service, key, value):
            self.stored[(service, key)] = value

        def get_password(self, service, key):
            return self.stored.get((service, key))

    fake = _FakeKeyring()
    monkeypatch.setitem(
        __import__("sys").modules, "keyring", SimpleNamespace(
            set_password=fake.set_password, get_password=fake.get_password
        )
    )

    client = MCPClient()
    cfg = client.install_from_catalog(
        "postgres", {"connection_string": "postgresql://u:p@h/db"}
    )

    assert "DATABASE_URL" in (cfg.env or {})
    # The raw connection string must NOT appear in args (command-line leak)
    for arg in cfg.args:
        assert "postgresql://" not in arg
    assert mcp_mod  # quiet unused-import hint


# ── load_mcp_config ─────────────────────────────────────────────────


def test_load_mcp_config_missing_file_seeds_defaults(tmp_path, monkeypatch):
    """First run: seed sequential-thinking/memory/fetch."""
    config_path = tmp_path / "mcp_servers.json"

    # Prevent secure_write from failing on directory perms
    servers = load_mcp_config(config_path)

    # A file should now exist (seeded)
    assert config_path.exists()
    # Seeded servers match the documented default set (sans those that
    # have config_fields — none of the three do)
    names = {s.name for s in servers}
    assert names <= {"sequential-thinking", "memory", "fetch"}
    assert names, "expected at least one default server to be seeded"


def test_load_mcp_config_malformed_json_returns_empty(tmp_path, monkeypatch):
    """A corrupted config must not crash startup — return []."""
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text("{ this is not json ]")
    config_path.chmod(0o600)

    servers = load_mcp_config(config_path)

    assert servers == []


def test_load_mcp_config_restores_keyring_placeholders(tmp_path, monkeypatch):
    """Values marked ``__KEYRING__`` must be replaced with the real secret."""
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
                        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "__KEYRING__"},
                        "enabled": True,
                    }
                }
            }
        )
    )
    config_path.chmod(0o600)

    # Inject a fake keyring before load so the placeholder resolves
    stored = {("polyglot-ai-mcp", "github/GITHUB_PERSONAL_ACCESS_TOKEN"): "ghp_secret_token"}
    monkeypatch.setitem(
        __import__("sys").modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda service, key: stored.get((service, key)),
            set_password=lambda *a, **k: None,
        ),
    )

    servers = load_mcp_config(config_path)

    assert len(servers) == 1
    assert servers[0].env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_secret_token"


def test_load_mcp_config_missing_keyring_entry_falls_back_to_empty_string(
    tmp_path, monkeypatch
):
    """If the keyring secret is gone (user cleared credentials), load
    the config without the secret rather than failing."""
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "github": {
                        "command": "npx",
                        "args": [],
                        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "__KEYRING__"},
                        "enabled": True,
                    }
                }
            }
        )
    )
    config_path.chmod(0o600)

    monkeypatch.setitem(
        __import__("sys").modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda service, key: None,  # nothing in keyring
            set_password=lambda *a, **k: None,
        ),
    )

    servers = load_mcp_config(config_path)

    assert len(servers) == 1
    assert servers[0].env["GITHUB_PERSONAL_ACCESS_TOKEN"] == ""


# ── add_server / remove_server ──────────────────────────────────────


def test_add_server_registers_config():
    client = MCPClient()
    cfg = MCPServerConfig(name="x", command="echo", args=["hi"])
    client.add_server(cfg)
    assert client.get_server_configs() == [cfg]


def test_remove_server_also_removes_its_tools():
    """Removing a server must drop every tool qualified by its name."""
    client = MCPClient()
    client._servers["x"] = MCPServerConfig(name="x", command="echo")
    client._tools["mcp_x_one"] = MCPTool(
        server_name="x", name="one", description="", input_schema={}
    )
    client._tools["mcp_x_two"] = MCPTool(
        server_name="x", name="two", description="", input_schema={}
    )
    client._tools["mcp_y_other"] = MCPTool(
        server_name="y", name="other", description="", input_schema={}
    )

    client.remove_server("x")

    assert "x" not in client._servers
    # Only y's tool survives
    assert list(client._tools.keys()) == ["mcp_y_other"]


# ── Schema validation on load ───────────────────────────────────────


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


def _install_noop_keyring(monkeypatch) -> None:
    monkeypatch.setitem(
        __import__("sys").modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda *a, **k: None,
            set_password=lambda *a, **k: None,
        ),
    )


def test_load_mcp_config_rejects_top_level_array(tmp_path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text("[]")
    config_path.chmod(0o600)

    assert load_mcp_config(config_path) == []


def test_load_mcp_config_rejects_servers_as_array(tmp_path):
    config_path = tmp_path / "mcp_servers.json"
    _write_config(config_path, {"servers": ["not", "an", "object"]})

    assert load_mcp_config(config_path) == []


def test_load_mcp_config_skips_invalid_entry_but_keeps_valid_ones(tmp_path, monkeypatch):
    """A malformed server shouldn't poison the whole config — valid
    peers should still load."""
    _install_noop_keyring(monkeypatch)
    config_path = tmp_path / "mcp_servers.json"
    _write_config(
        config_path,
        {
            "servers": {
                "good": {
                    "command": "npx",
                    "args": ["-y", "@mcp/good@1.0.0"],
                    "env": {},
                    "enabled": True,
                },
                # args MUST be a list, not a shell string — common
                # mis-edit that would invite injection if we split it
                "bad_args_as_string": {
                    "command": "npx",
                    "args": "-y @mcp/bad@1.0.0",
                },
                # command is required
                "missing_command": {
                    "args": ["whatever"],
                },
                # env values must be strings (rules out accidental int/obj nesting)
                "env_value_not_string": {
                    "command": "npx",
                    "args": [],
                    "env": {"PORT": 8080},  # int, not "8080"
                },
            }
        },
    )

    servers = load_mcp_config(config_path)

    assert [s.name for s in servers] == ["good"]


def test_load_mcp_config_accepts_minimal_valid_entry(tmp_path, monkeypatch):
    _install_noop_keyring(monkeypatch)
    config_path = tmp_path / "mcp_servers.json"
    _write_config(
        config_path,
        {"servers": {"tiny": {"command": "echo"}}},
    )

    servers = load_mcp_config(config_path)
    assert len(servers) == 1
    assert servers[0].name == "tiny"
    assert servers[0].args == []
    assert servers[0].enabled is True  # defaulted


def test_load_mcp_config_rejects_non_bool_enabled(tmp_path, monkeypatch):
    _install_noop_keyring(monkeypatch)
    config_path = tmp_path / "mcp_servers.json"
    _write_config(
        config_path,
        {
            "servers": {
                "bad_enabled": {
                    "command": "echo",
                    "enabled": "yes",  # must be bool
                }
            }
        },
    )

    assert load_mcp_config(config_path) == []


# ── Catalog integrity ───────────────────────────────────────────────


def test_mcp_catalog_entries_have_required_fields():
    """Catalog is user-facing; each entry must be complete enough for
    the marketplace UI to render."""
    required = {"id", "name", "icon", "description", "command", "args"}
    for entry in MCP_CATALOG:
        missing = required - set(entry.keys())
        assert not missing, f"Catalog entry {entry.get('id')} missing: {missing}"
        # Args should be a list, not a string (shell-split risk)
        assert isinstance(entry["args"], list)
