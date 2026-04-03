"""Agent loop — orchestrates multi-turn tool-call cycles."""

from __future__ import annotations

import asyncio
import logging

from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_TOOL_CALL_REQUEST,
    EVT_APPROVAL_REQUESTED,
    EVT_APPROVAL_RESPONSE,
    MAX_AGENT_ITERATIONS,
)
from polyglot_ai.core.ai.models import Conversation, Message, ToolCall
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.ai.tools import ToolRegistry
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


APPROVAL_TIMEOUT = 300  # 5 minutes max wait for user approval


class AgentLoop:
    """Orchestrates the AI agent's tool-call cycle.

    Works with any AIProvider, not just OpenAI.
    """

    def __init__(
        self,
        client: AIProvider,
        tools: ToolRegistry,
        event_bus: EventBus,
    ) -> None:
        self._client = client
        self._tools = tools
        self._event_bus = event_bus
        self._approval_event = asyncio.Event()
        self._approval_result: bool = False
        self._pending_approval_id: str | None = None  # binds response to request
        self._running = False
        self._subscribed = False

    def _ensure_subscribed(self) -> None:
        """Subscribe to approval events (idempotent)."""
        if not self._subscribed:
            self._event_bus.subscribe(EVT_APPROVAL_RESPONSE, self._on_approval)
            self._subscribed = True

    def _unsubscribe(self) -> None:
        """Unsubscribe from approval events."""
        if self._subscribed:
            self._event_bus.unsubscribe(EVT_APPROVAL_RESPONSE, self._on_approval)
            self._subscribed = False

    def _on_approval(self, approved: bool = False, **kwargs) -> None:
        # Only accept responses that match the pending request ID
        request_id = kwargs.get("request_id")
        if self._pending_approval_id and request_id != self._pending_approval_id:
            logger.warning(
                "Ignoring approval response with mismatched request_id: "
                "expected=%s, got=%s",
                self._pending_approval_id, request_id,
            )
            return
        self._approval_result = approved
        self._approval_event.set()

    async def run(
        self,
        conversation: Conversation,
        system_prompt: str = "",
    ) -> None:
        """Run the agent loop until completion or max iterations."""
        if self._running:
            logger.warning("Agent loop already running")
            return

        self._running = True
        self._ensure_subscribed()
        iteration = 0

        try:
            while iteration < MAX_AGENT_ITERATIONS:
                iteration += 1
                logger.info("Agent iteration %d/%d", iteration, MAX_AGENT_ITERATIONS)

                # Build messages
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.extend(conversation.get_api_messages())

                # Stream response
                full_content = ""
                tool_calls_data: dict[int, dict] = {}
                finish_reason = None

                async for chunk in self._client.stream_chat(
                    messages=messages,
                    model=conversation.model,
                    tools=self._tools.get_tool_definitions(),
                ):
                    if chunk.delta_content:
                        full_content += chunk.delta_content

                    if chunk.tool_calls:
                        for tc in chunk.tool_calls:
                            idx = tc["index"]
                            if idx not in tool_calls_data:
                                tool_calls_data[idx] = {
                                    "id": tc.get("id", ""),
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.get("id"):
                                tool_calls_data[idx]["id"] = tc["id"]
                            func = tc.get("function", {})
                            if func.get("name"):
                                tool_calls_data[idx]["function"]["name"] = func["name"]
                            if func.get("arguments"):
                                tool_calls_data[idx]["function"]["arguments"] += func["arguments"]

                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason

                # Store assistant message
                tool_calls_list = None
                if tool_calls_data:
                    tool_calls_list = [
                        ToolCall(
                            id=tc["id"],
                            function_name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"],
                        )
                        for tc in tool_calls_data.values()
                    ]

                assistant_msg = Message(
                    role="assistant",
                    content=full_content if full_content else None,
                    tool_calls=tool_calls_list,
                    model=conversation.model,
                )
                conversation.messages.append(assistant_msg)

                # If no tool calls, we're done
                # Note: finish_reason varies by provider:
                #   OpenAI: "tool_calls", Anthropic: "tool_use", Google: "tool_calls"
                if not tool_calls_list or finish_reason not in ("tool_calls", "tool_use"):
                    break

                # Process tool calls
                for tc in tool_calls_list:
                    logger.info("Tool call: %s(%s)", tc.function_name, tc.arguments[:100])

                    # Check if approval needed
                    if self._tools.needs_approval(tc.function_name):
                        # Clear BEFORE emit to avoid race condition
                        self._approval_event.clear()

                        # Generate unique request ID to bind response
                        import uuid
                        request_id = str(uuid.uuid4())
                        self._pending_approval_id = request_id

                        self._event_bus.emit(
                            EVT_APPROVAL_REQUESTED,
                            tool_name=tc.function_name,
                            arguments=tc.arguments,
                            tool_call_id=tc.id,
                            request_id=request_id,
                        )

                        # Wait for approval with timeout
                        try:
                            await asyncio.wait_for(
                                self._approval_event.wait(),
                                timeout=APPROVAL_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            logger.warning("Approval timed out for %s", tc.function_name)
                            result_msg = Message(
                                role="tool",
                                content="Approval timed out — tool call skipped.",
                                tool_call_id=tc.id,
                            )
                            conversation.messages.append(result_msg)
                            continue

                        if not self._approval_result:
                            # User rejected
                            result_msg = Message(
                                role="tool",
                                content="User rejected this tool call.",
                                tool_call_id=tc.id,
                            )
                            conversation.messages.append(result_msg)
                            continue

                    # Execute tool
                    self._event_bus.emit(
                        EVT_AI_TOOL_CALL_REQUEST,
                        tool_name=tc.function_name,
                        arguments=tc.arguments,
                    )

                    result = await self._tools.execute(tc.function_name, tc.arguments)

                    # Add tool result message
                    result_msg = Message(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                    )
                    conversation.messages.append(result_msg)

                if iteration >= MAX_AGENT_ITERATIONS:
                    conversation.messages.append(
                        Message(
                            role="assistant",
                            content=f"Reached maximum of {MAX_AGENT_ITERATIONS} iterations. Stopping.",
                        )
                    )
                    self._event_bus.emit(EVT_AI_ERROR, error="Max iterations reached")

        except Exception:
            logger.exception("Agent loop error")
            self._event_bus.emit(EVT_AI_ERROR, error="Agent loop encountered an error")

        finally:
            self._running = False
            self._unsubscribe()
