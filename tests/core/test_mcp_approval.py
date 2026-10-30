"""MCP tools ask before running unless the user trusts that server.

Previously every MCP tool ran without approval, and first run seeded the
``fetch`` server (any URL, no private-address filter) and ``memory``
(persistent instructions) — so injected text could exfiltrate project
data or plant instructions with no prompt.
"""

from __future__ import annotations

import json

from polyglot_ai.core import mcp_client as mc
from polyglot_ai.core.mcp_client import MCPClient, MCPServerConfig, MCPTool, load_mcp_config


def _client_with(server: MCPServerConfig) -> MCPClient:
    client = MCPClient()
    client.add_server(server)
    client._tools["mcp_fetch_fetch"] = MCPTool(
        server_name=server.name, name="fetch", description="", input_schema={}
    )
    return client


def test_tools_ask_by_default():
    client = _client_with(MCPServerConfig(name="fetch", command="uvx"))
    assert client.tool_needs_approval("mcp_fetch_fetch") is True


def test_trusted_server_runs_without_asking(monkeypatch):
    monkeypatch.setattr(MCPClient, "_save_config", lambda self: None)
    client = _client_with(MCPServerConfig(name="fetch", command="uvx"))
    client.set_auto_approve("fetch", True)
    assert client.tool_needs_approval("mcp_fetch_fetch") is False


def test_unknown_tool_asks():
    assert MCPClient().tool_needs_approval("mcp_nope") is True


def test_only_side_effect_free_servers_are_seeded(tmp_path):
    seeded = mc._seed_default_config(tmp_path / "mcp_servers.json")
    names = {s.name for s in seeded}
    assert "fetch" not in names and "memory" not in names
    assert all(s.auto_approve for s in seeded)  # sequential-thinking only
    data = json.loads((tmp_path / "mcp_servers.json").read_text())
    assert set(data["servers"]) <= mc.SIDE_EFFECT_FREE_SERVERS


def test_existing_configs_without_the_field_now_ask(tmp_path):
    """Upgraders who got the old fetch/memory seeds must start being asked."""
    path = tmp_path / "mcp_servers.json"
    old = {
        "servers": {
            name: {"command": cmd, "args": [], "env": {}, "enabled": True}
            for name, cmd in (
                ("fetch", "uvx"),
                ("memory", "npx"),
                ("sequential-thinking", "npx"),
            )
        }
    }
    path.write_text(json.dumps(old))
    servers = {s.name: s for s in load_mcp_config(path)}
    assert servers["fetch"].auto_approve is False
    assert servers["memory"].auto_approve is False
    assert servers["sequential-thinking"].auto_approve is True


def test_auto_approve_must_be_a_bool(tmp_path):
    ok, reason = mc._validate_server_entry("x", {"command": "npx", "auto_approve": "yes"})
    assert not ok and "auto_approve" in reason
