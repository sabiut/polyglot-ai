"""Workflow engine — load and render YAML-based multi-step workflows.

A workflow is a YAML file that describes a sequence of AI prompts.
Each step's prompt is sent to the AI as a user message; the AI
responds using tools (Playwright, shell, K8s, etc.) as needed. The
chat panel drives execution by calling ``_stream_response()`` for
each step, reusing the full tool-calling loop.

Workflow files live in ``.polyglot/workflows/`` in the project root.
Built-in defaults are bundled in ``polyglot_ai/core/workflows/`` and
seeded on first use.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory name inside the project root
_PROJECT_WORKFLOWS_DIR = ".polyglot/workflows"

# Bundled defaults shipped with the app
_BUNDLED_DIR = Path(__file__).parent / "workflows"


@dataclass
class WorkflowInput:
    """A single input parameter for a workflow."""

    name: str
    description: str = ""
    required: bool = True
    default: str = ""


@dataclass
class WorkflowStep:
    """A single step in a workflow."""

    name: str
    prompt: str  # Template string with {{variable}} placeholders


@dataclass
class WorkflowDefinition:
    """A complete workflow loaded from YAML."""

    name: str
    description: str = ""
    inputs: list[WorkflowInput] = field(default_factory=list)
    steps: list[WorkflowStep] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def slug(self) -> str:
        """Filename-based identifier (e.g. 'verify-deploy')."""
        if self.source_path:
            return self.source_path.stem
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


def render_step_prompt(step: WorkflowStep, inputs: dict[str, str]) -> str:
    """Render a step's prompt template with the given input values.

    Uses simple ``{{variable}}`` substitution. Unknown variables are
    left as-is so the AI can see what was expected.
    """
    result = step.prompt
    for key, value in inputs.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def parse_workflow_args(arg_string: str) -> tuple[str, dict[str, str]]:
    """Parse a ``/workflow name --key value --key2 value2`` argument string.

    Uses ``shlex.split`` so quoted values survive as a single token
    (``--scenario "guest checkout flow"`` -> ``scenario="guest checkout flow"``).
    The previous ``str.split()`` implementation silently truncated
    multi-word values to the first whitespace-delimited token, which
    broke any workflow argument the user typed naturally with spaces.

    On unbalanced-quote input ``shlex.split`` raises ``ValueError`` —
    we fall back to ``str.split()`` so the caller still gets *something*
    to work with rather than a crash; the resulting inputs will be
    truncated, but ``validate_inputs`` will then complain about the
    missing required keys, which is the surface the user actually sees.

    Returns (workflow_name, {key: value, ...}).
    """
    try:
        parts = shlex.split(arg_string)
    except ValueError:
        # Unbalanced quote — fall back to whitespace split rather than
        # losing the entire command. The user will see a "missing
        # required input" error on the truncated args, which is
        # actionable.
        parts = arg_string.strip().split()
    if not parts:
        return "", {}

    name = parts[0]
    inputs: dict[str, str] = {}
    i = 1
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            key = parts[i][2:]  # strip --
            value = parts[i + 1]
            inputs[key] = value
            i += 2
        else:
            i += 1

    return name, inputs


def validate_inputs(
    definition: WorkflowDefinition, inputs: dict[str, str]
) -> tuple[bool, dict[str, str], list[str]]:
    """Validate and fill defaults for workflow inputs.

    Returns (ok, filled_inputs, missing_names).
    """
    filled = dict(inputs)
    missing: list[str] = []

    for inp in definition.inputs:
        if inp.name not in filled:
            if inp.default:
                filled[inp.name] = inp.default
            elif inp.required:
                missing.append(inp.name)

    return len(missing) == 0, filled, missing


class WorkflowLoader:
    """Discovers and loads workflow YAML definitions."""

    @staticmethod
    def list_workflows(project_root: str | Path | None) -> list[WorkflowDefinition]:
        """List all available workflows (project-local + bundled defaults).

        Project-local workflows override bundled ones with the same slug.
        """
        workflows: dict[str, WorkflowDefinition] = {}

        # Load bundled defaults first
        if _BUNDLED_DIR.is_dir():
            for path in sorted(_BUNDLED_DIR.glob("*.yml")):
                wf = WorkflowLoader._load_file(path)
                if wf:
                    workflows[wf.slug] = wf

        # Project-local overrides
        if project_root:
            local_dir = Path(project_root) / _PROJECT_WORKFLOWS_DIR
            if local_dir.is_dir():
                for path in sorted(local_dir.glob("*.yml")):
                    wf = WorkflowLoader._load_file(path)
                    if wf:
                        workflows[wf.slug] = wf

        return sorted(workflows.values(), key=lambda w: w.name)

    @staticmethod
    def load(name: str, project_root: str | Path | None) -> tuple[WorkflowDefinition | None, str]:
        """Load a workflow by slug name.

        Returns (definition, error_message). If the workflow is found
        and parsed successfully, error_message is empty. If the file
        exists but fails to parse, error_message explains why. If no
        file is found, definition is None and error_message says so.
        """
        # Check project-local
        if project_root:
            local_path = Path(project_root) / _PROJECT_WORKFLOWS_DIR / f"{name}.yml"
            if local_path.is_file():
                wf = WorkflowLoader._load_file(local_path)
                if wf:
                    return wf, ""
                return None, f"File exists at {local_path} but failed to parse. Check YAML syntax."

        # Check bundled
        bundled_path = _BUNDLED_DIR / f"{name}.yml"
        if bundled_path.is_file():
            wf = WorkflowLoader._load_file(bundled_path)
            if wf:
                return wf, ""
            return None, f"Built-in workflow '{name}' failed to parse."

        return None, ""

    @staticmethod
    def seed_defaults(project_root: str | Path) -> int:
        """Copy bundled workflows to ``.polyglot/workflows/`` if absent.

        Returns the number of files seeded.
        """
        target = Path(project_root) / _PROJECT_WORKFLOWS_DIR
        target.mkdir(parents=True, exist_ok=True)
        count = 0

        if not _BUNDLED_DIR.is_dir():
            return 0

        for src in _BUNDLED_DIR.glob("*.yml"):
            dst = target / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                count += 1
                logger.info("Seeded workflow: %s", dst)

        return count

    @staticmethod
    def _load_file(path: Path) -> WorkflowDefinition | None:
        """Parse a single YAML workflow file."""
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not installed — cannot load workflows")
            return None

        try:
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                logger.warning("Workflow %s: expected a YAML mapping", path)
                return None

            inputs = [
                WorkflowInput(
                    name=i.get("name", ""),
                    description=i.get("description", ""),
                    required=i.get("required", True),
                    default=str(i.get("default", "")),
                )
                for i in (data.get("inputs") or [])
                if isinstance(i, dict) and i.get("name")
            ]

            steps = [
                WorkflowStep(
                    name=s.get("name", f"Step {idx + 1}"),
                    prompt=s.get("prompt", ""),
                )
                for idx, s in enumerate(data.get("steps") or [])
                if isinstance(s, dict) and s.get("prompt")
            ]

            if not steps:
                logger.warning("Workflow %s: no valid steps found", path)
                return None

            return WorkflowDefinition(
                name=data.get("name", path.stem),
                description=data.get("description", ""),
                inputs=inputs,
                steps=steps,
                source_path=path,
            )
        except Exception:
            logger.exception("Failed to load workflow from %s", path)
            return None
