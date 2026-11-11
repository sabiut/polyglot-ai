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
import tempfile

from polyglot_ai.core import aws_cli

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 20_000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... (truncated, {len(text) - _MAX_OUTPUT} more characters)"


def with_panel_context(args: dict) -> dict:
    """Fill a missing profile/region from the AWS panel's selection.

    Called before the approval card is shown, so what the user approves
    names the account and region and is exactly what runs — the tools
    themselves never look the selection up at run time. Returns ``args``
    unchanged when there is nothing to add.
    """
    from polyglot_ai.core import panel_state

    context = panel_state.get_aws_context()
    if context is None:
        return args
    command = aws_cli.split_command(str(args.get("command") or ""))
    filled = dict(args)
    for key, value in zip(("profile", "region"), context):
        in_command = any(tok.split("=", 1)[0] == f"--{key}" for tok in command)
        if value and not args.get(key) and not in_command:
            filled[key] = value
    return filled if filled != args else args


def display_command(args: dict) -> str:
    """``aws …`` as it will run, including the profile and region arguments."""
    text = "aws " + str(args.get("command") or "").strip()
    for key in ("profile", "region"):
        if args.get(key):
            text += f" --{key} {args[key]}"
    return text


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

    def run() -> tuple[str, int]:
        if aws_cli.classify(argv) != "read":
            return aws_cli.run(argv, profile=profile, region=region, timeout=60)
        # Nobody approved this call, so it must not be able to leave a
        # file behind: if it turns out to write one after all (an
        # operation newer than the classifier's list), a relative path
        # lands in a directory that is deleted straight away.
        with tempfile.TemporaryDirectory(prefix="polyglot-aws-") as scratch:
            return aws_cli.run(argv, profile=profile, region=region, timeout=60, cwd=scratch)

    output, code = await asyncio.to_thread(run)
    if code != 0:
        return output
    return _truncate(output) or "(no output)"


async def aws_logs_tail(args: dict) -> str:
    """Tail a CloudWatch log group (last ``since``, default 1h)."""
    group = str(args.get("log_group") or "").strip()
    if not group:
        return "Error: 'log_group' is required, e.g. '/aws/lambda/my-function'."
    if group.startswith("-"):
        # This tool never asks for approval, so the group must not be
        # readable as an option (``--endpoint-url=…``).
        return "Error: 'log_group' must be a log group name, not an option."
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
