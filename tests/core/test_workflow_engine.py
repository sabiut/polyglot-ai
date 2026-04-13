"""Tests for the workflow engine — YAML loading, template rendering, input validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from polyglot_ai.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowInput,
    WorkflowLoader,
    WorkflowStep,
    parse_workflow_args,
    render_step_prompt,
    validate_inputs,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".polyglot" / "workflows"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_yaml(workflows_dir: Path) -> Path:
    path = workflows_dir / "smoke-test.yml"
    path.write_text(
        dedent("""\
        name: Smoke Test
        description: Quick health check
        inputs:
          - name: url
            description: Target URL
            required: true
          - name: env
            description: Environment
            required: false
            default: staging
        steps:
          - name: Load page
            prompt: Navigate to {{url}} on {{env}} and take a screenshot.
          - name: Check errors
            prompt: Check console errors on {{url}}.
        """),
        encoding="utf-8",
    )
    return path


# ── YAML loading ───────────────────────────────────────────────────────


def test_load_file_parses_inputs_and_steps(sample_yaml: Path):
    wf = WorkflowLoader._load_file(sample_yaml)
    assert wf is not None
    assert wf.name == "Smoke Test"
    assert wf.description == "Quick health check"
    assert len(wf.inputs) == 2
    assert wf.inputs[0].name == "url"
    assert wf.inputs[0].required is True
    assert wf.inputs[1].name == "env"
    assert wf.inputs[1].default == "staging"
    assert len(wf.steps) == 2
    assert wf.steps[0].name == "Load page"
    assert "{{url}}" in wf.steps[0].prompt


def test_load_file_returns_none_for_empty_steps(tmp_path: Path):
    path = tmp_path / "empty.yml"
    path.write_text("name: Empty\nsteps: []\n", encoding="utf-8")
    assert WorkflowLoader._load_file(path) is None


def test_load_file_returns_none_for_invalid_yaml(tmp_path: Path):
    path = tmp_path / "bad.yml"
    path.write_text("not: valid: yaml: {{{{", encoding="utf-8")
    assert WorkflowLoader._load_file(path) is None


def test_load_file_returns_none_for_nonexistent():
    assert WorkflowLoader._load_file(Path("/nonexistent/workflow.yml")) is None


def test_load_file_skips_steps_without_prompt(tmp_path: Path):
    path = tmp_path / "partial.yml"
    path.write_text(
        dedent("""\
        name: Partial
        steps:
          - name: Has prompt
            prompt: Do something
          - name: No prompt
          - name: Also has prompt
            prompt: Do another thing
        """),
        encoding="utf-8",
    )
    wf = WorkflowLoader._load_file(path)
    assert wf is not None
    assert len(wf.steps) == 2
    assert wf.steps[0].name == "Has prompt"
    assert wf.steps[1].name == "Also has prompt"


# ── Slug ───────────────────────────────────────────────────────────────


def test_slug_from_source_path(sample_yaml: Path):
    wf = WorkflowLoader._load_file(sample_yaml)
    assert wf is not None
    assert wf.slug == "smoke-test"


def test_slug_from_name_when_no_path():
    wf = WorkflowDefinition(name="My Custom Workflow")
    assert wf.slug == "my-custom-workflow"


# ── Listing ────────────────────────────────────────────────────────────


def test_list_workflows_finds_project_local(tmp_path: Path, sample_yaml: Path):
    workflows = WorkflowLoader.list_workflows(tmp_path)
    slugs = [w.slug for w in workflows]
    assert "smoke-test" in slugs


def test_list_workflows_includes_bundled():
    # Bundled workflows from src/polyglot_ai/core/workflows/
    workflows = WorkflowLoader.list_workflows(None)
    slugs = [w.slug for w in workflows]
    assert "verify-deploy" in slugs
    assert "investigate-failure" in slugs
    assert "reproduce-bug" in slugs
    assert "record-test" in slugs
    assert "build-infra" in slugs
    assert "infra-health-check" in slugs
    assert "incident-response" in slugs
    assert "security-audit" in slugs
    assert "resource-optimization" in slugs
    assert "pre-deploy-check" in slugs
    assert "db-migration-check" in slugs
    assert "record-test-interactive" in slugs


def test_list_workflows_project_overrides_bundled(tmp_path: Path):
    # Create a project-local file with the same name as a bundled one
    d = tmp_path / ".polyglot" / "workflows"
    d.mkdir(parents=True)
    (d / "verify-deploy.yml").write_text(
        dedent("""\
        name: Custom Verify
        steps:
          - name: Custom step
            prompt: Do custom verification
        """),
        encoding="utf-8",
    )
    workflows = WorkflowLoader.list_workflows(tmp_path)
    vd = next(w for w in workflows if w.slug == "verify-deploy")
    assert vd.name == "Custom Verify"


# ── Loading by name ────────────────────────────────────────────────────


def test_load_by_name_finds_bundled():
    wf, err = WorkflowLoader.load("verify-deploy", None)
    assert wf is not None
    assert wf.name == "Verify Deployment"
    assert err == ""


def test_load_by_name_prefers_project_local(tmp_path: Path, sample_yaml: Path):
    wf, err = WorkflowLoader.load("smoke-test", tmp_path)
    assert wf is not None
    assert wf.name == "Smoke Test"
    assert err == ""


def test_load_by_name_returns_none_for_missing():
    wf, err = WorkflowLoader.load("nonexistent-workflow", None)
    assert wf is None
    assert err == ""


def test_load_returns_error_for_broken_yaml(tmp_path: Path, workflows_dir: Path):
    (workflows_dir / "broken.yml").write_text("not: valid: yaml: {{{{")
    wf, err = WorkflowLoader.load("broken", tmp_path)
    assert wf is None
    assert "failed to parse" in err.lower()


# ── Seeding defaults ───────────────────────────────────────────────────


def test_seed_defaults_copies_bundled(tmp_path: Path):
    count = WorkflowLoader.seed_defaults(tmp_path)
    assert count >= 12
    target = tmp_path / ".polyglot" / "workflows"
    assert (target / "verify-deploy.yml").is_file()
    assert (target / "investigate-failure.yml").is_file()
    assert (target / "reproduce-bug.yml").is_file()
    assert (target / "record-test.yml").is_file()
    assert (target / "build-infra.yml").is_file()
    assert (target / "infra-health-check.yml").is_file()
    assert (target / "incident-response.yml").is_file()
    assert (target / "security-audit.yml").is_file()
    assert (target / "resource-optimization.yml").is_file()
    assert (target / "pre-deploy-check.yml").is_file()
    assert (target / "db-migration-check.yml").is_file()
    assert (target / "record-test-interactive.yml").is_file()


def test_seed_defaults_does_not_overwrite(tmp_path: Path):
    target = tmp_path / ".polyglot" / "workflows"
    target.mkdir(parents=True)
    custom = target / "verify-deploy.yml"
    custom.write_text("name: Custom\nsteps:\n  - name: x\n    prompt: y\n")
    WorkflowLoader.seed_defaults(tmp_path)
    # Custom file should not be overwritten
    assert "Custom" in custom.read_text()


# ── Template rendering ─────────────────────────────────────────────────


def test_render_step_prompt_substitutes_variables():
    step = WorkflowStep(name="test", prompt="Visit {{url}} on {{env}}")
    result = render_step_prompt(step, {"url": "https://example.com", "env": "staging"})
    assert result == "Visit https://example.com on staging"


def test_render_step_prompt_leaves_unknown_variables():
    step = WorkflowStep(name="test", prompt="Check {{url}} with {{unknown}}")
    result = render_step_prompt(step, {"url": "https://example.com"})
    assert "https://example.com" in result
    assert "{{unknown}}" in result


def test_render_step_prompt_handles_empty_inputs():
    step = WorkflowStep(name="test", prompt="No variables here")
    result = render_step_prompt(step, {})
    assert result == "No variables here"


# ── Input validation ───────────────────────────────────────────────────


def test_validate_inputs_all_provided():
    wf = WorkflowDefinition(
        name="Test",
        inputs=[
            WorkflowInput(name="url", required=True),
            WorkflowInput(name="env", required=False, default="staging"),
        ],
    )
    ok, filled, missing = validate_inputs(wf, {"url": "https://example.com"})
    assert ok is True
    assert filled["url"] == "https://example.com"
    assert filled["env"] == "staging"
    assert missing == []


def test_validate_inputs_missing_required():
    wf = WorkflowDefinition(
        name="Test",
        inputs=[
            WorkflowInput(name="url", required=True),
            WorkflowInput(name="token", required=True),
        ],
    )
    ok, filled, missing = validate_inputs(wf, {})
    assert ok is False
    assert "url" in missing
    assert "token" in missing


def test_validate_inputs_no_inputs_defined():
    wf = WorkflowDefinition(name="Test")
    ok, filled, missing = validate_inputs(wf, {"extra": "value"})
    assert ok is True
    assert filled["extra"] == "value"


# ── Argument parsing ──────────────────────────────────────────────────


def test_parse_workflow_args_name_only():
    name, inputs = parse_workflow_args("verify-deploy")
    assert name == "verify-deploy"
    assert inputs == {}


def test_parse_workflow_args_with_flags():
    name, inputs = parse_workflow_args(
        "verify-deploy --url https://staging.example.com --environment staging"
    )
    assert name == "verify-deploy"
    assert inputs["url"] == "https://staging.example.com"
    assert inputs["environment"] == "staging"


def test_parse_workflow_args_empty():
    name, inputs = parse_workflow_args("")
    assert name == ""
    assert inputs == {}


def test_parse_workflow_args_ignores_orphan_flags():
    name, inputs = parse_workflow_args("test --url https://x.com --orphan")
    assert name == "test"
    assert inputs["url"] == "https://x.com"
    assert "orphan" not in inputs
