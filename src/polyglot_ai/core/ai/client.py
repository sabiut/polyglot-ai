"""OpenAI provider — implements AIProvider for the OpenAI API."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_STREAM_CHUNK,
    EVT_AI_STREAM_DONE,
)
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


class OpenAIClient(AIProvider):
    """OpenAI provider with async streaming."""

    def __init__(self, api_key: str, event_bus: EventBus) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=120)
        self._event_bus = event_bus

    @property
    def name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "OpenAI"

    def update_api_key(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=120)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.models.list()
            chat_prefixes = ("gpt-3.5", "gpt-4", "gpt-5", "o1", "o3", "o4")
            models = [m.id for m in response.data if any(m.id.startswith(p) for p in chat_prefixes)]
            return sorted(models)
        except Exception:
            logger.exception("Failed to list OpenAI models")
            return ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3", "o3-mini", "o4-mini"]

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "gpt-4o",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        try:
            # Prepend system prompt if provided
            all_messages = list(messages)
            if system_prompt:
                all_messages.insert(0, {"role": "system", "content": system_prompt})

            kwargs = {
                "model": model,
                "messages": all_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }

            # Reasoning models (o1, o3, o4) don't support temperature/max_tokens
            reasoning_prefixes = ("o1", "o3", "o4")
            if not any(model.startswith(p) for p in reasoning_prefixes):
                kwargs["temperature"] = temperature
                kwargs["max_tokens"] = max_tokens

            if tools:
                kwargs["tools"] = tools

            stream = await self._client.chat.completions.create(**kwargs)

            async for chunk in stream:
                if not chunk.choices and chunk.usage:
                    yield StreamChunk(
                        usage={
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "total_tokens": chunk.usage.total_tokens,
                        }
                    )
                    continue

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish = chunk.choices[0].finish_reason

                sc = StreamChunk(
                    delta_content=delta.content if delta.content else None,
                    finish_reason=finish,
                )

                if delta.tool_calls:
                    sc.tool_calls = [
                        {
                            "index": tc.index,
                            "id": tc.id,
                            "function": {
                                "name": tc.function.name
                                if tc.function and tc.function.name
                                else None,
                                "arguments": tc.function.arguments if tc.function else "",
                            },
                        }
                        for tc in delta.tool_calls
                    ]

                if sc.delta_content:
                    self._event_bus.emit(EVT_AI_STREAM_CHUNK, content=sc.delta_content)

                yield sc

            self._event_bus.emit(EVT_AI_STREAM_DONE)

        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            error_msg = sanitize_error(str(e))
            logger.exception("OpenAI API error")
            self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
            yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._client.models.list()
            return True, "Connection successful"
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            return False, sanitize_error(str(e))
