"""File operation tools: read, write, patch, search, list directory.

Every public function is ``async`` but the underlying ``file_ops`` calls
are synchronous disk I/O.  We offload them to a thread via
``asyncio.to_thread`` so they never block the Qt/asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def file_read(file_ops, args: dict) -> str:
    path = args.get("path", "")

    # Block AI from reading secret/sensitive files
    from polyglot_ai.core.security import is_secret_file
    from polyglot_ai.core.file_safety import check_blocked_file

    from pathlib import Path

    # Judge the canonical project-relative path, not the raw string: an
    # absolute path used to skip the hidden-directory rule (.git/config).
    try:
        _resolved, rel = file_ops.project_path(path)
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"
    p = Path(rel)
    blocked = check_blocked_file(rel)
    if blocked:
        return f"Error: Cannot read {p.name} — {blocked}"
    if is_secret_file(p):
        return f"Error: Cannot read {p.name} — file appears to contain secrets"

    content = await asyncio.to_thread(file_ops.read, path)
    # The filename checks above only catch *known-secret* files (.env,
    # id_rsa, …). A hardcoded key sitting inside an ordinary source file
    # would otherwise flow verbatim to the LLM provider. file_read is
    # auto-approved and the highest-traffic tool in the app, so redact
    # the body the same way MCP tool output is redacted.
    from polyglot_ai.core.security import redact_secrets_in_content

    return redact_secrets_in_content(content)


_PLACEHOLDER = "[SECRET_REDACTED]"
_PLACEHOLDER_REFUSAL = (
    "Error: the new text contains [SECRET_REDACTED] placeholders that aren't in the "
    "file now. They came from a redacted read, and writing them would replace the real "
    "secrets in {path} with placeholder text. Use file_patch to change only the lines "
    "you need, keeping secret lines out of old_text and new_text."
)


def _adds_placeholders(new_text: str, current_text: str) -> bool:
    """True if a write would put *more* redaction placeholders on disk.

    file_read redacts secrets before the model sees a file. If the model
    then writes the whole file back, the real keys on disk become the
    literal placeholder — silent data loss (the original only survives in
    .polyglot-backups). Files that legitimately contain the placeholder
    text keep working: only an increase is refused.
    """
    return new_text.count(_PLACEHOLDER) > current_text.count(_PLACEHOLDER)


async def file_write(sandbox, file_ops, args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")

    from polyglot_ai.core.file_safety import validate_python_syntax, is_sensitive_path

    try:
        _resolved, rel = file_ops.project_path(path)
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"
    if is_sensitive_path(rel):
        return (
            f"Error: '{path}' is a CI/workflow/hooks config path. "
            f"Modifications to this path require explicit user approval "
            f"through the approval dialog."
        )

    syntax_error = validate_python_syntax(content, path)
    if syntax_error:
        return f"Error: {syntax_error}"

    resolved = sandbox.validate_path(path)
    if _PLACEHOLDER in content:
        current = ""
        if resolved.is_file():
            current = await asyncio.to_thread(
                resolved.read_text, encoding="utf-8", errors="replace"
            )
        if _adds_placeholders(content, current):
            return _PLACEHOLDER_REFUSAL.format(path=path)
    if resolved.is_file():
        from polyglot_ai.core.ai.code_applier import _create_backup

        await asyncio.to_thread(_create_backup, resolved)

    await asyncio.to_thread(file_ops.write, path, content)
    return f"Successfully wrote to {path}"


async def file_patch(sandbox, file_ops, args: dict) -> str:
    """Apply a search-and-replace edit to a file."""
    path = args.get("path", "")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    if not path:
        return "Error: No file path provided"

    from polyglot_ai.core.file_safety import is_sensitive_path

    # Also refuses symlinks and '..': patch exactly the file named,
    # never a symlink's target (it's read directly below).
    try:
        _resolved, rel = file_ops.project_path(path)
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"
    if is_sensitive_path(rel):
        return (
            f"Error: '{path}' is a CI/workflow/hooks config path. "
            f"Modifications require explicit user approval."
        )
    if not old_text:
        return "Error: No old_text provided"
    if _adds_placeholders(new_text, old_text):
        return _PLACEHOLDER_REFUSAL.format(path=path)

    resolved = sandbox.validate_path(path)
    if not resolved:
        return f"Error: Path not allowed: {path}"
    if not resolved.exists():
        return f"Error: File not found: {path}"

    try:
        content = await asyncio.to_thread(resolved.read_text, encoding="utf-8", errors="replace")
        count = content.count(old_text)
        if count == 0:
            if _PLACEHOLDER in old_text:
                return (
                    f"Error: old_text not found in {path}. It contains [SECRET_REDACTED], "
                    "which stands in for a real secret on disk; pick old_text that avoids "
                    "the secret lines."
                )
            return f"Error: old_text not found in {path}"
        if count > 1:
            return f"Error: old_text matches {count} locations in {path}. Make it more specific."

        new_content = content.replace(old_text, new_text, 1)

        from polyglot_ai.core.ai.code_applier import _create_backup

        await asyncio.to_thread(_create_backup, resolved)

        await asyncio.to_thread(file_ops.write, path, new_content)
        return f"Patched {path}: replaced {len(old_text)} chars with {len(new_text)} chars"
    except Exception as e:
        return f"Error patching {path}: {e}"


async def file_delete(file_ops, args: dict) -> str:
    """Delete a single file inside the project root.

    Refuses to act on directories — use ``dir_delete`` for those, so
    the AI has to be explicit about recursive removal.
    """
    path = args.get("path", "")
    if not path:
        return "Error: No file path provided"
    try:
        resolved = file_ops.validate_path(path)
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"

    if resolved.is_dir():
        return f"Error: '{path}' is a directory. Use dir_delete to remove it recursively."

    try:
        await asyncio.to_thread(file_ops.delete, path)
    except (PermissionError, FileNotFoundError) as exc:
        return f"Error: {exc}"
    return f"Deleted file: {path}"


async def dir_create(file_ops, args: dict) -> str:
    """Create a directory (and any missing parents) inside the project root."""
    path = args.get("path", "")
    if not path:
        return "Error: No directory path provided"
    try:
        await asyncio.to_thread(file_ops.make_directory, path)
    except (PermissionError, FileExistsError, ValueError) as exc:
        return f"Error: {exc}"
    return f"Created directory: {path}"


async def dir_delete(file_ops, args: dict) -> str:
    """Recursively delete a directory inside the project root.

    Requires the caller to pass ``recursive: true`` so the AI cannot
    accidentally wipe a folder by typing the wrong path. The
    underlying ``file_ops.delete`` enforces the same flag.
    """
    path = args.get("path", "")
    recursive = bool(args.get("recursive", False))
    if not path:
        return "Error: No directory path provided"
    if not recursive:
        return (
            "Error: dir_delete refuses to remove a directory unless 'recursive' "
            'is set to true. Pass {"path": ..., "recursive": true} to confirm.'
        )
    try:
        resolved = file_ops.validate_path(path)
    except (PermissionError, ValueError) as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: Directory does not exist: {path}"
    if not resolved.is_dir():
        return f"Error: '{path}' is not a directory. Use file_delete instead."

    try:
        await asyncio.to_thread(file_ops.delete, path, force_directory=True)
    except (PermissionError, FileNotFoundError) as exc:
        return f"Error: {exc}"
    return f"Deleted directory (recursive): {path}"


async def file_search(file_ops, args: dict) -> str:
    pattern = args.get("pattern", "")
    search_path = args.get("path", ".")
    # Use async subprocess directly — avoids blocking a thread pool thread
    results = await file_ops.search_async(pattern, path=search_path)
    if not results:
        return "No matches found."
    return "\n".join(r["file"] for r in results)


async def list_directory(file_ops, args: dict) -> str:
    path = args.get("path", ".")
    depth = args.get("depth", 3)
    return await asyncio.to_thread(file_ops.list_dir, path, depth)
