"""Tests for ``ContextBuilder.set_active_task`` and prompt rendering.

The active-task block is the entire mechanism by which the task
manager influences AI responses. These tests pin:

- clearing the block with ``None``
- duck-typed task objects work (documented contract)
- defensive ``getattr`` chain on partial/missing attrs
- the block renders BEFORE the boilerplate (load-bearing per comment)
- the 20-file truncation on ``modified_files``
"""

from __future__ import annotations

from types import SimpleNamespace

from polyglot_ai.core.ai.context import ContextBuilder


def _kind(value: str) -> SimpleNamespace:
    """Mimic the ``TaskKind`` enum's ``.value`` attribute."""
    return SimpleNamespace(value=value)


def _state(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _task(**overrides) -> SimpleNamespace:
    """Build a duck-typed task object with sensible defaults."""
    defaults = {
        "kind": _kind("feature"),
        "title": "Add CSV export",
        "description": "",
        "branch": None,
        "state": _state("planning"),
        "modified_files": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_no_active_task_has_no_task_block():
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert "ACTIVE TASK" not in prompt


def test_set_active_task_none_clears_block():
    cb = ContextBuilder()
    cb.set_active_task(_task())
    cb.set_active_task(None)
    assert "ACTIVE TASK" not in cb.build_system_prompt()


def test_set_active_task_renders_title_kind_state():
    cb = ContextBuilder()
    cb.set_active_task(_task(kind=_kind("bugfix"), state=_state("active")))
    prompt = cb.build_system_prompt()
    assert "ACTIVE TASK" in prompt
    assert "Add CSV export" in prompt
    assert "Kind:  bugfix" in prompt
    assert "State: active" in prompt


def test_set_active_task_renders_branch_when_set():
    cb = ContextBuilder()
    cb.set_active_task(_task(branch="feat/csv"))
    assert "Branch: feat/csv" in cb.build_system_prompt()


def test_set_active_task_skips_branch_when_none():
    cb = ContextBuilder()
    cb.set_active_task(_task(branch=None))
    assert "Branch:" not in cb.build_system_prompt()


def test_set_active_task_renders_description_block():
    cb = ContextBuilder()
    cb.set_active_task(_task(description="Export user reports as CSV"))
    prompt = cb.build_system_prompt()
    assert "Description:" in prompt
    assert "Export user reports as CSV" in prompt


def test_set_active_task_omits_description_when_empty():
    cb = ContextBuilder()
    cb.set_active_task(_task(description=""))
    assert "Description:" not in cb.build_system_prompt()


def test_set_active_task_renders_modified_files():
    cb = ContextBuilder()
    cb.set_active_task(_task(modified_files=["src/a.py", "src/b.py"]))
    prompt = cb.build_system_prompt()
    assert "Files touched so far on this task:" in prompt
    assert "- src/a.py" in prompt
    assert "- src/b.py" in prompt


def test_set_active_task_truncates_files_over_20():
    """The "... and N more" line kicks in past 20 files."""
    files = [f"src/m{i}.py" for i in range(25)]
    cb = ContextBuilder()
    cb.set_active_task(_task(modified_files=files))
    prompt = cb.build_system_prompt()
    assert "- src/m0.py" in prompt
    assert "- src/m19.py" in prompt
    assert "- src/m20.py" not in prompt, "files beyond the 20-item window must not render"
    assert "... and 5 more" in prompt


def test_set_active_task_survives_missing_attributes():
    """The ``getattr(..., None)`` chain should tolerate half-built objects."""
    minimal = SimpleNamespace(title="Just a title")  # no kind, no state, etc.
    cb = ContextBuilder()
    cb.set_active_task(minimal)
    prompt = cb.build_system_prompt()
    assert "Just a title" in prompt
    # Kind/state lines should simply be omitted, not crash.
    assert "Kind:" not in prompt
    assert "State:" not in prompt


def test_active_task_block_precedes_boilerplate():
    """The task block is the first thing the model reads — load-bearing.

    If a refactor ever moves the ``ACTIVE TASK`` block below the "You
    are a coding assistant" boilerplate, this test fails and forces a
    conscious decision.
    """
    cb = ContextBuilder()
    cb.set_active_task(_task(title="Load-bearing ordering"))
    prompt = cb.build_system_prompt()

    active_idx = prompt.index("ACTIVE TASK")
    boilerplate_idx = prompt.index("You are a coding assistant")
    assert active_idx < boilerplate_idx


def test_plan_steps_render_in_prompt_when_present():
    """Generated plan steps must reach the model — the chat needs to
    see them so the user can ask "what's step 3?" without re-pasting.
    """
    plan = [
        SimpleNamespace(text="Sketch the routes", status="done"),
        SimpleNamespace(text="Stand up the templates", status="in_progress"),
        SimpleNamespace(text="Wire the contact form", status="pending"),
        SimpleNamespace(text="Write deployment notes", status="pending"),
    ]
    cb = ContextBuilder()
    cb.set_active_task(
        _task(
            modified_files=[],
            **{"description": ""},
        )
    )
    # Re-set with plan attached (the helper doesn't take a plan kwarg).
    task_with_plan = _task()
    task_with_plan.plan = plan
    cb.set_active_task(task_with_plan)

    prompt = cb.build_system_prompt()
    assert "Plan checklist:" in prompt
    assert "Sketch the routes" in prompt
    assert "Stand up the templates" in prompt
    assert "Wire the contact form" in prompt
    # Status glyphs render the right state.
    assert "[x] Sketch the routes" in prompt
    assert "[~] Stand up the templates" in prompt
    assert "[ ] Wire the contact form" in prompt


def test_plan_block_omitted_when_empty():
    cb = ContextBuilder()
    task = _task()
    task.plan = []
    cb.set_active_task(task)
    assert "Plan checklist:" not in cb.build_system_prompt()


def test_plan_skips_blank_text_entries():
    cb = ContextBuilder()
    task = _task()
    task.plan = [
        SimpleNamespace(text="", status="pending"),
        SimpleNamespace(text="   ", status="pending"),
        SimpleNamespace(text="Real step", status="pending"),
    ]
    cb.set_active_task(task)
    prompt = cb.build_system_prompt()
    assert "Real step" in prompt
    # Only one numbered line for the real step.
    assert prompt.count("[ ] Real step") == 1


def test_stay_scoped_directive_only_appears_with_task():
    cb = ContextBuilder()
    empty = cb.build_system_prompt()
    assert "Stay scoped to this task" not in empty

    cb.set_active_task(_task())
    with_task = cb.build_system_prompt()
    assert "Stay scoped to this task" in with_task


# ── Panel state block (review snapshot) ─────────────────────────────
#
# The review panel publishes a snapshot to ``core.panel_state`` after
# every run. The ContextBuilder reads that and renders a compact
# ``--- PANEL STATE ---`` block in the system prompt so the AI can
# answer "what did the last review find?" without the user pasting
# anything and without the user having to be inside a task. These
# tests pin:
#
# - block is omitted when no review has been published
# - block shows mode, status, file list, counts, top-N findings
# - nudge to call get_review_findings is present
# - block is positioned BEFORE the boilerplate rules (load-bearing:
#   buried panel state gets ignored by most models)
# - works with NO active task (primary use case — users who just
#   run a review without any taskflow involvement)


import pytest  # noqa: E402

from polyglot_ai.core import panel_state  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_panel_state():
    panel_state.clear()
    yield
    panel_state.clear()


def _review_snapshot(findings=None, mode="docker_compose", status="ok"):
    findings = findings or []
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return {
        "mode": mode,
        "files": ["docker-compose.yml", "docker-compose.prod.yml"],
        "findings": findings,
        "counts": counts,
        "total": len(findings),
        "status": status,
        "model": "claude-opus-4-6",
        "provider": "anthropic",
        "summary": "",
        "timestamp": "2026-04-10T12:00:00+00:00",
    }


def _finding(file, line, severity, title):
    return {
        "file": file,
        "line": line,
        "severity": severity,
        "category": "security",
        "title": title,
        "body": "",
        "suggestion": None,
    }


def test_panel_state_block_absent_when_no_review():
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert "PANEL STATE" not in prompt


def test_panel_state_block_renders_without_active_task():
    """Primary use case: user never touched the task panel, just ran
    a review. The block must appear regardless of task state."""
    panel_state.set_last_review(
        _review_snapshot(
            [
                _finding("docker-compose.yml", 14, "critical", "privileged: true on db"),
                _finding("docker-compose.prod.yml", 22, "critical", "password hardcoded"),
                _finding("docker-compose.yml", 31, "high", "docker.sock mounted"),
                _finding("docker-compose.yml", 40, "medium", "no healthcheck"),
            ]
        )
    )
    cb = ContextBuilder()
    # Explicitly NO set_active_task call
    prompt = cb.build_system_prompt()

    assert "ACTIVE TASK" not in prompt
    assert "--- PANEL STATE ---" in prompt
    assert "Last review: docker_compose" in prompt
    assert "docker-compose.yml" in prompt
    assert "docker-compose.prod.yml" in prompt
    assert "Findings: 4" in prompt
    assert "2 critical" in prompt
    assert "1 high" in prompt
    assert "1 medium" in prompt


def test_panel_state_block_lists_top_findings_and_nudges_tool():
    findings = [
        _finding("a.yml", 1, "critical", "privileged root container"),
        _finding("a.yml", 5, "critical", "secret in env"),
        _finding("b.yml", 3, "high", "host network mode"),
    ]
    panel_state.set_last_review(_review_snapshot(findings))

    cb = ContextBuilder()
    prompt = cb.build_system_prompt()

    assert "[CRITICAL] a.yml:1 privileged root container" in prompt
    assert "[CRITICAL] a.yml:5 secret in env" in prompt
    assert "[HIGH] b.yml:3 host network mode" in prompt
    assert "get_review_findings" in prompt


def test_panel_state_block_truncates_to_top_5():
    findings = [_finding(f"f{i}.yml", i, "high", f"issue {i}") for i in range(10)]
    panel_state.set_last_review(_review_snapshot(findings))

    cb = ContextBuilder()
    prompt = cb.build_system_prompt()

    for i in range(5):
        assert f"issue {i}" in prompt
    assert "and 5 more" in prompt


def test_panel_state_block_clean_review_shows_zero():
    panel_state.set_last_review(_review_snapshot([]))
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert "Findings: 0 (clean)" in prompt


def test_panel_state_block_renders_before_boilerplate():
    panel_state.set_last_review(_review_snapshot([_finding("a.yml", 1, "high", "issue")]))
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert prompt.index("PANEL STATE") < prompt.index("IMPORTANT RULES")


def test_failed_review_status_rendered():
    panel_state.set_last_review(_review_snapshot([], status="failed"))
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert "Last review: docker_compose (status: failed)" in prompt


# ── Empty-project directive ─────────────────────────────────────────
#
# When the user opens an empty directory and asks the AI to "build X",
# the model would otherwise silently pick a framework and scaffold it.
# These tests pin the directive that forces a stack question first.


def test_empty_project_directive_present_for_empty_dir(tmp_path):
    project = tmp_path / "fresh"
    project.mkdir()
    cb = ContextBuilder(project_root=project)
    prompt = cb.build_system_prompt()
    assert "EMPTY PROJECT DIRECTIVE" in prompt
    assert "BEFORE calling create_plan" in prompt
    assert "ask the user which stack" in prompt


def test_empty_project_directive_absent_when_source_files_exist(tmp_path):
    project = tmp_path / "has-code"
    project.mkdir()
    (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
    cb = ContextBuilder(project_root=project)
    prompt = cb.build_system_prompt()
    assert "EMPTY PROJECT DIRECTIVE" not in prompt


def test_empty_project_directive_absent_when_project_type_detected(tmp_path):
    """pyproject.toml alone (no source yet) still counts as a known
    stack — the user has already chosen Python, don't nag them."""
    project = tmp_path / "py-bootstrap"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    cb = ContextBuilder(project_root=project)
    prompt = cb.build_system_prompt()
    assert "EMPTY PROJECT DIRECTIVE" not in prompt


def test_empty_project_directive_absent_when_no_project_root():
    cb = ContextBuilder()
    prompt = cb.build_system_prompt()
    assert "EMPTY PROJECT DIRECTIVE" not in prompt


def test_panel_state_block_coexists_with_active_task():
    """Task block and panel state both render; task first, then panel."""
    panel_state.set_last_review(_review_snapshot([_finding("a.yml", 1, "high", "issue")]))
    cb = ContextBuilder()
    cb.set_active_task(_task())
    prompt = cb.build_system_prompt()

    assert "ACTIVE TASK" in prompt
    assert "PANEL STATE" in prompt
    assert prompt.index("ACTIVE TASK") < prompt.index("PANEL STATE")


# ── build_augmented_prompt (RAG) ─────────────────────────────────────
#
# This path was dead code until the chat panel started calling it
# (gated on ai.auto_context); pin its contract now that it's live.


class _StubIndexer:
    def __init__(self, results, ready=True):
        self._results = results
        self.is_ready = ready

    def query(self, text, top_k=5):
        return self._results


def _builder_with_files(tmp_path, files: dict[str, str]) -> ContextBuilder:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    cb = ContextBuilder(project_root=tmp_path)
    return cb


def test_augmented_prompt_appends_relevant_files(tmp_path):
    cb = _builder_with_files(tmp_path, {"app.py": "def main():\n    return 1\n"})
    cb.set_indexer(_StubIndexer([("app.py", 0.9)]))
    prompt = cb.build_augmented_prompt("how does main work?")
    assert "RELEVANT FILES" in prompt
    assert "--- app.py ---" in prompt
    assert "def main():" in prompt


def test_augmented_prompt_without_indexer_is_base_prompt(tmp_path):
    cb = _builder_with_files(tmp_path, {"app.py": "x = 1\n"})
    assert cb.build_augmented_prompt("query") == cb.build_system_prompt()


def test_augmented_prompt_index_not_ready_is_base_prompt(tmp_path):
    cb = _builder_with_files(tmp_path, {"app.py": "x = 1\n"})
    cb.set_indexer(_StubIndexer([("app.py", 0.9)], ready=False))
    assert cb.build_augmented_prompt("query") == cb.build_system_prompt()


def test_augmented_prompt_skips_files_with_secrets(tmp_path):
    cb = _builder_with_files(
        tmp_path,
        {"config.py": 'AWS_SECRET_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLEKEY9"\n'},
    )
    cb.set_indexer(_StubIndexer([("config.py", 0.9)]))
    prompt = cb.build_augmented_prompt("aws setup")
    # The base prompt's project tree may list the file name; what must
    # NOT appear is the RAG content block for it.
    assert "--- config.py ---" not in prompt
    assert "AKIAIOSFODNN7EXAMPLEKEY9" not in prompt


def test_augmented_prompt_skips_missing_files(tmp_path):
    cb = ContextBuilder(project_root=tmp_path)
    cb.set_indexer(_StubIndexer([("gone.py", 0.9)]))
    prompt = cb.build_augmented_prompt("query")
    assert "--- gone.py ---" not in prompt
