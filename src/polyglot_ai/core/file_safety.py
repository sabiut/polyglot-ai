"""Shared file-write safety rules — used by both code_applier and file_ops."""

from __future__ import annotations

from pathlib import Path

# Extensions that are NEVER writable by AI
BLOCKED_EXTENSIONS = {
    ".env",                             # secrets
    ".pem", ".key", ".crt", ".p12",    # certificates
    ".service", ".timer",              # systemd
    ".sudoers",                        # privilege escalation
}

# Filenames that are NEVER writable by AI
BLOCKED_FILENAMES = {
    ".bashrc", ".zshrc", ".profile", ".bash_profile",
    ".gitconfig", ".npmrc", ".pypirc",
    "id_rsa", "id_ed25519", "authorized_keys",
    "shadow", "passwd", "sudoers",
}

# Hidden directories that ARE allowed (common repo config)
ALLOWED_HIDDEN_DIRS = {
    ".github", ".vscode", ".claude", ".husky",
    ".circleci", ".gitlab",
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
}


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
    normalized = rel_path.replace("\\", "/")
    for sensitive in SENSITIVE_HIDDEN_PATHS:
        if normalized.startswith(sensitive):
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
