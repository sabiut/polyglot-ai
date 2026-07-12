"""Plan executor — runs approved plan steps with AI assistance."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from polyglot_ai.core.ai.plan_models import Plan, PlanStatus, PlanStep, PlanStepStatus
from polyglot_ai.core.ai.tool_streaming import ToolCallAccumulator
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

EVT_PLAN_STEP_STARTED = "plan:step_started"
EVT_PLAN_STEP_COMPLETED = "plan:step_completed"
EVT_PLAN_STEP_FAILED = "plan:step_failed"
EVT_PLAN_DONE = "plan:done"


class PlanExecutor:
    """Execute a plan step-by-step using an AI provider and tool registry."""

    def __init__(
        self,
        provider,
        model_id: str,
        tool_registry,
        event_bus: EventBus,
        system_prompt: str = "",
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._tools = tool_registry
        self._bus = event_bus
        self._system_prompt = system_prompt
        self._messages: list[dict] = []

    def set_messages(self, messages: list[dict]) -> None:
        """Set initial conversation context."""
        self._messages = list(messages)

    async def execute(
        self,
        plan: Plan,
        on_stream: Callable[[int, str], None] | None = None,
        on_tool_approval: Callable[[str, dict], Any] | None = None,
    ) -> None:
        """Execute all approved steps in a plan."""
        plan.status = PlanStatus.EXECUTING

        for step in plan.steps:
            if step.status in (PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED):
                continue
            if step.status not in (PlanStepStatus.APPROVED, PlanStepStatus.FAILED):
                continue

            step.status = PlanStepStatus.IN_PROGRESS
            self._bus.emit(EVT_PLAN_STEP_STARTED, plan=plan, step=step)

            try:
                await self._execute_step(plan, step, on_stream, on_tool_approval)
                step.status = PlanStepStatus.COMPLETED
                self._bus.emit(EVT_PLAN_STEP_COMPLETED, plan=plan, step=step)
            except Exception as e:
                step.status = PlanStepStatus.FAILED
                step.result = str(e)
                self._bus.emit(EVT_PLAN_STEP_FAILED, plan=plan, step=step)
                plan.status = PlanStatus.PAUSED
                self._bus.emit(EVT_PLAN_DONE, plan=plan)
                return

        plan.status = PlanStatus.COMPLETED
        self._bus.emit(EVT_PLAN_DONE, plan=plan)

    async def _execute_step(
        self,
        plan: Plan,
        step: PlanStep,
        on_stream: Callable[[int, str], None] | None,
        on_tool_approval: Callable[[str, dict], Any] | None,
    ) -> None:
        """Execute a single plan step with tool calling loop."""
        # Build step prompt
        step_prompt = (
            f"Execute step {step.index + 1}: {step.title}\n\nDescription: {step.description}\n"
        )
        if step.files_affected:
            step_prompt += f"Files: {', '.join(step.files_affected)}\n"

        self._messages.append({"role": "user", "content": step_prompt})

        # Tool-calling loop
        max_iterations = 10
        for iteration in range(max_iterations):
            messages = []
            if self._system_prompt:
                messages.append({"role": "system", "content": self._system_prompt})
            messages.extend(self._messages)

            # Get tool definitions
            tools = self._tools.get_tool_definitions() if self._tools else None

            # Stream response
            full_content = ""
            tool_acc = ToolCallAccumulator()
            finish_reason = None

            async for chunk in self._provider.stream_chat(
                messages=messages,
                model=self._model_id,
                tools=tools,
            ):
                if chunk.delta_content:
                    full_content += chunk.delta_content
                    if on_stream:
                        on_stream(step.index, chunk.delta_content)

                tool_acc.add_chunk(chunk.tool_calls)

                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

            tool_calls = tool_acc.build()

            # Store assistant message
            assistant_msg = {"role": "assistant", "content": full_content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function_name,
                            "arguments": tc.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            self._messages.append(assistant_msg)

            # If no tool calls, step is done
            if not tool_calls or finish_reason not in ("tool_calls", "tool_use"):
                step.result = full_content
                break

            # Execute tool calls
            for tc_idx, tc in enumerate(tool_calls):
                tool_name = tc.function_name
                tool_call_id = tc.id or f"call_{tc_idx}"
                try:
                    args = json.loads(tc.arguments)
                except Exception:
                    args = {}

                # Check approval
                needs_approval = self._tools and not self._tools.is_auto_approved(tool_name)
                if needs_approval and on_tool_approval:
                    approved = await on_tool_approval(tool_name, args)
                    if not approved:
                        step.status = PlanStepStatus.FAILED
                        step.result = f"User rejected {tool_name}"
                        self._bus.emit(EVT_PLAN_STEP_FAILED, plan=plan, step=step)
                        return

                # Execute tool — ToolRegistry.execute() expects a JSON string
                if self._tools:
                    args_str = json.dumps(args)
                    result = await self._tools.execute(tool_name, args_str)
                else:
                    result = "No tool registry available"

                self._messages.append(
                    {
                        "role": "tool",
                        "content": result,
                        "tool_call_id": tool_call_id,
                    }
                )
