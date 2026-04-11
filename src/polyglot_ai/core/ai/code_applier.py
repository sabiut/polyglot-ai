"""Parse AI responses for code blocks with filenames and apply them safely.

Security measures:
- Path traversal blocked via resolve() + relative_to()
- Python files syntax-checked before writing
- Dangerous file types blocked
- Backups created before overwriting
- Commands validated through Sandbox
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from polyglot_ai.core.file_safety import (
    check_blocked_file,
    is_sensitive_path,
    validate_python_syntax,
)

logger = logging.getLogger(__name__)


def parse_code_blocks(text: str) -> list[dict]:
    """Extract code blocks that have file paths from AI response.

    Returns list of {path: str, content: str, language: str}
    """
    blocks = []

    # Pattern 1: ```lang path/to/file.ext\ncode\n```
    # Also supports extensionless files: ```docker Dockerfile
    pattern1 = re.compile(
        r"```(\w+)\s+([\w./\-]+(?:\.\w+)?)\s*\n(.*?)```",
        re.DOTALL,
    )
    for match in pattern1.finditer(text):
        lang = match.group(1)
        path = match.group(2)
        # Skip if it looks like a code block without a path (just a language)
        if "/" not in path and "." not in path and path == lang:
            continue
        if ".." in path:
            continue
        blocks.append(
            {
                "path": path,
                "content": match.group(3).strip(),
                "language": lang,
            }
        )

    if blocks:
        return blocks

    # Pattern 2: filename before code block
    # Supports: `path/file.py`:\n```...\n or **path**\n```...\n or === path ===\n```...\n
    pattern2 = re.compile(
        r"(?:`([\w./\-]+(?:\.\w+)?)`|(?:\*\*)([\w./\-]+(?:\.\w+)?)(?:\*\*)"
        r"|=== ([\w./\-]+(?:\.\w+)?) ===)"
        r"\s*:?\s*\n```\w*\n(.*?)```",
        re.DOTALL,
    )
    for match in pattern2.finditer(text):
        path = match.group(1) or match.group(2) or match.group(3)
        if ".." in path:
            continue
        blocks.append(
            {
                "path": path,
                "content": match.group(4).strip(),
                "language": "",
            }
        )

    return blocks


def _create_backup(full_path: Path) -> Path | None:
    """Create a backup of an existing file before overwriting. Returns backup path."""
    if not full_path.exists():
        return None
    backup_dir = full_path.parent / ".polyglot-backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{full_path.name}.{timestamp}.bak"
    try:
        shutil.copy2(full_path, backup_path)
        logger.info("Backup created: %s", backup_path)
        return backup_path
    except Exception:
        logger.exception("Failed to create backup")
        return None


def apply_code_block(project_root: Path, block: dict) -> tuple[bool, str]:
    """Write a code block to the project with safety checks.

    Returns (success, message).
    """
    rel_path = block["path"]
    content = block["content"]

    # Check blocked files
    blocked = check_blocked_file(rel_path)
    if blocked:
        return False, f"Rejected {rel_path}: {blocked}"

    # Warn about sensitive CI/workflow/hooks paths
    if is_sensitive_path(rel_path):
        return False, (
            f"Rejected {rel_path}: this is a CI/workflow/hooks config path. "
            f"Modifications to automation files require manual editing."
        )

    # Validate Python syntax before writing
    syntax_error = validate_python_syntax(content, rel_path)
    if syntax_error:
        return False, f"Rejected {rel_path}: {syntax_error}"

    # Reject suspiciously short files (likely truncated), but allow
    # legitimately small files like __init__.py, conftest.py, etc.
    if rel_path.endswith(".py") and len(content.strip()) < 20:
        import os

        basename = os.path.basename(rel_path)
        # These are often very short or even empty
        allowed_short = {"__init__.py", "__main__.py", "conftest.py", "setup.py"}
        if basename not in allowed_short:
            return False, f"Rejected {rel_path}: content too short (likely truncated)"

    # Security: ensure path stays within project root
    full_path = (project_root / rel_path).resolve()
    try:
        full_path.relative_to(project_root.resolve())
    except ValueError:
        return False, f"Rejected {rel_path}: path escapes project root"

    # Symlink protection — reject writes through symlinked path components
    from polyglot_ai.core.security import check_no_symlinks_in_path

    safe, reason = check_no_symlinks_in_path(full_path, project_root.resolve())
    if not safe:
        return False, f"Rejected {rel_path}: {reason}"

    try:
        # Backup existing file before overwriting
        backup = _create_backup(full_path)

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content + "\n", encoding="utf-8")
        logger.info("Applied code block to: %s", rel_path)

        msg = f"Written: {rel_path}"
        if backup:
            msg += f" (backup: {backup.name})"
        return True, msg
    except Exception as e:
        return False, f"Failed to write {rel_path}: {e}"


async def run_command_safe(
    project_root: Path,
    command: str,
    timeout: int = 60,
    *,
    user_approved: bool = False,
) -> tuple[str, int]:
    """Run a command after Sandbox validation.

    Uses Sandbox.validate_command() to check blocklist and shell
    operators. When ``user_approved`` is True the command allowlist
    is skipped (the user clicked "Run" in the UI).
    """
    from polyglot_ai.core.sandbox import Sandbox

    sandbox = Sandbox(project_root)

    # Execute through sandbox — exec_command validates internally
    output, returncode = await sandbox.exec_command(
        command, timeout=timeout, user_approved=user_approved
    )
    return output, returncode
