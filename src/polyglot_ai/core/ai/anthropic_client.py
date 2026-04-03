"""Anthropic provider — implements AIProvider for the Claude API."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_STREAM_CHUNK,
    EVT_AI_STREAM_DONE,
)
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-0",
]


class AnthropicClient(AIProvider):
    """Anthropic (Claude) provider with async streaming."""

    def __init__(self, api_key: str, event_bus: EventBus) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._event_bus = event_bus

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic"

    def update_api_key(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.models.list(limit=100)
            models = [m.id for m in response.data if m.id.startswith("claude")]
            return sorted(models) if models else DEFAULT_MODELS
        except Exception:
            logger.exception("Failed to list Anthropic models")
            return list(DEFAULT_MODELS)

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "claude-sonnet-4-20250514",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        try:
            # Convert OpenAI-format messages to Anthropic format
            anthropic_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                if role == "system":
                    if not system_prompt:
                        system_prompt = msg.get("content", "")
                    continue

                if role == "assistant":
                    # Build content blocks: text + tool_use
                    content_blocks = []
                    text = msg.get("content")
                    if text:
                        content_blocks.append({"type": "text", "text": text})
                    # Convert OpenAI tool_calls to Anthropic tool_use blocks
                    for tc in msg.get("tool_calls", []):
                        import json as _json

                        fn = tc.get("function", {})
                        args_str = fn.get("arguments", "{}")
                        try:
                            args = _json.loads(args_str) if args_str else {}
                        except _json.JSONDecodeError:
                            args = {}
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": fn.get("name", ""),
                                "input": args,
                            }
                        )
                    anthropic_messages.append(
                        {
                            "role": "assistant",
                            "content": content_blocks if content_blocks else (text or ""),
                        }
                    )

                elif role == "tool":
                    # Tool results → Anthropic uses role "user" with tool_result content
                    tool_call_id = msg.get("tool_call_id", "")
                    result_content = msg.get("content", "")
                    # Anthropic expects tool results in a user message
                    # Merge consecutive tool results into one user message
                    if (
                        anthropic_messages
                        and anthropic_messages[-1]["role"] == "user"
                        and isinstance(anthropic_messages[-1]["content"], list)
                        and any(
                            b.get("type") == "tool_result"
                            for b in anthropic_messages[-1]["content"]
                        )
                    ):
                        anthropic_messages[-1]["content"].append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": result_content,
                            }
                        )
                    else:
                        anthropic_messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tool_call_id,
                                        "content": result_content,
                                    }
                                ],
                            }
                        )

                elif role == "user":
                    anthropic_messages.append(
                        {
                            "role": role,
                            "content": msg.get("content", ""),
                        }
                    )

            # Ensure messages alternate user/assistant
            if not anthropic_messages:
                anthropic_messages = [{"role": "user", "content": "Hello"}]

            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            if temperature is not None:
                kwargs["temperature"] = temperature

            # Convert OpenAI-style tools to Anthropic format
            if tools:
                anthropic_tools = []
                for tool in tools:
                    func = tool.get("function", {})
                    anthropic_tools.append(
                        {
                            "name": func.get("name", ""),
                            "description": func.get("description", ""),
                            "input_schema": func.get("parameters", {}),
                        }
                    )
                kwargs["tools"] = anthropic_tools

            # Map Anthropic content block index → our tool call index
            # Anthropic numbers ALL content blocks (text + tool_use),
            # but we only care about tool_use blocks for indexing.
            block_to_tool_idx: dict[int, int] = {}
            next_tool_idx = 0

            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if not hasattr(event, "type"):
                        continue

                    # Skip parsed helper events (TextEvent, InputJsonEvent, etc.)
                    # Only process raw events that have 'index' for block tracking
                    event_type = event.type

                    if event_type == "content_block_start" and hasattr(event, "index"):
                        if hasattr(event.content_block, "type"):
                            if event.content_block.type == "tool_use":
                                tidx = next_tool_idx
                                block_to_tool_idx[event.index] = tidx
                                next_tool_idx += 1
                                yield StreamChunk(
                                    tool_calls=[
                                        {
                                            "index": tidx,
                                            "id": event.content_block.id,
                                            "function": {
                                                "name": event.content_block.name,
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                )

                    elif event_type == "content_block_delta" and hasattr(event, "index"):
                        delta = event.delta
                        if hasattr(delta, "text"):
                            self._event_bus.emit(EVT_AI_STREAM_CHUNK, content=delta.text)
                            yield StreamChunk(delta_content=delta.text)
                        elif hasattr(delta, "partial_json"):
                            tidx = block_to_tool_idx.get(event.index, 0)
                            yield StreamChunk(
                                tool_calls=[
                                    {
                                        "index": tidx,
                                        "id": None,
                                        "function": {
                                            "name": None,
                                            "arguments": delta.partial_json,
                                        },
                                    }
                                ]
                            )

                    elif event_type == "message_delta":
                        if hasattr(event, "usage") and event.usage:
                            reason = event.delta.stop_reason
                            if reason == "tool_use":
                                reason = "tool_calls"
                            yield StreamChunk(
                                finish_reason=reason,
                                usage={
                                    "prompt_tokens": 0,
                                    "completion_tokens": event.usage.output_tokens,
                                    "total_tokens": event.usage.output_tokens,
                                },
                            )

                # Get final usage while stream is still open
                final_message = await stream.get_final_message()
                if final_message and final_message.usage:
                    yield StreamChunk(
                        usage={
                            "prompt_tokens": final_message.usage.input_tokens,
                            "completion_tokens": final_message.usage.output_tokens,
                            "total_tokens": (
                                final_message.usage.input_tokens + final_message.usage.output_tokens
                            ),
                        }
                    )

            self._event_bus.emit(EVT_AI_STREAM_DONE)

        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            error_msg = sanitize_error(str(e))
            logger.exception("Anthropic API error")
            self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
            yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._client.models.list(limit=1)
            return True, "Connection successful"
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            return False, sanitize_error(str(e))
