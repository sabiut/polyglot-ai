"""Shared file-write safety rules — used by both code_applier and file_ops."""

from __future__ import annotations

from pathlib import Path

# Extensions that are NEVER writable by AI
BLOCKED_EXTENSIONS = {
    ".env",  # secrets
    ".pem",
    ".key",
    ".crt",
    ".p12",  # certificates
    ".service",
    ".timer",  # systemd
    ".sudoers",  # privilege escalation
}

# Filenames that are NEVER writable by AI
BLOCKED_FILENAMES = {
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".bash_logout",
    ".login",
    ".cshrc",
    ".gitconfig",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".docker/config.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "known_hosts",
    "authorized_keys",
    "shadow",
    "passwd",
    "sudoers",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service_account.json",
}

# Hidden directories that ARE allowed (common repo config)
ALLOWED_HIDDEN_DIRS = {
    ".github",
    ".vscode",
    ".claude",
    ".husky",
    ".circleci",
    ".gitlab",
}

# Paths within allowed hidden dirs that control CI/hooks/automation.
# Writes here can alter build pipelines, deploy workflows, or git hooks —
# these should require elevated approval even though the parent dir is allowed.
SENSITIVE_HIDDEN_PATHS = {
    ".github/workflows",
    ".github/actions",
    ".husky",
    ".circleci",
    ".gitlab/ci",
    ".gitlab-ci.yml",
    ".git/hooks",
}


def resolve_project_path(project_root: Path, path: str) -> tuple[Path, str]:
    """Resolve an AI-supplied path inside the project, safely.

    Returns ``(absolute_path, project_relative_posix_path)``. The rules in
    this module (blocked files, sensitive CI/hook paths) must be checked
    against the *relative* form: they used to receive the raw string, so
    an absolute path (``/home/me/proj/.git/hooks/pre-commit``) or a
    ``./``-prefixed one walked straight past them.

    Refuses, with PermissionError:
    - paths outside the project (absolute or otherwise),
    - any ``..`` component (``link/..`` resolves through ``link``),
    - any symlinked component, checked on the path *as written* — a
      resolved path never contains a symlink, which is why the old
      post-resolve checks never fired.
    """
    root = Path(project_root).resolve()
    raw = Path(path)
    if raw.is_absolute():
        try:
            raw = raw.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes project root: {path}") from exc
    current = root
    parts: list[str] = []
    for part in raw.parts:
        if part == "..":
            raise PermissionError(f"'..' is not allowed in paths: {path}")
        current = current / part
        parts.append(part)
        if current.is_symlink():
            raise PermissionError(f"Refusing to follow a symlink: {'/'.join(parts)}")
    return current, "/".join(parts)


def check_blocked_file(rel_path: str) -> str | None:
    """Check if a file is blocked from writing.

    Returns an error message if blocked, or None if allowed.
    """
    path = Path(rel_path)

    if path.name in BLOCKED_FILENAMES:
        return f"Blocked filename: {path.name}"

    if path.suffix.lower() in BLOCKED_EXTENSIONS:
        return f"Blocked file type: {path.suffix}"

    # Block hidden directories except known safe ones
    if path.parts and path.parts[0].startswith("."):
        if path.parts[0] not in ALLOWED_HIDDEN_DIRS:
            return f"Cannot write to hidden directory: {path.parts[0]}"

    return None


def is_sensitive_path(rel_path: str) -> bool:
    """Check if a path targets CI/hooks/workflow config that needs extra approval.

    These paths are technically writable but control automation pipelines,
    git hooks, or deployment workflows — modifications can have security impact.
    """
    # Compare path components, not raw strings: "./.github/workflows/x"
    # used to miss the ".github/workflows" prefix. Callers should still
    # pass the project-relative form from resolve_project_path.
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p not in ("", ".")]
    for sensitive in SENSITIVE_HIDDEN_PATHS:
        s_parts = sensitive.split("/")
        if parts[: len(s_parts)] == s_parts:
            return True
    return False


def validate_python_syntax(content: str, path: str) -> str | None:
    """Check Python syntax. Returns error message or None if valid."""
    if not path.endswith(".py"):
        return None
    try:
        compile(content, path, "exec")
        return None
    except SyntaxError as e:
        return f"Syntax error at line {e.lineno}: {e.msg}"
