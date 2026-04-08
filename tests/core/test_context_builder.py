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
