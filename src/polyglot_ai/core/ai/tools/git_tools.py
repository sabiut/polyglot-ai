"""Git tools: status, diff, log, commit, show file."""

from __future__ import annotations

import logging
import re
import shlex

logger = logging.getLogger(__name__)


async def git_status(sandbox, args: dict) -> str:
    command = "git status --porcelain"
    allowed, reason = sandbox.validate_command(command)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(command)
    if returncode != 0:
        return f"Error running git status: {output}"
    if not output or not output.strip():
        return "Working tree is clean. No changes."
    return f"Git status:\n{output.strip()}"


async def git_diff(sandbox, args: dict) -> str:
    mode = args.get("mode", "working")

    if mode == "staged":
        command = "git diff --cached"
    elif mode == "branch":
        detect_cmd = "git rev-parse --verify main"
        allowed, reason = sandbox.validate_command(detect_cmd)
        if allowed:
            output, rc = await sandbox.exec_command(detect_cmd)
            base = "main" if rc == 0 else "master"
        else:
            base = "main"
        command = f"git diff {base}...HEAD"
    else:
        command = "git diff"

    allowed, reason = sandbox.validate_command(command)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(command)
    if returncode != 0:
        return f"Error running git diff: {output}"
    if not output or not output.strip():
        return f"No changes found ({mode} mode)."

    if len(output) > 30000:
        output = output[:30000] + "\n... (truncated)"

    return f"Git diff ({mode}):\n{output}"


async def git_log(sandbox, args: dict) -> str:
    count = min(args.get("count", 20), 50)
    command = f"git log --oneline -{count}"

    allowed, reason = sandbox.validate_command(command)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(command)
    if returncode != 0:
        return f"Error running git log: {output}"
    if not output or not output.strip():
        return "No commits found."
    return f"Git log (last {count} commits):\n{output.strip()}"


async def git_commit(sandbox, args: dict) -> str:
    message = args.get("message", "")
    if not message:
        return "Error: No commit message provided"

    # Step 1: git add -A
    add_cmd = "git add -A"
    allowed, reason = sandbox.validate_command(add_cmd)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(add_cmd)
    if returncode != 0:
        return f"Staging failed: {output}"

    # Step 2: git commit -m <message>
    # Use exec_argv to avoid shell-style quoting issues with the message
    output, returncode = await sandbox.exec_argv(["git", "commit", "-m", message])
    if returncode != 0:
        return f"Commit failed: {output}"
    return f"Commit successful:\n{output.strip()}" if output else "Commit successful."


async def git_show_file(sandbox, args: dict) -> str:
    path = args.get("path", "")
    ref = args.get("ref", "HEAD")
    if not path:
        return "Error: No file path provided"

    if not re.match(r"^[a-zA-Z0-9_./@^~\-]+$", ref):
        return f"Error: Invalid git ref: {ref}"
    if not re.match(r"^[a-zA-Z0-9_./ \-]+$", path):
        return f"Error: Invalid file path: {path}"

    git_ref_path = shlex.quote(f"{ref}:{path}")
    command = f"git show {git_ref_path}"

    allowed, reason = sandbox.validate_command(command)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(command)
    if returncode != 0:
        return f"Error: {output}"

    if output and len(output) > 50000:
        output = output[:50000] + "\n... (truncated)"

    return output if output else "(empty file)"
