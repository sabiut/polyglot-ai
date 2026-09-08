"""AWS AI tools — the AI drives the user's ``aws`` CLI.

``aws_cli`` is the general tool: read-only calls (describe/list/get…)
are auto-approved by the registry; anything that changes state goes
through the normal approval card, with destructive verbs flagged.
``aws_logs_tail`` is a convenience for the most common ask — "why is
my Lambda failing?" — and is always read-only.
"""

from __future__ import annotations

import asyncio
import logging

from polyglot_ai.core import aws_cli

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 20_000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... (truncated, {len(text) - _MAX_OUTPUT} more characters)"


def is_read_only(args: dict | None) -> bool:
    """True when the ``aws_cli`` call only reads state (auto-approvable)."""
    if not args:
        return False
    return aws_cli.classify(str(args.get("command") or "")) == "read"


async def aws_cli_tool(args: dict) -> str:
    """Run an AWS CLI command. ``command`` omits the leading ``aws``."""
    command = str(args.get("command") or "").strip()
    if not command:
        return "Error: 'command' is required, e.g. 'ec2 describe-instances --max-items 5'."
    argv = aws_cli.split_command(command)
    if not argv:
        return "Error: empty command."
    profile = str(args.get("profile") or "")
    region = str(args.get("region") or "")
    output, code = await asyncio.to_thread(
        aws_cli.run, argv, profile=profile, region=region, timeout=60
    )
    if code != 0:
        return output
    return _truncate(output) or "(no output)"


async def aws_logs_tail(args: dict) -> str:
    """Tail a CloudWatch log group (last ``since``, default 1h)."""
    group = str(args.get("log_group") or "").strip()
    if not group:
        return "Error: 'log_group' is required, e.g. '/aws/lambda/my-function'."
    since = str(args.get("since") or "1h")
    filter_pattern = str(args.get("filter") or "").strip()
    argv = ["logs", "tail", group, "--since", since, "--format", "short"]
    if filter_pattern:
        argv.extend(["--filter-pattern", filter_pattern])
    profile = str(args.get("profile") or "")
    region = str(args.get("region") or "")
    output, code = await asyncio.to_thread(
        aws_cli.run, argv, profile=profile, region=region, timeout=60, json_output=False
    )
    if code != 0:
        return output
    return _truncate(output) or f"(no log events in {group} in the last {since})"
