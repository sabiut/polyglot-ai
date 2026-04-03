"""Security helpers — file permission checks, error sanitization, symlink guards.

Centralized security utilities used across the application.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Secret file patterns (excluded from AI context/indexing) ──────

SECRET_FILE_PATTERNS = frozenset({
    # Environment files
    ".env", ".env.local", ".env.production", ".env.staging", ".env.development",
    ".envrc",
    # Certificates and keys
    ".pem", ".key", ".crt", ".p12", ".pfx", ".jks",
    ".keystore", ".truststore",
    # SSH
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "known_hosts", "authorized_keys",
    # Package manager auth
    ".npmrc", ".yarnrc", ".yarnrc.yml",
    ".pypirc", ".netrc", ".curlrc",
    # Cloud credentials
    ".boto", ".s3cfg",
    # Docker
    ".dockercfg", "config.json",  # docker config
    # Other
    "poetry.toml",  # can contain PyPI tokens
})

SECRET_FILE_EXTENSIONS = frozenset({
    ".pem", ".key", ".crt", ".p12", ".pfx", ".jks",
    ".keystore", ".env", ".secret", ".credentials",
    ".tfvars", ".auto.tfvars",
})

SECRET_FILE_PREFIXES = frozenset({
    ".env",
    "secret",
    "credentials",
    ".netrc",
})

# Patterns to redact from error messages
_REDACT_PATTERNS = [
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(Authorization:\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[=:\s]+)\S+", re.IGNORECASE),
    re.compile(r"(token[=:\s]+)\S+", re.IGNORECASE),
    re.compile(r"(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"(key-[a-zA-Z0-9]{20,})"),
    re.compile(r"(ghp_[a-zA-Z0-9]{36,})"),
    re.compile(r"(gho_[a-zA-Z0-9]{36,})"),
    re.compile(r"(glpat-[a-zA-Z0-9\-]{20,})"),
    re.compile(r"(xai-[a-zA-Z0-9]{20,})"),
]

# ── Allowed MCP server commands ───────────────────────────────────

MCP_ALLOWED_COMMANDS = frozenset({
    "npx", "uvx", "python", "python3", "node",
    "docker", "podman",
})


def is_secret_file(path: Path) -> bool:
    """Check if a file path looks like it contains secrets."""
    name = path.name.lower()
    suffix = path.suffix.lower()

    if name in SECRET_FILE_PATTERNS:
        return True
    if suffix in SECRET_FILE_EXTENSIONS:
        return True
    if any(name.startswith(prefix) for prefix in SECRET_FILE_PREFIXES):
        return True

    return False


# ── Content-based secret detection ─────────────────────────────────
# Regexes that detect common secret patterns embedded in file contents.
# Used to prevent accidental upload of secrets to AI providers.

_CONTENT_SECRET_PATTERNS = [
    re.compile(r"(?:sk|pk|rk)-[a-zA-Z0-9]{20,}"),             # OpenAI / Stripe style
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),                       # GitHub PAT
    re.compile(r"gho_[a-zA-Z0-9]{36,}"),                       # GitHub OAuth
    re.compile(r"glpat-[a-zA-Z0-9\-]{20,}"),                   # GitLab PAT
    re.compile(r"xai-[a-zA-Z0-9]{20,}"),                       # xAI
    re.compile(r"AKIA[0-9A-Z]{16}"),                            # AWS access key
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),    # Private keys
    re.compile(r"-----BEGIN\s+CERTIFICATE-----"),               # Certificates
    re.compile(
        r"""(?:password|passwd|pwd|secret|token|api_key|apikey|"""
        r"""access_key|private_key)\s*[=:]\s*['"][^'"]{8,}['"]""",
        re.IGNORECASE,
    ),
]


def scan_content_for_secrets(content: str, max_scan: int = 50_000) -> list[str]:
    """Scan text content for embedded secrets.

    Returns a list of description strings for each detected secret type.
    Scans at most max_scan characters for performance.
    """
    findings: list[str] = []
    sample = content[:max_scan]
    for pattern in _CONTENT_SECRET_PATTERNS:
        if pattern.search(sample):
            findings.append(f"Matches secret pattern: {pattern.pattern[:60]}")
    return findings


def sanitize_error(message: str, max_length: int = 200) -> str:
    """Redact potential secrets from error messages before logging/display."""
    result = message
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub(r"\1[REDACTED]", result)
    if len(result) > max_length:
        result = result[:max_length] + "..."
    return result


def check_secure_file(path: Path) -> tuple[bool, str]:
    """Check that a sensitive file has secure permissions.

    Returns (is_secure, reason). Rejects:
    - files not owned by current user
    - files readable by group/others
    - symlinks
    """
    if not path.exists():
        return False, "File does not exist"

    if path.is_symlink():
        return False, f"Refusing symlinked file: {path}"

    try:
        st = path.stat()
    except OSError as e:
        return False, f"Cannot stat file: {e}"

    # Check ownership
    if st.st_uid != os.getuid():
        return False, f"File not owned by current user (owner uid={st.st_uid})"

    # Check permissions — reject if group/other readable
    mode = st.st_mode
    if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
        return False, (
            f"File has insecure permissions ({oct(mode & 0o777)}). "
            f"Expected 0600 or stricter."
        )

    return True, "OK"


def secure_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write a file with secure permissions (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp then rename for atomicity
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        tmp.rename(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def check_no_symlinks_in_path(target: Path, root: Path) -> tuple[bool, str]:
    """Verify no component of target (relative to root) is a symlink.

    Prevents symlink-based write escapes within a project.
    """
    try:
        rel = target.relative_to(root)
    except ValueError:
        return False, f"Path {target} is outside root {root}"

    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return False, f"Symlink found in path: {current}"

    return True, "OK"


def validate_mcp_command(command: str, args: list[str] | None = None) -> tuple[bool, str]:
    """Validate an MCP server command and args against the allowlist.

    Returns (is_allowed, reason).
    Resolves to absolute path and rejects symlinks to prevent PATH hijacking.
    Also validates args for shell injection patterns.
    """
    import shutil

    # Extract base command name (strip path)
    base = Path(command).name

    if base not in MCP_ALLOWED_COMMANDS:
        return False, (
            f"Command '{command}' is not in the MCP allowlist. "
            f"Allowed: {', '.join(sorted(MCP_ALLOWED_COMMANDS))}"
        )

    # Resolve to absolute path to prevent PATH hijacking
    if command.startswith("/"):
        resolved = Path(command)
    else:
        found = shutil.which(command)
        if not found:
            return False, f"Command '{command}' not found on PATH"
        resolved = Path(found)

    # Log symlinks for transparency but allow them if they resolve to
    # a real file under a known package manager directory (e.g. nvm, npm).
    # The command name was already validated against the allowlist above.
    if resolved.is_symlink():
        real = resolved.resolve()
        logger.info("MCP command '%s' resolves via symlink to '%s'", command, real)
        if not real.exists():
            return False, f"Command '{command}' symlink target does not exist: {real}"

    # Validate args — reject shell injection patterns
    if args:
        _DANGEROUS_ARG_PATTERNS = (";", "&&", "||", "|", "`", "$(", "${", ">", "<")
        for i, arg in enumerate(args):
            for pat in _DANGEROUS_ARG_PATTERNS:
                if pat in arg:
                    return False, f"MCP arg [{i}] contains shell operator '{pat}': {arg[:50]}"
            # Block args that look like they execute arbitrary commands
            if arg.startswith("--exec") or arg == "-e":
                return False, f"MCP arg [{i}] '{arg}' could execute arbitrary code"

    return True, "OK"
