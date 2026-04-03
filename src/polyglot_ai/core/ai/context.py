"""Context builder for AI system prompts — includes project files."""

from __future__ import annotations

import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

from polyglot_ai.constants import CODE_EXTENSIONS, MAX_FILE_SIZE, SKIP_DIRS
MAX_CONTEXT_CHARS = 200_000  # total character budget for file contents


class ContextBuilder:
    """Builds system prompts with project structure and key file contents."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root
        self._indexer = None

    def set_project_root(self, root: Path | str) -> None:
        self._project_root = Path(root) if isinstance(root, str) else root

    def set_indexer(self, indexer) -> None:
        self._indexer = indexer

    def build_augmented_prompt(self, user_message: str, custom_prompt: str = "") -> str:
        """Build system prompt with RAG-retrieved relevant files prioritized."""
        base = self.build_system_prompt(custom_prompt)
        if not self._indexer or not self._indexer.is_ready or not user_message:
            return base

        relevant = self._indexer.query(user_message, top_k=5)
        if not relevant:
            return base

        from polyglot_ai.core.security import scan_content_for_secrets

        parts = [base, "", "RELEVANT FILES (auto-detected from your query):"]
        budget = 30_000  # extra chars for relevant files
        for rel_path, score in relevant:
            if budget <= 0:
                break
            full_path = self._project_root / rel_path
            if not full_path.exists():
                continue
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                # Skip files with embedded secrets
                if scan_content_for_secrets(content):
                    logger.warning("Skipping %s from RAG context: secret detected", rel_path)
                    continue
                if len(content) > budget:
                    content = content[:budget] + "\n... (truncated)"
                parts.append(f"\n--- {rel_path} ---")
                parts.append(content)
                budget -= len(content)
            except OSError:
                continue

        return "\n".join(parts)

    def build_system_prompt(self, custom_prompt: str = "") -> str:
        parts = [
            "You are a coding assistant built into a desktop IDE.",
            "The user's project files are included below — you can read everything.",
            "",
            "IMPORTANT RULES:",
            "1. ALWAYS ASK before making any changes. Describe what you plan to do and wait for the user to confirm.",
            "2. NEVER output code blocks with file changes unless the user explicitly says 'yes', 'go ahead', 'do it', 'apply', or similar confirmation.",
            "3. When reviewing code, give feedback and suggestions — do NOT immediately output changed files.",
            "4. When the user confirms, THEN output the code block with the complete file.",
            "",
            "HOW TO PROPOSE CHANGES:",
            "First, explain what you would change and why. For example:",
            "  'I'd like to add a docstring to the main() function in app.py and fix the import order. Shall I go ahead?'",
            "Wait for the user to confirm before outputting any code blocks.",
            "",
            "HOW TO WRITE FILES (only after user confirms):",
            "```python src/example.py",
            "# complete file content here",
            "```",
            "The first line after ``` MUST have the language AND the relative file path.",
            "Always include the COMPLETE file content, not a snippet.",
            "The user will see an 'Apply' button and must click it to write the file.",
            "",
            "HOW TO RUN COMMANDS (only after user confirms):",
            "$ pytest",
            "The user will see a 'Run' button and must click it to execute.",
            "",
            "YOUR BEHAVIOR:",
            "- When asked to review: read the files, give specific feedback. Do NOT output code blocks yet.",
            "- When asked to fix something: explain what you'd change, ask for confirmation, then output the fix.",
            "- When asked to run tests: suggest the command, ask if user wants to run it.",
            "- Keep explanations clear and concise.",
            "- After the user approves and you output a file change, summarize: 'Updated src/file.py: added X, fixed Y'",
            "",
            f"System: {platform.system()}",
            f"Python: {platform.python_version()}",
        ]

        if self._project_root:
            # Use project directory name only — avoid leaking absolute
            # home path (username, directory structure) to AI providers.
            parts.append(f"\nProject: {self._project_root.name}")
            parts.append(self._detect_project_type())
            parts.append(self._get_project_tree())
            parts.append(self._get_file_contents())

        if custom_prompt:
            parts.append("")
            parts.append("Additional instructions:")
            parts.append(custom_prompt)

        return "\n".join(parts)

    def _get_project_tree(self) -> str:
        """Generate project directory tree."""
        if not self._project_root or not self._project_root.is_dir():
            return ""

        lines = ["\n--- PROJECT STRUCTURE ---"]
        count = 0

        def walk(directory: Path, prefix: str, depth: int) -> None:
            nonlocal count
            if depth <= 0 or count >= 300:
                return
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda e: (not e.is_dir(), e.name),
                )
            except PermissionError:
                return
            entries = [e for e in entries if e.name not in SKIP_DIRS]
            for entry in entries:
                if count >= 300:
                    return
                connector = "├── " if entry != entries[-1] else "└── "
                lines.append(f"{prefix}{connector}{entry.name}")
                count += 1
                if entry.is_dir():
                    ext = "│   " if entry != entries[-1] else "    "
                    walk(entry, prefix + ext, depth - 1)

        walk(self._project_root, "", 4)
        return "\n".join(lines)

    def _detect_project_type(self) -> str:
        """Detect project type and suggest relevant commands."""
        if not self._project_root:
            return ""

        root = self._project_root
        detections: list[str] = []

        # Python
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            detections.append("Python project")
            if (root / "pyproject.toml").exists():
                detections.append("  Build: pip install -e .")
            if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
                detections.append("  Test: pytest")
            if (root / ".venv").exists() or (root / "venv").exists():
                detections.append("  Venv: .venv/ detected")

        # Node/JavaScript
        if (root / "package.json").exists():
            detections.append("Node.js project")
            detections.append("  Install: npm install")
            if (root / "package-lock.json").exists():
                detections.append("  Lock: package-lock.json (npm)")
            elif (root / "yarn.lock").exists():
                detections.append("  Lock: yarn.lock (yarn)")
            elif (root / "pnpm-lock.yaml").exists():
                detections.append("  Lock: pnpm-lock.yaml (pnpm)")
            # Check scripts
            try:
                import json as _json
                pkg = _json.loads((root / "package.json").read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                if "test" in scripts:
                    detections.append(f"  Test: npm test ({scripts['test'][:60]})")
                if "build" in scripts:
                    detections.append(f"  Build: npm run build ({scripts['build'][:60]})")
                if "dev" in scripts:
                    detections.append(f"  Dev: npm run dev ({scripts['dev'][:60]})")
                if "lint" in scripts:
                    detections.append(f"  Lint: npm run lint ({scripts['lint'][:60]})")
            except Exception:
                pass

        # Go
        if (root / "go.mod").exists():
            detections.append("Go project")
            detections.append("  Test: go test ./...")
            detections.append("  Build: go build ./...")

        # Rust
        if (root / "Cargo.toml").exists():
            detections.append("Rust project")
            detections.append("  Test: cargo test")
            detections.append("  Build: cargo build")

        # Docker
        if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
            detections.append("Docker: Dockerfile present")

        # Makefile
        if (root / "Makefile").exists():
            detections.append("Makefile: make targets available")

        if not detections:
            return ""

        return "\n--- PROJECT TYPE ---\n" + "\n".join(detections) + "\n"

    def _get_file_contents(self) -> str:
        """Read key project files into context."""
        if not self._project_root or not self._project_root.is_dir():
            return ""

        parts = ["\n--- FILE CONTENTS ---"]
        total_chars = 0

        # Priority files to always include
        priority = [
            "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
            "Makefile", "Dockerfile", "README.md", "setup.py", "setup.cfg",
        ]

        files_to_read: list[Path] = []

        # Add priority files first
        for name in priority:
            f = self._project_root / name
            if f.is_file():
                files_to_read.append(f)

        # Then walk and add source files
        for f in self._walk_files(self._project_root):
            if f not in files_to_read:
                files_to_read.append(f)

        from polyglot_ai.core.security import scan_content_for_secrets

        for file_path in files_to_read:
            if total_chars >= MAX_CONTEXT_CHARS:
                parts.append(f"\n... (context limit reached, {len(files_to_read)} files total)")
                break

            try:
                size = file_path.stat().st_size
                if size > MAX_FILE_SIZE:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="replace")

                # Content-based secret scanning — skip files with embedded secrets
                findings = scan_content_for_secrets(content)
                if findings:
                    rel = file_path.relative_to(self._project_root)
                    logger.warning(
                        "Skipping %s from AI context: detected %d secret pattern(s)",
                        rel, len(findings),
                    )
                    continue

                rel = file_path.relative_to(self._project_root)
                header = f"\n=== {rel} ===\n"
                parts.append(header + content)
                total_chars += len(header) + len(content)
            except Exception:
                continue

        return "\n".join(parts)

    def _walk_files(self, directory: Path, depth: int = 5) -> list[Path]:
        """Walk project and collect source files, excluding secrets."""
        from polyglot_ai.core.security import is_secret_file

        files: list[Path] = []
        if depth <= 0:
            return files
        try:
            for entry in sorted(directory.iterdir()):
                if entry.name in SKIP_DIRS:
                    continue
                if entry.is_file():
                    if is_secret_file(entry):
                        continue  # Never send secrets to AI providers
                    if (entry.suffix in CODE_EXTENSIONS
                            or entry.name in CODE_EXTENSIONS):
                        files.append(entry)
                elif entry.is_dir():
                    files.extend(self._walk_files(entry, depth - 1))
        except PermissionError:
            pass
        return files

    def read_file(self, rel_path: str) -> str | None:
        """Read a specific file from the project."""
        if not self._project_root:
            return None
        try:
            full = (self._project_root / rel_path).resolve()
            full.relative_to(self._project_root.resolve())
            return full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
