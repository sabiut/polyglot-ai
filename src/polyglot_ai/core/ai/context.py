"""Context builder for AI system prompts — includes project files."""

from __future__ import annotations

import logging
import platform
from pathlib import Path

from polyglot_ai.constants import CODE_EXTENSIONS, MAX_FILE_SIZE, SKIP_DIRS

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 200_000  # total character budget for file contents


class ContextBuilder:
    """Builds system prompts with project structure and key file contents."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root
        self._indexer = None
        self._sequential_thinking_tool: str | None = None
        # Active task context — set via set_active_task() so the system
        # prompt can include "you're helping with task X". Kept as a
        # plain dict so this module doesn't have a hard dependency on
        # the task module (and tests can pass any dict).
        self._active_task: dict | None = None

    def set_project_root(self, root: Path | str) -> None:
        self._project_root = Path(root) if isinstance(root, str) else root

    def set_indexer(self, indexer) -> None:
        self._indexer = indexer

    def set_active_task(self, task) -> None:
        """Inject the active Task into the system prompt as context.

        Pass ``None`` to clear the task block. Accepts the
        ``polyglot_ai.core.tasks.Task`` dataclass but only reads
        attributes by name, so any duck-typed object works (useful
        for tests).
        """
        if task is None:
            self._active_task = None
            return
        # Snapshot the plan into a list of (text, status) tuples so the
        # context block can render the AI-generated checklist when one
        # exists. Tolerant of duck-typed PlanStep stand-ins (anything
        # with ``.text`` and ``.status`` attributes works).
        plan_snapshot: list[tuple[str, str]] = []
        for step in getattr(task, "plan", None) or []:
            text = str(getattr(step, "text", "") or "").strip()
            if not text:
                continue
            status = str(getattr(step, "status", "pending") or "pending")
            plan_snapshot.append((text, status))
        self._active_task = {
            "kind": getattr(getattr(task, "kind", None), "value", "") or "",
            "title": getattr(task, "title", "") or "",
            "description": getattr(task, "description", "") or "",
            "branch": getattr(task, "branch", None),
            "state": getattr(getattr(task, "state", None), "value", "") or "",
            "modified_files": list(getattr(task, "modified_files", []) or []),
            "plan": plan_snapshot,
        }

    def set_available_tools(self, tool_names: list[str]) -> None:
        """Inform the builder which tool names are currently registered.

        Used to conditionally inject instructions that depend on a specific
        tool being available — e.g. the sequential-thinking directive is
        only emitted when an actual sequentialthinking/sequential_thinking
        tool exists in the registry.
        """
        self._sequential_thinking_tool = None
        for name in tool_names:
            lname = name.lower()
            if "sequentialthinking" in lname or "sequential_thinking" in lname:
                self._sequential_thinking_tool = name
                break

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
        parts: list[str] = []

        # If the user is working on a specific task, lead with it so the
        # AI's responses stay scoped. Goes BEFORE the boilerplate so the
        # task framing is the first thing the model reads.
        if self._active_task:
            t = self._active_task
            parts.append("ACTIVE TASK")
            parts.append(f"Title: {t['title']}")
            if t["kind"]:
                parts.append(f"Kind:  {t['kind']}")
            if t["state"]:
                parts.append(f"State: {t['state']}")
            if t["branch"]:
                parts.append(f"Branch: {t['branch']}")
            if t["description"]:
                parts.append("")
                parts.append("Description:")
                parts.append(t["description"])
            # Render the AI-generated plan checklist (if present) so
            # the model can refer to "step 3" or check progress without
            # having to ask the user. Status glyphs match the dialog.
            plan_steps = t.get("plan") or []
            if plan_steps:
                parts.append("")
                parts.append("Plan checklist:")
                glyphs = {
                    "pending": "[ ]",
                    "in_progress": "[~]",
                    "done": "[x]",
                    "skipped": "[-]",
                }
                for idx, (text, status) in enumerate(plan_steps, start=1):
                    glyph = glyphs.get(status, "[ ]")
                    parts.append(f"{idx}. {glyph} {text}")
            if t["modified_files"]:
                parts.append("")
                parts.append("Files touched so far on this task:")
                for f in t["modified_files"][:20]:
                    parts.append(f"- {f}")
                if len(t["modified_files"]) > 20:
                    parts.append(f"... and {len(t['modified_files']) - 20} more")
            parts.append("")
            parts.append(
                "Stay scoped to this task. If the user asks something off-topic, "
                "answer briefly and offer to create a new task for it."
            )
            parts.append("")

        # Panel-state block: anything published by UI panels via
        # ``core.panel_state`` (currently the most recent code review).
        # Placed before the boilerplate rules so the model sees it as
        # high-priority, user-visible context — not buried under the
        # file listing.
        panel_block = self._render_panel_state_block()
        if panel_block:
            parts.append(panel_block)
            parts.append("")

        parts += [
            "You are a coding assistant built into a desktop IDE.",
            "The user's project files are included below — you can read everything.",
            "",
            "IMPORTANT RULES:",
            "1. ALWAYS ASK before making any changes. Describe what you plan to do and wait for the user to confirm.",
            "2. NEVER output code blocks with file changes unless the user explicitly says 'yes', 'go ahead', 'do it', 'apply', or similar confirmation.",
            "3. When reviewing code, give feedback and suggestions — do NOT immediately output changed files.",
            "4. When the user confirms, THEN output the code block with the complete file.",
            "",
            "MUTATION TOOLS — file_write, file_patch, file_delete, dir_create, dir_delete:",
            "These tools execute IMMEDIATELY with no confirmation dialog. The ONLY safety",
            "gate is your behaviour. Therefore:",
            "- NEVER call any of these tools on the same turn the user first describes the work.",
            "- ALWAYS describe what you will do in plain text first (path, kind of change, any",
            "  files being deleted or overwritten) and ask 'Should I go ahead?' or similar.",
            "- ONLY call these tools after the user has replied with explicit consent in the",
            "  current conversation: 'yes', 'go ahead', 'do it', 'apply', 'agree', 'sure', etc.",
            "- For file_delete and dir_delete, name every file/directory that will be removed",
            "  BEFORE asking, so the user knows exactly what they are agreeing to.",
            "- A vague 'looks good' or an unrelated answer is NOT consent — ask again.",
            "- Consent for one action is NOT consent for follow-up actions. Re-ask for each",
            "  destructive batch unless the user said 'do all of these'.",
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

        # Only emit the sequential-thinking directive when that tool is
        # actually registered — otherwise we'd be instructing the model to
        # call a tool that does not exist, which causes hallucinated tool
        # calls or refusal loops on several providers.
        if self._sequential_thinking_tool:
            parts.extend(
                [
                    "",
                    "DEEP REASONING:",
                    f"- A `{self._sequential_thinking_tool}` tool is available. Use it for",
                    "  complex multi-step problems: debugging non-obvious bugs, architecture",
                    "  decisions, security reviews, refactors that touch many files, or any",
                    "  task where breaking the problem into explicit numbered thoughts would",
                    "  meaningfully improve the answer. Revise your thoughts as you learn more,",
                    "  then produce the final answer to the user.",
                    "- Skip it for chit-chat, one-line factual lookups, and yes/no clarifications.",
                    "- Do NOT mention the tool to the user — just use it and deliver the result.",
                ]
            )

        if self._project_root:
            # Use project directory name only — avoid leaking absolute
            # home path (username, directory structure) to AI providers.
            parts.append(f"\nProject: {self._project_root.name}")
            project_type = self._detect_project_type()
            parts.append(project_type)
            project_tree = self._get_project_tree()
            parts.append(project_tree)
            file_contents = self._get_file_contents()
            parts.append(file_contents)

            # Empty-project guardrail: when the folder has no detected
            # stack AND no code files, the model would otherwise silently
            # pick a framework (usually Next.js or Django) and scaffold
            # it without asking. That's a bad experience for users who
            # had a specific stack in mind. Inject a directive that
            # forces a stack question before create_plan is emitted.
            if not project_type.strip() and not self._has_any_code_files():
                parts.append("")
                parts.append("EMPTY PROJECT DIRECTIVE:")
                parts.append(
                    "This project directory is empty or has no recognisable "
                    "source files. BEFORE calling create_plan or writing any "
                    "files, you MUST ask the user which stack/framework they "
                    "want (language, web framework, database, styling, auth), "
                    "unless they already stated it in the current message. "
                    "Do NOT guess. List 2-3 sensible options if they seem "
                    "unsure, but wait for their answer before proposing a plan."
                )

        if custom_prompt:
            parts.append("")
            parts.append("Additional instructions:")
            parts.append(custom_prompt)

        return "\n".join(parts)

    def _has_any_code_files(self) -> bool:
        """Return True if the project has at least one code/source file.

        Used by the empty-project directive — we intentionally reuse
        ``_walk_files`` so "code file" means the same thing here as it
        does in the file-contents section (same extensions, same
        SKIP_DIRS). Limited to depth 3 to keep the check fast.
        """
        if not self._project_root or not self._project_root.is_dir():
            return False
        try:
            files = self._walk_files(self._project_root, depth=3)
            return len(files) > 0
        except OSError:
            # Err on the side of NOT emitting the directive: a transient
            # permission error shouldn't nag a user whose project is
            # perfectly populated.
            logger.debug(
                "_has_any_code_files: filesystem error scanning %s, assuming populated",
                self._project_root,
                exc_info=True,
            )
            return True

    def _render_panel_state_block(self) -> str:
        """Render a compact ``--- PANEL STATE ---`` block.

        Currently only shows the most recent review (if any). The
        block names the review mode, file list, finding counts, the
        top five findings by severity, and nudges the model to call
        ``get_review_findings`` when it needs more detail. Returns an
        empty string when nothing has been published yet.

        Wrapped in a top-level try/except so a malformed snapshot can
        never crash :meth:`build_system_prompt`.
        """
        try:
            return self._render_panel_state_block_inner()
        except Exception:
            logger.debug(
                "_render_panel_state_block: failed to render, skipping",
                exc_info=True,
            )
            return ""

    def _render_panel_state_block_inner(self) -> str:
        """Inner renderer — may raise on malformed snapshot data."""
        from polyglot_ai.core import panel_state

        review = panel_state.get_last_review()
        workflow = panel_state.get_last_workflow_run()
        if not review and not workflow:
            return ""

        lines: list[str] = ["--- PANEL STATE ---", ""]

        # Workflow run (if any)
        if workflow:
            wf_name = workflow.get("name") or workflow.get("workflow") or "unknown"
            wf_status = workflow.get("status") or "unknown"
            steps_done = workflow.get("steps_completed", 0)
            steps_total = workflow.get("steps_total", 0)
            lines.append(
                f"Last workflow: {wf_name} ({wf_status}, {steps_done}/{steps_total} steps)"
            )
            wf_inputs = workflow.get("inputs") or {}
            if wf_inputs:
                lines.append(f"Inputs: {', '.join(f'{k}={v}' for k, v in wf_inputs.items())}")
            lines.append("")

        if not review:
            return "\n".join(lines)

        mode = review.get("mode") or "unknown"
        status = review.get("status") or "ok"
        lines.append(f"Last review: {mode} (status: {status})")

        files = review.get("files") or []
        if files:
            shown = ", ".join(files[:5])
            if len(files) > 5:
                shown += f", ... +{len(files) - 5} more"
            lines.append(f"Files scanned: {shown}")

        counts = review.get("counts") or {}
        try:
            total = int(review.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total:
            parts_counts = [
                f"{counts.get(sev, 0)} {sev}"
                for sev in ("critical", "high", "medium", "low", "info")
                if counts.get(sev, 0)
            ]
            lines.append(f"Findings: {total} ({' · '.join(parts_counts)})")
        else:
            lines.append("Findings: 0 (clean)")

        findings = review.get("findings") or []
        if findings:
            lines.append("")
            lines.append("Top findings (most severe first):")
            for f in findings[:5]:
                sev = str(f.get("severity") or "").upper()
                file = f.get("file") or ""
                line_no = f.get("line") or 0
                title = f.get("title") or ""
                lines.append(f"  - [{sev}] {file}:{line_no} {title}")
            if len(findings) > 5:
                lines.append(
                    f"  ... and {len(findings) - 5} more "
                    "(call get_review_findings for the full list)"
                )

        lines.append("")
        lines.append(
            "The user just ran this review in the Code Review panel. "
            "Call the get_review_findings tool to filter by severity or "
            "file when the user asks about specific issues."
        )
        return "\n".join(lines)

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
        if (root / "Dockerfile").exists():
            detections.append("Docker: Dockerfile present")
        if (root / "docker-compose.yml").exists() or (root / "compose.yml").exists():
            detections.append("Docker Compose: compose file present")

        # Terraform
        tf_files = list(root.glob("*.tf"))
        if tf_files or (root / ".terraform.lock.hcl").exists():
            detections.append("Terraform project")
            detections.append("  Init: terraform init")
            detections.append("  Plan: terraform plan")
            detections.append("  Apply: terraform apply")
            if (root / "terraform.tfvars").exists():
                detections.append("  Vars: terraform.tfvars present")

        # Kubernetes
        k8s_dirs = [root / "k8s", root / "manifests", root / "deploy"]
        if any(d.is_dir() for d in k8s_dirs):
            detections.append("Kubernetes: manifest directory detected")
            detections.append("  Apply: kubectl apply -f <dir>/")

        # Helm
        if (root / "Chart.yaml").exists():
            detections.append("Helm chart")
            detections.append("  Lint: helm lint .")
            detections.append("  Template: helm template .")

        # Ansible
        if (root / "ansible.cfg").exists() or (root / "playbooks").is_dir():
            detections.append("Ansible project")
            if (root / "inventory").is_dir() or (root / "inventory").exists():
                detections.append("  Inventory: inventory/ detected")

        # dbt
        if (root / "dbt_project.yml").exists():
            detections.append("dbt project")
            detections.append("  Run: dbt run")
            detections.append("  Test: dbt test")
            detections.append("  Docs: dbt docs generate")

        # Airflow
        if (root / "dags").is_dir() or (root / "airflow.cfg").exists():
            detections.append("Airflow project")
            detections.append("  DAGs: dags/ directory detected")

        # GitHub Actions
        gh_workflows = root / ".github" / "workflows"
        if gh_workflows.is_dir() and list(gh_workflows.glob("*.yml")):
            detections.append("GitHub Actions: CI/CD workflows detected")

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
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "Makefile",
            "Dockerfile",
            "docker-compose.yml",
            "compose.yml",
            "README.md",
            "setup.py",
            "setup.cfg",
            # DevOps / IaC
            "Chart.yaml",
            "values.yaml",
            "dbt_project.yml",
            "ansible.cfg",
            "terraform.tfvars",
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

        from polyglot_ai.core.security import scan_file_for_secrets_cached

        for file_path in files_to_read:
            if total_chars >= MAX_CONTEXT_CHARS:
                parts.append(f"\n... (context limit reached, {len(files_to_read)} files total)")
                break

            try:
                size = file_path.stat().st_size
                if size > MAX_FILE_SIZE:
                    continue

                # Content-based secret scanning (cached by mtime)
                findings = scan_file_for_secrets_cached(file_path)
                if findings:
                    rel = file_path.relative_to(self._project_root)
                    logger.warning(
                        "Skipping %s from AI context: detected %d secret pattern(s)",
                        rel,
                        len(findings),
                    )
                    continue

                content = file_path.read_text(encoding="utf-8", errors="replace")
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
                    if entry.suffix in CODE_EXTENSIONS or entry.name in CODE_EXTENSIONS:
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
