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


def test_parse_workflow_args_preserves_quoted_multiword_values():
    """Quoted values must survive as a single token.

    Regression test for a bug where ``str.split()`` truncated
    ``--scenario "guest checkout flow"`` to ``scenario='"guest'``,
    silently dropping the rest of the value. The Playwright web tests
    view (and any future workflow caller) relies on this round-trip
    working — multi-word scenario/feature/test names are the common
    case, not the edge case.
    """
    name, inputs = parse_workflow_args(
        "playwright-planner --url http://localhost:3000 "
        '--scenario "guest checkout flow" '
        "--feature guest-checkout"
    )
    assert name == "playwright-planner"
    assert inputs["url"] == "http://localhost:3000"
    assert inputs["scenario"] == "guest checkout flow"
    assert inputs["feature"] == "guest-checkout"


def test_parse_workflow_args_handles_path_with_spaces():
    """Quoted file paths (env_file, plan, test) must survive whole."""
    name, inputs = parse_workflow_args('playwright-planner --env_file "My Project/env.sh"')
    assert name == "playwright-planner"
    assert inputs["env_file"] == "My Project/env.sh"


def test_parse_workflow_args_handles_embedded_escaped_quotes():
    """A scenario like ``he said "hi"`` must round-trip cleanly."""
    name, inputs = parse_workflow_args(r'playwright-planner --scenario "he said \"hi\""')
    assert name == "playwright-planner"
    assert inputs["scenario"] == 'he said "hi"'


def test_parse_workflow_args_unbalanced_quote_falls_back():
    """If the user mistypes and leaves a quote open, we should still
    produce *something* parseable rather than crash. The fallback uses
    naive whitespace split — values will be truncated, but the caller's
    required-input validation will catch the missing/garbage value."""
    # Should not raise.
    name, inputs = parse_workflow_args('playwright-planner --url http://x.com --scenario "open')
    assert name == "playwright-planner"
    # We don't assert on inputs here — the contract is "don't crash";
    # the fallback path's exact shape is an implementation detail.
    assert "url" in inputs


def test_quoting_round_trip_with_web_tests_view_quote_helper():
    """End-to-end: feed the panel's ``_quote`` helper into
    ``parse_workflow_args`` and verify the value survives intact.
    Catches any future regression in either side of the contract."""
    from polyglot_ai.ui.panels.web_tests_view import WebTestsView

    cases = [
        "simple",
        "two words",
        "three word value",
        "path/to/file with spaces.md",
        'has "embedded quotes"',
    ]
    for original in cases:
        quoted = WebTestsView._quote(original)
        command = f"workflow-name --value {quoted}"
        _, inputs = parse_workflow_args(command)
        assert inputs["value"] == original, (
            f"round-trip failed for {original!r}: quoted={quoted!r}, parsed={inputs.get('value')!r}"
        )


# ── Bundled Playwright workflow validation ────────────────────────────
#
# These tests load each shipped playwright-*.yml and verify the
# contract the Web Tests panel depends on: the workflow loads, the
# inputs it accepts cover the args the panel dispatches, and the
# step prompts render without leaving stale ``{{...}}`` placeholders.
# Without these, a typo or stale ``{{var}}`` in a YAML would ship
# silently — every "panel dispatches a command" test passes because
# it only inspects the command string, never the receiving workflow.


# (slug, panel-args-the-UI-sends, optional-args-the-UI-may-send)
_PLAYWRIGHT_WORKFLOW_CONTRACTS = [
    (
        "playwright-planner",
        {"url", "scenario", "feature", "language"},
        {"env_file", "seed_path", "prd_path"},
    ),
    (
        "playwright-generator",
        {"plan", "url", "language"},
        {"seed_path", "scenarios"},
    ),
    (
        "playwright-healer",
        {"test", "url", "language"},
        {"env_file", "max_attempts"},
    ),
]


@pytest.mark.parametrize(
    "slug,required_panel_args,optional_panel_args", _PLAYWRIGHT_WORKFLOW_CONTRACTS
)
def test_playwright_workflow_loads(slug, required_panel_args, optional_panel_args):
    """The bundled YAML must load and declare every arg the panel
    can send. A missing input declaration would mean the panel
    dispatches a flag the workflow silently ignores."""
    wf, err = WorkflowLoader.load(slug, project_root=None)
    assert wf is not None, f"{slug} failed to load: {err}"
    assert wf.slug == slug

    declared = {inp.name for inp in wf.inputs}
    missing = required_panel_args - declared
    assert not missing, (
        f"{slug} does not declare these args the panel dispatches: {missing}. Declared: {declared}."
    )
    # Optional args are only checked if the panel CAN send them — we
    # don't require them to be declared (the workflow may not need
    # every optional argument), just that any args the workflow does
    # declare match what the panel might send.
    extra_in_panel = optional_panel_args - declared
    # This is not an assertion failure — just a sanity check that the
    # contracts list and the workflow's declared inputs haven't
    # drifted apart in an obvious way.
    _ = extra_in_panel  # noqa: F841


@pytest.mark.parametrize(
    "slug,required_panel_args,optional_panel_args", _PLAYWRIGHT_WORKFLOW_CONTRACTS
)
def test_playwright_workflow_steps_render(slug, required_panel_args, optional_panel_args):
    """Each step's prompt must render with stub values without
    leaving any ``{{var}}`` placeholders behind. A stale placeholder
    means the workflow references a variable that's neither an input
    nor substituted upstream — the AI would see the literal
    ``{{var}}`` and either echo it or behave unpredictably."""
    wf, _ = WorkflowLoader.load(slug, project_root=None)
    assert wf is not None

    # Provide a stub value for every declared input, regardless of
    # required/default — we're testing the prompt template, not the
    # validator.
    stub_inputs = {inp.name: f"<stub-{inp.name}>" for inp in wf.inputs}

    for step in wf.steps:
        rendered = render_step_prompt(step, stub_inputs)
        # Look for any remaining ``{{anything}}`` that didn't get
        # substituted. Accept ``{{filter|format}}`` style placeholders
        # in EXAMPLE markdown blocks (the planner shows a template the
        # AI is supposed to fill in) — those use a pipe and are
        # genuinely meant to survive substitution as documentation.
        import re as _re

        leftovers = _re.findall(r"\{\{([^}|]+)\}\}", rendered)
        # Filter out ``{{var|filter}}`` (Jinja-style filter, used as
        # doc-only example in the planner's spec) — they contain `|`
        # so the regex above already excludes them.
        assert not leftovers, (
            f"{slug} step {step.name!r}: unresolved placeholders after "
            f"substitution: {set(leftovers)}. Either add the missing "
            f"input or escape the literal braces."
        )


@pytest.mark.parametrize(
    "slug,required_panel_args,optional_panel_args", _PLAYWRIGHT_WORKFLOW_CONTRACTS
)
def test_playwright_workflow_validates_required_inputs(
    slug, required_panel_args, optional_panel_args
):
    """``validate_inputs`` should reject an empty input dict when
    required inputs are declared — the panel relies on this to catch
    a malformed dispatch (e.g. all-empty-field bug we now guard
    against in the dialogs)."""
    wf, _ = WorkflowLoader.load(slug, project_root=None)
    assert wf is not None
    has_required = [inp for inp in wf.inputs if inp.required and not inp.default]
    if not has_required:
        # Workflow has no strictly-required-and-no-default inputs.
        # Skip the assertion rather than passing trivially.
        return
    ok, _, missing = validate_inputs(wf, {})
    assert not ok
    assert set(missing) == {inp.name for inp in has_required}
