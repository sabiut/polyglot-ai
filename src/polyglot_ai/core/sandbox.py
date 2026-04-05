"""Security sandbox — path confinement and command validation."""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path

from polyglot_ai.constants import (
    ALLOWED_COMMANDS,
    BLOCKED_PATTERNS,
    COMMAND_TIMEOUT,
    DANGEROUS_COMMANDS,
)

logger = logging.getLogger(__name__)


class Sandbox:
    """Enforces path confinement and command allowlist/blocklist."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()

    @property
    def project_root(self) -> Path:
        return self._root

    def validate_path(self, path: str) -> Path:
        """Resolve path and verify it's within project root. Returns absolute path."""
        resolved = (self._root / path).resolve()

        # Check for path traversal
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise PermissionError(f"Path '{path}' escapes project root '{self._root}'")

        # Check for symlinks escaping root
        if resolved.is_symlink():
            real = resolved.resolve()
            try:
                real.relative_to(self._root)
            except ValueError:
                raise PermissionError(f"Symlink '{path}' points outside project root")

        return resolved

    # Commands that can mutate the filesystem or execute arbitrary code.
    # These always require explicit user approval via the approval dialog.
    _MUTATING_COMMANDS = frozenset(
        {
            "python",
            "python3",
            "pip",
            "pip3",
            "node",
            "npm",
            "npx",
            "rm",
            "cp",
            "mv",
            "mkdir",
            "touch",
            "git",
            "make",
            "cmake",
            "cargo",
            "rustc",
            "sed",
            "awk",
            "tee",
        }
    )

    def validate_command(self, command: str) -> tuple[bool, str]:
        """Check if a command is allowed. Returns (allowed, reason)."""
        # Check blocked patterns
        cmd_lower = command.lower()
        for pattern in BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                return False, f"Blocked pattern: {pattern}"

        # Block shell composition operators — must check BEFORE shlex.split
        # so we catch raw shell syntax even if quoting is odd
        dangerous_chars = [";", "&&", "||", "`", "$(", "${", "|", ">>", ">", "<"]
        for char in dangerous_chars:
            if char in command:
                return False, f"Shell operator '{char}' is not allowed"

        # Parse command to get the base command
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "Invalid command syntax"

        if not parts:
            return False, "Empty command"

        base_cmd = Path(parts[0]).name  # Strip path, get just command name

        # Reject absolute/relative paths to bypass the allowlist
        if "/" in parts[0] and base_cmd not in ALLOWED_COMMANDS:
            return False, f"Path-based command '{parts[0]}' is not allowed"

        # Check allowlist
        if base_cmd not in ALLOWED_COMMANDS:
            return False, f"Command '{base_cmd}' is not in the allowlist"

        # Block interpreter inline-execution flags that allow arbitrary code
        _INTERPRETER_EXEC_FLAGS = {
            "python": {"-c", "--command"},
            "python3": {"-c", "--command"},
            "node": {"-e", "--eval", "-p", "--print"},
            "npm": {"exec"},
            "npx": set(),  # npx itself is an executor — require approval always
        }
        blocked_flags = _INTERPRETER_EXEC_FLAGS.get(base_cmd, set())
        if blocked_flags:
            for part in parts[1:]:
                if part in blocked_flags:
                    return False, (
                        f"Inline code execution via '{base_cmd} {part}' is not allowed. "
                        f"Write code to a file and run it instead."
                    )

        # Block dangerous subcommand patterns
        _DANGEROUS_FLAGS = frozenset(
            {
                "-exec",
                "-delete",
                "--delete",
                "--exec",
                "-rf",
                "--force",
                "--no-preserve-root",
                "eval",
                "exec",
                "-ok",
                "-execdir",
            }
        )
        for part in parts[1:]:
            if part in _DANGEROUS_FLAGS:
                return False, f"Dangerous flag '{part}' is not allowed"
            # Block env-var-like injection in arguments
            if part.startswith("$") or part.startswith("`"):
                return False, f"Shell expansion '{part}' is not allowed in arguments"

        # Git subcommand restrictions — only allow read-only subcommands
        # without explicit approval. Mutating git ops go through the
        # git_commit tool which has its own approval flow.
        if base_cmd == "git" and len(parts) > 1:
            _SAFE_GIT_SUBCMDS = frozenset(
                {
                    "status",
                    "diff",
                    "log",
                    "show",
                    "branch",
                    "tag",
                    "remote",
                    "stash",
                    "blame",
                    "shortlog",
                    "describe",
                    "rev-parse",
                    "ls-files",
                    "ls-tree",
                    "cat-file",
                    "reflog",
                    "config",  # read-only by default
                }
            )
            subcmd = parts[1]
            # Skip flags before the subcommand (e.g. git -C /path status)
            if subcmd.startswith("-"):
                for p in parts[2:]:
                    if not p.startswith("-"):
                        subcmd = p
                        break
            if subcmd not in _SAFE_GIT_SUBCMDS:
                return False, (
                    f"Git subcommand '{subcmd}' requires approval. "
                    f"Only read-only git commands are auto-allowed."
                )
            # Block git config writes — any set/unset requires approval
            if subcmd == "config":
                # Block global/system scope entirely
                if any(p in ("--global", "--system") for p in parts[2:]):
                    return False, "Modifying global/system git config is not allowed"
                # Block unset operations
                if any(p in ("--unset", "--unset-all", "--remove-section", "--rename-section") for p in parts[2:]):
                    return False, "Modifying git config requires approval"
                # Block value-setting: `git config <key> <value>` has 2+ non-flag args after "config"
                non_flag_args = [p for p in parts[2:] if not p.startswith("-")]
                if len(non_flag_args) >= 2:
                    return False, "Writing to git config requires approval"

        # find restrictions — block dangerous flags not caught above
        if base_cmd == "find":
            for part in parts[1:]:
                if part in ("-ok", "-execdir", "-fls", "-fprintf"):
                    return False, f"Dangerous find flag '{part}' is not allowed"

        return True, "Allowed"

    def is_dangerous_command(self, command: str) -> bool:
        """Check if a command requires explicit user approval.

        Returns True for interpreters, package managers, build tools,
        and file-mutating commands. These are allowed by the sandbox
        but must be approved by the user before execution.
        """
        try:
            parts = shlex.split(command)
        except ValueError:
            return True  # When in doubt, require approval
        if not parts:
            return True
        base_cmd = Path(parts[0]).name
        return base_cmd in DANGEROUS_COMMANDS

    async def exec_argv(
        self,
        argv: list[str],
        workdir: str | None = None,
        timeout: int = COMMAND_TIMEOUT,
    ) -> tuple[str, int]:
        """Execute a pre-split command as argv list.

        Bypasses shlex parsing — caller provides the exact argv.
        Still enforces the command allowlist and path confinement for workdir.
        Returns (output, returncode).
        """
        if not argv:
            return "Empty command", 1

        # Enforce allowlist even for pre-split argv
        base_cmd = Path(argv[0]).name
        if base_cmd not in ALLOWED_COMMANDS:
            return f"Command blocked: '{base_cmd}' is not in the allowlist", 1

        cwd = self._root
        if workdir:
            cwd = self.validate_path(workdir)
            if not cwd.is_dir():
                return f"Not a directory: {workdir}", 1

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if len(output) > 10000:
                output = output[:10000] + "\n... (output truncated)"
            return output, proc.returncode or 0
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return f"Command timed out after {timeout}s", 1
        except Exception as e:
            return f"Error executing command: {e}", 1

    async def exec_command(
        self,
        command: str,
        workdir: str | None = None,
        timeout: int = COMMAND_TIMEOUT,
    ) -> tuple[str, int]:
        """Execute a command in the sandbox with timeout.

        Enforces command validation at the execution boundary —
        callers do NOT need to call validate_command() separately.
        Uses create_subprocess_exec (not shell) to prevent shell injection.
        Returns (output, returncode).
        """
        # Enforce policy at execution boundary
        allowed, reason = self.validate_command(command)
        if not allowed:
            return f"Command blocked: {reason}", 1

        cwd = self._root
        if workdir:
            cwd = self.validate_path(workdir)
            if not cwd.is_dir():
                return f"Not a directory: {workdir}", 1

        # Parse into argv — NO shell execution
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"Invalid command syntax: {e}", 1

        if not args:
            return "Empty command", 1

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            # Truncate very long output
            if len(output) > 10000:
                output = output[:10000] + "\n... (output truncated)"

            return output, proc.returncode or 0

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return f"Command timed out after {timeout}s", 1
        except Exception as e:
            return f"Error executing command: {e}", 1
