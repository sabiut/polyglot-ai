"""OpenAI-compatible provider — implements AIProvider for OpenAI and xAI APIs."""

from __future__ import annotations

import logging
import time
from typing import AsyncGenerator

from openai import AsyncOpenAI

from polyglot_ai.constants import EVT_AI_STREAM_CHUNK
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

_MODEL_CACHE_TTL = 300  # seconds — cache model list for 5 minutes


class OpenAIClient(AIProvider):
    """OpenAI-compatible provider with async streaming.

    Also used for xAI (Grok) by passing a different base_url.
    """

    def __init__(
        self,
        api_key: str,
        event_bus: EventBus,
        *,
        base_url: str | None = None,
        provider_name: str = "openai",
        provider_display_name: str = "OpenAI",
        default_models: list[str] | None = None,
        model_filter: tuple[str, ...] | None = None,
        enable_stream_options: bool = True,
        reasoning_prefixes: tuple[str, ...] = ("o1", "o3", "o4"),
    ) -> None:
        super().__init__(event_bus)
        self._base_url = base_url
        self._provider_name = provider_name
        self._provider_display_name = provider_display_name
        self._default_models = default_models or [
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "o3",
            "o3-mini",
            "o4-mini",
        ]
        self._model_filter = model_filter or ("gpt-3.5", "gpt-4", "gpt-5", "o1", "o3", "o4")
        self._enable_stream_options = enable_stream_options
        self._reasoning_prefixes = reasoning_prefixes
        self._client = self._make_client(api_key)
        self._cached_models: list[str] | None = None
        self._models_cached_at: float = 0.0

    def _make_client(self, api_key: str) -> AsyncOpenAI:
        kwargs = {"api_key": api_key, "timeout": 120}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncOpenAI(**kwargs)

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return self._provider_display_name

    def update_api_key(self, api_key: str) -> None:
        self._client = self._make_client(api_key)

    async def list_models(self) -> list[str]:
        now = time.time()
        if self._cached_models and (now - self._models_cached_at) < _MODEL_CACHE_TTL:
            return list(self._cached_models)
        try:
            response = await self._client.models.list()
            models = [
                m.id for m in response.data if any(m.id.startswith(p) for p in self._model_filter)
            ]
            result = sorted(models) if models else list(self._default_models)
            self._cached_models = result
            self._models_cached_at = now
            return list(result)
        except Exception:
            logger.exception("Failed to list %s models", self._provider_display_name)
            return list(self._default_models)

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
            all_messages = list(messages)
            if system_prompt:
                all_messages.insert(0, {"role": "system", "content": system_prompt})

            kwargs = {
                "model": model,
                "messages": all_messages,
                "stream": True,
            }

            if self._enable_stream_options:
                kwargs["stream_options"] = {"include_usage": True}

            # Reasoning models don't support temperature/max_tokens
            if not any(model.startswith(p) for p in self._reasoning_prefixes):
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

            self._emit_stream_done()

        except Exception as e:
            yield self._handle_stream_error(e)

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._client.models.list()
            return True, "Connection successful"
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            return False, sanitize_error(str(e))
