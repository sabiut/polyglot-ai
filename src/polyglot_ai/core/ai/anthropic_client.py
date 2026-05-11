"""Anthropic provider — implements AIProvider for the Claude API."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    BadRequestError,
    RateLimitError,
)

from polyglot_ai.constants import EVT_AI_ERROR
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider, ModelListCache
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]


class AnthropicClient(AIProvider):
    """Anthropic (Claude) provider with async streaming."""

    def __init__(self, api_key: str, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self._client = AsyncAnthropic(api_key=api_key)
        self._model_cache = ModelListCache(DEFAULT_MODELS, "Anthropic")

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic"

    def update_api_key(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def list_models(self) -> list[str]:
        async def _fetch() -> list[str]:
            response = await self._client.models.list(limit=100)
            return [m.id for m in response.data if m.id.startswith("claude")]

        return await self._model_cache.get(_fetch)

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

            # Anthropic numbers ALL content blocks (text + tool_use),
            # but we only care about tool_use blocks for indexing. We
            # key by the tool_use **id** (canonical, stable) and keep
            # the block-index map only as a lookup hint for delta
            # events, which carry ``index`` but not ``id``. Duplicate
            # ``content_block_start`` events for the same id reuse
            # the existing tidx instead of re-assigning, which would
            # otherwise concatenate two tools' partial JSON together.
            block_to_tool_idx: dict[int, int] = {}
            id_to_tool_idx: dict[str, int] = {}
            next_tool_idx = 0

            # Same temperature-deprecation handling as the OAuth path —
            # newer Claude models (Sonnet 4.5+, Opus 4.7+) reject the
            # parameter outright with a 400 raised on ``__aenter__``.
            # Strip and retry once when that happens; older models
            # still accept it, so we send it on the first attempt.
            from polyglot_ai.core.ai.claude_oauth import (
                _is_temperature_deprecated_error,
            )

            stream_cm = self._client.messages.stream(**kwargs)
            try:
                stream = await stream_cm.__aenter__()
            except BadRequestError as e:
                from polyglot_ai.core.security import sanitize_error

                if (
                    _is_temperature_deprecated_error(sanitize_error(str(e)))
                    and "temperature" in kwargs
                ):
                    logger.info("Model rejected temperature parameter; retrying without it")
                    kwargs.pop("temperature", None)
                    stream_cm = self._client.messages.stream(**kwargs)
                    stream = await stream_cm.__aenter__()
                else:
                    raise

            try:
                async for event in stream:
                    if not hasattr(event, "type"):
                        continue

                    # Skip parsed helper events (TextEvent, InputJsonEvent, etc.)
                    # Only process raw events that have 'index' for block tracking
                    event_type = event.type

                    if event_type == "content_block_start" and hasattr(event, "index"):
                        if hasattr(event.content_block, "type"):
                            if event.content_block.type == "tool_use":
                                tool_use_id = event.content_block.id
                                if tool_use_id in id_to_tool_idx:
                                    # Duplicate start for the same tool — reuse
                                    # the existing index and refresh the block
                                    # mapping, but don't emit a second start.
                                    tidx = id_to_tool_idx[tool_use_id]
                                    block_to_tool_idx[event.index] = tidx
                                else:
                                    tidx = next_tool_idx
                                    id_to_tool_idx[tool_use_id] = tidx
                                    block_to_tool_idx[event.index] = tidx
                                    next_tool_idx += 1
                                    yield self._tool_call_start_chunk(
                                        tidx,
                                        tool_use_id,
                                        event.content_block.name,
                                    )

                    elif event_type == "content_block_delta" and hasattr(event, "index"):
                        delta = event.delta
                        if hasattr(delta, "text"):
                            yield self._emit_text_delta(delta.text)
                        elif hasattr(delta, "partial_json"):
                            # Skip orphan deltas (no matching start) rather
                            # than misroute their JSON onto tool 0 — the
                            # old default would corrupt that tool's args.
                            tidx = block_to_tool_idx.get(event.index)
                            if tidx is None:
                                logger.warning(
                                    "Anthropic stream: dropping partial_json for unknown block index %d",
                                    event.index,
                                )
                                continue
                            yield self._tool_call_args_chunk(tidx, delta.partial_json)

                    elif event_type == "message_delta":
                        # Emit finish_reason only — usage is intentionally omitted here.
                        # get_final_message() below emits a complete chunk with both
                        # prompt_tokens and completion_tokens. Emitting partial usage
                        # here (prompt_tokens=0) caused double-counting of completion
                        # tokens in any consumer that sums usage across chunks.
                        reason = event.delta.stop_reason
                        if reason == "tool_use":
                            reason = "tool_calls"
                        yield StreamChunk(finish_reason=reason)

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
            finally:
                await stream_cm.__aexit__(None, None, None)

            self._emit_stream_done()

        except (APITimeoutError, APIConnectionError) as e:
            # Transient network blip — DNS, TLS handshake, edge
            # outage, captive portal. Don't dump a 30-line stack
            # trace into the user's chat for something that almost
            # always resolves on retry.
            from polyglot_ai.core.security import sanitize_error

            logger.info("Anthropic transient network issue: %s", sanitize_error(str(e))[:200])
            self._event_bus.emit(EVT_AI_ERROR, error=sanitize_error(str(e)))
            yield StreamChunk(
                delta_content=(
                    "\n\n**Couldn't reach Anthropic just now.**\n\n"
                    "The connection to ``api.anthropic.com`` timed out or "
                    "was refused. Almost always transient — try the prompt "
                    "again. If it keeps failing, check your network or "
                    "switch to a different provider in the model dropdown."
                )
            )
            return
        except RateLimitError as e:
            # Show a friendly explanation instead of the raw 429
            # JSON dump. API-key quotas are per-organization and
            # reset on a sliding window — Retry-After tells us how
            # long, when present.
            from polyglot_ai.core.ai.claude_oauth import _retry_after_hint
            from polyglot_ai.core.security import sanitize_error

            logger.warning("Anthropic rate limit hit: %s", sanitize_error(str(e))[:200])
            self._event_bus.emit(EVT_AI_ERROR, error=sanitize_error(str(e)))
            wait_hint = _retry_after_hint(e)
            yield StreamChunk(
                delta_content=(
                    "\n\n**Anthropic rate limit reached.**\n\n"
                    f"Your API key has hit its per-minute quota{wait_hint}. "
                    "Anthropic enforces these per organization; bursts add up "
                    "across every app on the same key.\n\n"
                    "**What to do:**\n\n"
                    "1. Wait a minute and retry the same prompt.\n"
                    "2. Switch to a different provider in the model dropdown "
                    "for now.\n"
                    "3. If you hit this often, your tier may need a bump — "
                    "see https://console.anthropic.com/settings/limits."
                )
            )
            return
        except Exception as e:
            yield self._handle_stream_error(e)

    async def test_connection(self) -> tuple[bool, str]:
        return await self._test_connection_via_list(lambda: self._client.models.list(limit=1))
