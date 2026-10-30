"""Sandboxed file operations — all paths confined to project root."""

from __future__ import annotations

import asyncio
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

    def project_path(self, path: str) -> tuple[Path, str]:
        """``(absolute, project-relative)`` for an AI-supplied path.

        Refuses paths outside the project, ``..``, and symlinked
        components (see :func:`file_safety.resolve_project_path`). Safety
        rules must be checked against the relative form it returns, never
        the raw string: an absolute or ``./``-prefixed path used to slip
        past the CI/hooks protection (``/proj/.git/hooks/pre-commit``).
        """
        if self._project_root is None:
            raise ValueError("No project is open")
        from polyglot_ai.core.file_safety import resolve_project_path

        return resolve_project_path(self._project_root, path)

    def reject_symlinks(self, path: str) -> None:
        """Refuse ``path`` if it leaves the project, uses ``..`` or goes through a symlink."""
        self.project_path(path)

    def _check_writable(self, rel: str, action: str) -> None:
        from polyglot_ai.core.file_safety import check_blocked_file, is_sensitive_path

        blocked = check_blocked_file(rel)
        if blocked:
            raise PermissionError(blocked)
        if is_sensitive_path(rel):
            raise PermissionError(
                f"Cannot {action} '{rel}': CI/workflow/hooks config paths "
                "require manual editing for security."
            )

    def read(self, path: str) -> str:
        resolved, _rel = self.project_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        try:
            return resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return resolved.read_text(encoding="latin-1")

    def write(self, path: str, content: str) -> None:
        # The approval card showed ``path``: the write must land on exactly
        # that file (no symlink target), and the rules must judge the
        # canonical relative path, whatever spelling the model used.
        resolved, rel = self.project_path(path)
        self._check_writable(rel, "write to")

        is_new = not resolved.exists()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        event = EVT_FILE_CREATED if is_new else EVT_FILE_CHANGED
        self._event_bus.emit(event, path=str(resolved))
        logger.info("Wrote file: %s", resolved)

    def delete(self, path: str, force_directory: bool = False) -> None:
        """Delete a file or directory inside the project root.

        Same safety policy as :meth:`write` — no symlinks anywhere on the
        path, no ``..``, blocked/sensitive rules on the canonical relative
        path — plus: the project root itself can never be deleted, and
        recursive directory removal requires ``force_directory=True``.
        """
        resolved, rel = self.project_path(path)
        if not rel:
            raise PermissionError("Refusing to delete the project root itself.")
        if not resolved.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        self._check_writable(rel, "delete")

        if resolved.is_file():
            resolved.unlink()
            self._event_bus.emit(EVT_FILE_DELETED, path=str(resolved))
            logger.info("Deleted file: %s", resolved)
            return

        if resolved.is_dir():
            if not force_directory:
                raise PermissionError(
                    f"Refusing to recursively delete directory '{path}'. "
                    "Pass force_directory=True to confirm."
                )
            import shutil

            shutil.rmtree(resolved)
            self._event_bus.emit(EVT_FILE_DELETED, path=str(resolved))
            logger.info("Deleted directory: %s", resolved)

    def make_directory(self, path: str) -> None:
        """Create a directory (including parents) inside the project root.

        Refuses sensitive paths and existing non-directory targets.
        Idempotent: creating an already-existing directory is a no-op.
        """
        resolved, rel = self.project_path(path)
        self._check_writable(rel, "create")

        if resolved.exists():
            if resolved.is_dir():
                return
            raise FileExistsError(f"Path exists and is not a directory: {path}")

        resolved.mkdir(parents=True, exist_ok=True)
        self._event_bus.emit(EVT_FILE_CREATED, path=str(resolved))
        logger.info("Created directory: %s", resolved)

    _MAX_SEARCH_PATTERN_LEN = 500

    def search(self, pattern: str, path: str = ".", max_results: int = 50) -> list[dict]:
        """Search for a pattern in files (sync fallback).

        Prefer :meth:`search_async` from async contexts to avoid blocking
        the event loop's thread pool.
        """
        if self._project_root is None:
            return []
        if len(pattern) > self._MAX_SEARCH_PATTERN_LEN:
            return []

        search_dir = "."
        if path and path != ".":
            resolved = self.validate_path(path)
            if resolved.is_dir():
                search_dir = str(resolved.relative_to(self._project_root))

        try:
            result = subprocess.run(
                ["grep", "-rn", "-F", "-l", "--", pattern, search_dir],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return self._filter_search_results(result.stdout, max_results)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    async def search_async(
        self, pattern: str, path: str = ".", max_results: int = 50
    ) -> list[dict]:
        """Search for a pattern using async subprocess (non-blocking)."""
        if self._project_root is None:
            return []
        if len(pattern) > self._MAX_SEARCH_PATTERN_LEN:
            return []

        search_dir = "."
        if path and path != ".":
            resolved = self.validate_path(path)
            if resolved.is_dir():
                search_dir = str(resolved.relative_to(self._project_root))

        try:
            proc = await asyncio.create_subprocess_exec(
                "grep",
                "-rn",
                "-F",
                "-l",
                "--",
                pattern,
                search_dir,
                cwd=self._project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return self._filter_search_results(stdout.decode(errors="replace"), max_results)
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            return []

    @staticmethod
    def _filter_search_results(stdout: str, max_results: int) -> list[dict]:
        """Filter grep output, excluding secret files."""
        from polyglot_ai.core.security import is_secret_file

        files = stdout.strip().split("\n")[: max_results * 2]
        filtered: list[dict] = []
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
