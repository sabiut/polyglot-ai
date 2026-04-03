"""File operation tools: read, write, patch, search, list directory."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def file_read(file_ops, args: dict) -> str:
    path = args.get("path", "")

    # Block AI from reading secret/sensitive files
    from polyglot_ai.core.security import is_secret_file
    from polyglot_ai.core.file_safety import check_blocked_file

    from pathlib import Path

    p = Path(path)
    blocked = check_blocked_file(path)
    if blocked:
        return f"Error: Cannot read {p.name} — {blocked}"
    if is_secret_file(p):
        return f"Error: Cannot read {p.name} — file appears to contain secrets"

    return file_ops.read(path)


async def file_write(sandbox, file_ops, args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")

    from polyglot_ai.core.file_safety import validate_python_syntax, is_sensitive_path

    if is_sensitive_path(path):
        return (
            f"Error: '{path}' is a CI/workflow/hooks config path. "
            f"Modifications to this path require explicit user approval "
            f"through the approval dialog."
        )

    syntax_error = validate_python_syntax(content, path)
    if syntax_error:
        return f"Error: {syntax_error}"

    resolved = sandbox.validate_path(path)
    if resolved.is_file():
        from polyglot_ai.core.ai.code_applier import _create_backup

        _create_backup(resolved)

    file_ops.write(path, content)
    return f"Successfully wrote to {path}"


async def file_patch(sandbox, file_ops, args: dict) -> str:
    """Apply a search-and-replace edit to a file."""
    path = args.get("path", "")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    if not path:
        return "Error: No file path provided"

    from polyglot_ai.core.file_safety import is_sensitive_path

    if is_sensitive_path(path):
        return (
            f"Error: '{path}' is a CI/workflow/hooks config path. "
            f"Modifications require explicit user approval."
        )
    if not old_text:
        return "Error: No old_text provided"

    resolved = sandbox.validate_path(path)
    if not resolved:
        return f"Error: Path not allowed: {path}"
    if not resolved.exists():
        return f"Error: File not found: {path}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_text)
        if count == 0:
            return f"Error: old_text not found in {path}"
        if count > 1:
            return f"Error: old_text matches {count} locations in {path}. Make it more specific."

        new_content = content.replace(old_text, new_text, 1)

        from polyglot_ai.core.ai.code_applier import _create_backup

        _create_backup(resolved)

        file_ops.write(path, new_content)
        return f"Patched {path}: replaced {len(old_text)} chars with {len(new_text)} chars"
    except Exception as e:
        return f"Error patching {path}: {e}"


async def file_search(file_ops, args: dict) -> str:
    pattern = args.get("pattern", "")
    search_path = args.get("path", ".")
    results = file_ops.search(pattern, path=search_path)
    if not results:
        return "No matches found."
    return "\n".join(r["file"] for r in results)


async def list_directory(file_ops, args: dict) -> str:
    path = args.get("path", ".")
    depth = args.get("depth", 3)
    return file_ops.list_dir(path, depth)
