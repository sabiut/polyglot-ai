"""Workflow command handling for the chat panel.

Extracted from ``chat_panel.py``. Handles:

* ``/workflow`` — list available workflows
* ``/workflow seed`` — copy the built-in catalogue into ``.polyglot/workflows/``
* ``/workflow <name> [--key value ...]`` — run a workflow autonomously

Workflows are multi-step prompt sequences defined in YAML. Each step
is injected as a user message and the normal streaming loop handles
the response — the "autonomous" framing is just a prompt prefix that
discourages the model from asking for confirmation mid-run.

The functions here take ``panel`` as the first argument so they can
reach whatever state they need (current conversation, model picker,
task manager, welcome widget, etc.) without a formal interface. The
panel still owns all widget mutation and the streaming call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polyglot_ai.ui.panels.chat_panel import ChatPanel

logger = logging.getLogger(__name__)


def handle_workflow_command(panel: "ChatPanel", arg: str) -> None:
    """Entry point for ``/workflow [...]``."""
    from polyglot_ai.core.workflow_engine import (
        WorkflowLoader,
        parse_workflow_args,
        validate_inputs,
    )

    project_root = panel._get_project_root()

    if not arg.strip():
        # List available workflows
        workflows = WorkflowLoader.list_workflows(project_root)
        if not workflows:
            panel._add_system_message(
                "No workflows found. Add YAML files to "
                "`.polyglot/workflows/` or run `/workflow seed` to "
                "create the built-in defaults."
            )
            return
        lines = ["**Available workflows:**"]
        for wf in workflows:
            inputs_hint = ""
            required = [i for i in wf.inputs if i.required]
            if required:
                inputs_hint = " " + " ".join(f"--{i.name} <value>" for i in required)
            lines.append(f"• `/workflow {wf.slug}{inputs_hint}` — {wf.description}")
        panel._add_system_message("\n".join(lines))
        return

    if arg.strip() == "seed":
        if not project_root:
            panel._add_system_message("Open a project first to seed workflows.")
            return
        count = WorkflowLoader.seed_defaults(project_root)
        panel._add_system_message(
            f"Seeded {count} workflow(s) to `.polyglot/workflows/`. "
            "Run `/workflow` to see the list."
        )
        return

    name, inputs = parse_workflow_args(arg)
    definition, load_error = WorkflowLoader.load(name, project_root)
    if not definition:
        msg = f"Workflow '{name}' not found. Run `/workflow` to see available workflows."
        if load_error:
            msg = f"Workflow '{name}' could not be loaded: {load_error}"
        panel._add_system_message(msg)
        return

    ok, filled_inputs, missing = validate_inputs(definition, inputs)
    if not ok:
        missing_hints = ", ".join(f"`--{m}`" for m in missing)
        panel._add_system_message(
            f"Missing required inputs for **{definition.name}**: {missing_hints}\n\n"
            f"Usage: `/workflow {name} {' '.join(f'--{m} <value>' for m in missing)}`"
        )
        return

    start_workflow(panel, definition, filled_inputs)


def start_workflow(panel: "ChatPanel", definition, inputs: dict[str, str]) -> None:
    """Kick off a workflow — ensures a conversation exists, then runs steps."""
    if panel._workflow_running:
        panel._add_system_message("A workflow is already running.")
        return
    if not panel._provider_manager or not panel._provider_manager.has_providers:
        panel._add_system_message("Please sign in or add an API key first.")
        return

    full_id, display_model = panel._get_selected_model()
    if not full_id:
        panel._add_system_message("Please select a model from the dropdown.")
        return

    # Ensure a conversation exists
    if panel._current_conversation is None:
        from polyglot_ai.core.ai.models import Conversation

        panel._current_conversation = Conversation(model=full_id or display_model)
        panel._persisted_message_count = 0

    panel._welcome.hide()
    panel._workflow_running = True

    # Show workflow start banner
    input_summary = ", ".join(f"{k}={v}" for k, v in inputs.items())
    panel._add_system_message(
        f"**⚡ Starting workflow: {definition.name}**\n"
        f"{definition.description}\n"
        f"Inputs: {input_summary}\n"
        f"Steps: {len(definition.steps)}"
    )

    # Record on active task (best-effort — don't let audit trail fail the run)
    if hasattr(panel, "_task_manager") and panel._task_manager:
        try:
            panel._task_manager.add_note(
                "workflow_started",
                f"Workflow started: {definition.name}",
                data={
                    "workflow": definition.slug,
                    "inputs": inputs,
                    "steps": len(definition.steps),
                },
                category="workflow",
            )
        except Exception:
            logger.debug("Failed to record workflow_started note", exc_info=True)

    from polyglot_ai.core.async_utils import safe_task

    try:
        safe_task(
            _run_workflow_steps(panel, definition, inputs),
            name=f"workflow_{definition.slug}",
            on_error=lambda e: on_workflow_error(panel, definition, inputs, e),
        )
    except Exception:
        panel._workflow_running = False
        panel._add_system_message("Failed to start workflow.")


async def _run_workflow_steps(panel: "ChatPanel", definition, inputs: dict[str, str]) -> None:
    """Execute each step by injecting its prompt and streaming the response."""
    from polyglot_ai.core.ai.models import Message
    from polyglot_ai.core.workflow_engine import render_step_prompt

    completed = 0
    try:
        for i, step in enumerate(definition.steps):
            if not panel._workflow_running:
                break  # cancelled
            if panel._current_conversation is None:
                panel._add_system_message("Conversation closed during workflow.")
                break

            # Show step header
            panel._add_system_message(f"**Step {i + 1}/{len(definition.steps)}: {step.name}**")

            # Render and inject the step prompt as a user message. The
            # autonomous-mode prefix tells the model not to stop for
            # confirmation — launching the workflow IS the user's
            # confirmation for every step. Without this the model
            # frequently pauses mid-workflow to ask "should I continue?"
            prompt = render_step_prompt(step, inputs)
            prompt = (
                "[AUTONOMOUS WORKFLOW MODE — Do NOT ask for permission or "
                "confirmation. Execute everything in this step immediately. "
                "If something fails, fix it and retry. Never say 'Should I "
                "go ahead?' — just do it.]\n\n" + prompt
            )
            panel._current_conversation.messages.append(Message(role="user", content=prompt))
            panel._add_message_widget("user", prompt)

            # Stream the AI response (reuses the full tool-calling loop)
            await panel._stream_response()
            completed += 1

    except asyncio.CancelledError:
        panel._add_system_message("Workflow cancelled.")
    except Exception as e:
        logger.exception("Workflow step failed")
        panel._add_system_message(f"Workflow error at step {completed + 1}: {e}")
    finally:
        finish_workflow(panel, definition, inputs, completed)


def finish_workflow(
    panel: "ChatPanel", definition, inputs: dict[str, str], steps_completed: int
) -> None:
    """Clean up after a workflow completes or fails."""
    panel._workflow_running = False
    total = len(definition.steps)
    status = "completed" if steps_completed == total else "partial"

    panel._add_system_message(
        f"**⚡ Workflow finished: {definition.name}** — {steps_completed}/{total} steps {status}"
    )

    # Record on active task (best-effort — the run already finished)
    if hasattr(panel, "_task_manager") and panel._task_manager:
        try:
            panel._task_manager.add_note(
                "workflow_run",
                f"Workflow {status}: {definition.name} ({steps_completed}/{total} steps)",
                data={
                    "workflow": definition.slug,
                    "inputs": inputs,
                    "steps_completed": steps_completed,
                    "steps_total": total,
                    "status": status,
                },
                category="workflow",
            )
        except Exception:
            logger.debug("Failed to record workflow_run note", exc_info=True)

    # Publish to panel state so AI tool calls can see the last run.
    # Failure here is purely observability — never bubble it up.
    try:
        from polyglot_ai.core import panel_state

        panel_state.set_last_workflow_run(
            {
                "workflow": definition.slug,
                "name": definition.name,
                "status": status,
                "steps_completed": steps_completed,
                "steps_total": total,
                "inputs": inputs,
            }
        )
    except Exception:
        logger.debug("Failed to publish workflow state", exc_info=True)


def on_workflow_error(panel: "ChatPanel", definition, inputs: dict, error: Exception) -> None:
    """Callback for workflow task failures — ensures cleanup happens."""
    panel._add_system_message(f"Workflow failed: {error}")
    finish_workflow(panel, definition, inputs, 0)
