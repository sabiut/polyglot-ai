"""Tool registry for AI function calling.

Domain-specific implementations are in submodules:
  - file_tools: file_read, file_write, file_patch, file_search, list_directory
  - git_tools: git_status, git_diff, git_log, git_commit, git_show_file
  - shell_tools: shell_exec, web_search
"""

from __future__ import annotations

import json
import logging

from polyglot_ai.core.ai.tools.definitions import (
    AUTO_APPROVE,
    REQUIRES_APPROVAL as REQUIRES_APPROVAL,
    TOOL_DEFINITIONS,
)
from polyglot_ai.core.file_ops import FileOperations
from polyglot_ai.core.sandbox import Sandbox

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Manages tool execution with sandboxing.

    sandbox and file_ops are optional — when not provided, only standalone
    tools (docker, k8s, database, web_search, create_plan) can execute.
    """

    def __init__(
        self, sandbox: Sandbox | None = None, file_ops: FileOperations | None = None
    ) -> None:
        self._sandbox = sandbox
        self._file_ops = file_ops
        self._mcp_client = None

    @staticmethod
    def get_tool_definitions() -> list[dict]:
        return TOOL_DEFINITIONS

    def needs_approval(self, tool_name: str) -> bool:
        """Check if a tool requires user approval.

        Fail-safe: any tool NOT in AUTO_APPROVE requires approval,
        including unknown tools and MCP tools.
        """
        return tool_name not in AUTO_APPROVE

    def is_auto_approved(self, tool_name: str) -> bool:
        """Check if a tool can run without user approval."""
        return tool_name in AUTO_APPROVE

    def set_mcp_client(self, mcp_client) -> None:
        """Set MCP client for delegating mcp_* tool calls."""
        self._mcp_client = mcp_client

    # Max argument payload size (bytes) — reject oversized AI-generated args
    _MAX_ARGS_SIZE = 100_000

    async def execute(self, tool_name: str, arguments: str) -> str:
        """Execute a tool and return the result as a string."""
        # Reject oversized payloads
        if arguments and len(arguments) > self._MAX_ARGS_SIZE:
            return f"Error: Arguments too large ({len(arguments)} bytes, max {self._MAX_ARGS_SIZE})"

        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            parsed = self._split_concat_json(arguments)
            if parsed:
                results = []
                for single_args in parsed:
                    results.append(await self._execute_single(tool_name, single_args))
                return "\n---\n".join(results)
            return f"Error: Invalid JSON arguments: {arguments}"

        # Validate args is a dict (not list, string, etc.)
        if not isinstance(args, dict):
            return f"Error: Expected JSON object, got {type(args).__name__}"

        return await self._execute_single(tool_name, args)

    async def _execute_single(self, tool_name: str, args: dict) -> str:
        """Execute a single tool invocation with parsed arguments."""
        try:
            # File tools
            if tool_name == "file_read":
                from .file_tools import file_read

                return await file_read(self._file_ops, args)
            elif tool_name == "file_write":
                from .file_tools import file_write

                return await file_write(self._sandbox, self._file_ops, args)
            elif tool_name == "file_patch":
                from .file_tools import file_patch

                return await file_patch(self._sandbox, self._file_ops, args)
            elif tool_name == "file_search":
                from .file_tools import file_search

                return await file_search(self._file_ops, args)
            elif tool_name == "list_directory":
                from .file_tools import list_directory

                return await list_directory(self._file_ops, args)

            # Shell tools
            elif tool_name == "shell_exec":
                from .shell_tools import shell_exec

                return await shell_exec(self._sandbox, args)
            elif tool_name == "web_search":
                from .shell_tools import web_search

                return await web_search(args)

            # Git tools
            elif tool_name == "git_status":
                from .git_tools import git_status

                return await git_status(self._sandbox, args)
            elif tool_name == "git_diff":
                from .git_tools import git_diff

                return await git_diff(self._sandbox, args)
            elif tool_name == "git_log":
                from .git_tools import git_log

                return await git_log(self._sandbox, args)
            elif tool_name == "git_commit":
                from .git_tools import git_commit

                return await git_commit(self._sandbox, args)
            elif tool_name == "git_show_file":
                from .git_tools import git_show_file

                return await git_show_file(self._sandbox, args)

            # Plan tool
            elif tool_name == "create_plan":
                return json.dumps(args)

            # Docker tools
            elif tool_name == "docker_list_containers":
                from .docker_tools import docker_list_containers

                return await docker_list_containers(args)
            elif tool_name == "docker_list_images":
                from .docker_tools import docker_list_images

                return await docker_list_images(args)
            elif tool_name == "docker_container_logs":
                from .docker_tools import docker_container_logs

                return await docker_container_logs(args)
            elif tool_name == "docker_inspect":
                from .docker_tools import docker_inspect

                return await docker_inspect(args)
            elif tool_name == "docker_restart":
                from .docker_tools import docker_restart

                return await docker_restart(args)
            elif tool_name == "docker_stop":
                from .docker_tools import docker_stop

                return await docker_stop(args)
            elif tool_name == "docker_start":
                from .docker_tools import docker_start

                return await docker_start(args)
            elif tool_name == "docker_remove":
                from .docker_tools import docker_remove

                return await docker_remove(args)

            # Kubernetes tools
            elif tool_name == "k8s_current_context":
                from .k8s_tools import k8s_current_context

                return await k8s_current_context(args)
            elif tool_name == "k8s_list_pods":
                from .k8s_tools import k8s_list_pods

                return await k8s_list_pods(args)
            elif tool_name == "k8s_list_deployments":
                from .k8s_tools import k8s_list_deployments

                return await k8s_list_deployments(args)
            elif tool_name == "k8s_list_services":
                from .k8s_tools import k8s_list_services

                return await k8s_list_services(args)
            elif tool_name == "k8s_pod_logs":
                from .k8s_tools import k8s_pod_logs

                return await k8s_pod_logs(args)
            elif tool_name == "k8s_describe":
                from .k8s_tools import k8s_describe

                return await k8s_describe(args)
            elif tool_name == "k8s_delete_pod":
                from .k8s_tools import k8s_delete_pod

                return await k8s_delete_pod(args)
            elif tool_name == "k8s_restart_deployment":
                from .k8s_tools import k8s_restart_deployment

                return await k8s_restart_deployment(args)
            elif tool_name == "k8s_scale_deployment":
                from .k8s_tools import k8s_scale_deployment

                return await k8s_scale_deployment(args)
            elif tool_name == "k8s_apply":
                from .k8s_tools import k8s_apply

                return await k8s_apply(args)

            # Database tools
            elif tool_name == "db_list_connections":
                from .db_tools import db_list_connections

                return await db_list_connections(args)
            elif tool_name == "db_get_schema":
                from .db_tools import db_get_schema

                return await db_get_schema(args)
            elif tool_name == "db_query":
                from .db_tools import db_query, is_readonly_query

                # Reject non-read-only SQL at execution time
                sql = args.get("sql", "") or args.get("query", "")
                if sql and not is_readonly_query(sql):
                    return (
                        "Error: Only read-only queries are allowed via db_query. "
                        "Use db_execute for write statements (INSERT/UPDATE/DELETE/DDL)."
                    )
                return await db_query(args)
            elif tool_name == "db_execute":
                from .db_tools import db_execute

                return await db_execute(args)

            # MCP tools
            elif self._mcp_client and tool_name.startswith("mcp_"):
                return await self._execute_mcp_tool(tool_name, args)
            elif self._mcp_client and tool_name in self._mcp_client.available_tools:
                return await self._execute_mcp_tool(tool_name, args)
            else:
                return f"Error: Unknown tool '{tool_name}'"
        except Exception as e:
            logger.exception("Tool execution error: %s", tool_name)
            return f"Error: {e}"

    @staticmethod
    def _split_concat_json(raw: str) -> list[dict] | None:
        """Split concatenated JSON objects like '{"a":1}{"b":2}' into a list.

        Returns None if the string can't be parsed this way.
        """
        results: list[dict] = []
        decoder = json.JSONDecoder()
        idx = 0
        raw = raw.strip()
        while idx < len(raw):
            # Skip whitespace between objects
            while idx < len(raw) and raw[idx] in " \t\n\r":
                idx += 1
            if idx >= len(raw):
                break
            try:
                obj, end = decoder.raw_decode(raw, idx)
                if isinstance(obj, dict):
                    results.append(obj)
                idx = end
            except json.JSONDecodeError:
                return None
        return results if len(results) > 1 else None

    async def _execute_mcp_tool(self, tool_name: str, args: dict) -> str:
        """Execute a tool via MCP client."""
        try:
            result = await self._mcp_client.call_tool(tool_name, args)
            return str(result) if result else "Tool returned no output."
        except Exception as e:
            return f"MCP tool error: {e}"
