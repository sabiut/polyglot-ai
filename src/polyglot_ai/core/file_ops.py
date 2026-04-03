"""Sandboxed file operations — all paths confined to project root."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from polyglot_ai.constants import EVT_FILE_CHANGED, EVT_FILE_CREATED, EVT_FILE_DELETED
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


class FileOperations:
    """File read/write/search operations confined to a project root."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._project_root: Path | None = None

    def set_project_root(self, root: Path) -> None:
        self._project_root = root.resolve()

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    def validate_path(self, path: str) -> Path:
        """Resolve a path and ensure it's within the project root."""
        if self._project_root is None:
            raise ValueError("No project is open")

        resolved = (self._project_root / path).resolve()

        try:
            resolved.relative_to(self._project_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes project root: {path}") from exc

        return resolved

    def read(self, path: str) -> str:
        resolved = self.validate_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        # Symlink protection — same policy as writes
        if self._project_root:
            from polyglot_ai.core.security import check_no_symlinks_in_path
            safe, reason = check_no_symlinks_in_path(resolved, self._project_root)
            if not safe:
                raise PermissionError(f"Blocked read: {reason}")

        try:
            return resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return resolved.read_text(encoding="latin-1")

    def write(self, path: str, content: str) -> None:
        resolved = self.validate_path(path)

        # Block dangerous files (shared policy)
        from polyglot_ai.core.file_safety import check_blocked_file, is_sensitive_path
        blocked = check_blocked_file(path)
        if blocked:
            raise PermissionError(blocked)

        # Block writes to CI/workflow/hooks paths via AI tools
        if is_sensitive_path(path):
            raise PermissionError(
                f"Cannot write to '{path}': CI/workflow/hooks config paths "
                f"require manual editing for security."
            )

        # Symlink protection — reject writes through symlinks
        if self._project_root:
            from polyglot_ai.core.security import check_no_symlinks_in_path
            safe, reason = check_no_symlinks_in_path(resolved, self._project_root)
            if not safe:
                raise PermissionError(f"Blocked: {reason}")

        is_new = not resolved.exists()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        event = EVT_FILE_CREATED if is_new else EVT_FILE_CHANGED
        self._event_bus.emit(event, path=str(resolved))
        logger.info("Wrote file: %s", resolved)

    def delete(self, path: str, force_directory: bool = False) -> None:
        """Delete a file or directory.

        Args:
            path: Relative path from project root.
            force_directory: Must be True to recursively delete a directory.
                             Prevents accidental recursive deletion.
        """
        resolved = self.validate_path(path)
        if resolved.is_file():
            resolved.unlink()
            self._event_bus.emit(EVT_FILE_DELETED, path=str(resolved))
            logger.info("Deleted file: %s", resolved)
        elif resolved.is_dir():
            if not force_directory:
                raise PermissionError(
                    f"Refusing to recursively delete directory '{path}'. "
                    "Pass force_directory=True to confirm."
                )
            import shutil
            shutil.rmtree(resolved)
            self._event_bus.emit(EVT_FILE_DELETED, path=str(resolved))
            logger.info("Deleted directory: %s", resolved)

    _MAX_SEARCH_PATTERN_LEN = 500

    def search(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict]:
        """Search for a pattern in files under project root (or a subdirectory)."""
        if self._project_root is None:
            return []

        # Reject oversized patterns (DoS protection)
        if len(pattern) > self._MAX_SEARCH_PATTERN_LEN:
            return []

        # Validate and scope search directory
        search_dir = "."
        if path and path != ".":
            resolved = self.validate_path(path)
            if resolved.is_dir():
                search_dir = str(resolved.relative_to(self._project_root))

        try:
            # Use "--" to prevent pattern/dir from being interpreted as flags
            result = subprocess.run(
                ["grep", "-rn", "-F", "-l", "--", pattern, search_dir],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Filter results: exclude files that match secret patterns
            from polyglot_ai.core.security import is_secret_file
            files = result.stdout.strip().split("\n")[:max_results * 2]
            filtered = []
            for f in files:
                if not f:
                    continue
                clean = f.lstrip("./")
                if is_secret_file(Path(clean)):
                    continue
                filtered.append({"file": clean})
                if len(filtered) >= max_results:
                    break
            return filtered
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def list_dir(self, path: str = ".", depth: int = 3) -> str:
        """Return a tree-formatted directory listing."""
        resolved = self.validate_path(path)
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        lines = []
        self._build_tree(resolved, "", depth, lines, max_entries=200)
        return "\n".join(lines)

    def _build_tree(
        self,
        directory: Path,
        prefix: str,
        depth: int,
        lines: list[str],
        max_entries: int,
    ) -> None:
        if depth <= 0 or len(lines) >= max_entries:
            return

        skip = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return

        entries = [e for e in entries if e.name not in skip]

        for i, entry in enumerate(entries):
            if len(lines) >= max_entries:
                lines.append(f"{prefix}... (truncated)")
                return

            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                self._build_tree(entry, prefix + extension, depth - 1, lines, max_entries)
