"""OpenAI-compatible provider — implements AIProvider for OpenAI and DeepSeek APIs."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from polyglot_ai.constants import EVT_AI_ERROR, EVT_AI_STREAM_CHUNK
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider, ModelListCache
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


class OpenAIClient(AIProvider):
    """OpenAI-compatible provider with async streaming.

    Also used for DeepSeek by passing a different base_url.
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
        reasoning_prefixes: tuple[str, ...] = ("o1", "o4"),
    ) -> None:
        super().__init__(event_bus)
        self._base_url = base_url
        self._provider_name = provider_name
        self._provider_display_name = provider_display_name
        self._default_models = default_models or [
            "gpt-5.5",
            "gpt-5.4",
            "o4-mini",
        ]
        # ``model_filter`` controls which IDs returned by the API end up
        # in the dropdown. Kept broad enough that o-series reasoning
        # models still show even though we only ship o4-mini in the
        # defaults — users with access to ``o5`` etc. will still see them.
        self._model_filter = model_filter or ("gpt-3.5", "gpt-4", "gpt-5", "o1", "o4")
        self._enable_stream_options = enable_stream_options
        self._reasoning_prefixes = reasoning_prefixes
        self._client = self._make_client(api_key)
        self._model_cache = ModelListCache(self._default_models, provider_display_name)

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
        async def _fetch() -> list[str]:
            response = await self._client.models.list()
            return [
                m.id for m in response.data if any(m.id.startswith(p) for p in self._model_filter)
            ]

        return await self._model_cache.get(_fetch)

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "gpt-5.5",
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

            # We close the stream explicitly in a finally block (rather
            # than relying on garbage collection) so the underlying
            # httpx response is released inside *this* event loop
            # iteration. Without that, an early break or upstream
            # cancellation leaves the ``AsyncStream`` and its httpx
            # connection dangling. The GC eventually finalises them
            # outside any async context, and httpcore's sniffio probe
            # fails with:
            #   "AsyncLibraryNotFoundError: unknown async library, or
            #    not in async context"
            # That traceback is shown as "Exception ignored in:" — not
            # a crash, but it hides real diagnostics.
            stream = await self._client.chat.completions.create(**kwargs)

            # Diagnostic: total chars of reasoning_content captured in
            # this stream. Logged at INFO when non-zero so we can tell
            # from the log whether thinking-mode capture is working
            # without sprinkling per-chunk DEBUG lines.
            reasoning_total = 0
            try:
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

                    # Some OpenAI-compatible providers (DeepSeek's
                    # thinking-mode models, primarily) extend the chunk
                    # delta with a ``reasoning_content`` field carrying
                    # the model's chain-of-thought. The OpenAI SDK lets
                    # extras through via Pydantic's ``model_extra``, but
                    # also surfaces them as plain attributes on newer SDK
                    # versions, so ``getattr`` covers both cases.
                    # Falsy/missing → None (the default), no field emitted.
                    reasoning_delta = getattr(delta, "reasoning_content", None)
                    if not reasoning_delta and getattr(delta, "model_extra", None):
                        reasoning_delta = delta.model_extra.get("reasoning_content")
                    if reasoning_delta:
                        reasoning_total += len(reasoning_delta)

                    sc = StreamChunk(
                        delta_content=delta.content if delta.content else None,
                        delta_reasoning=reasoning_delta if reasoning_delta else None,
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
            finally:
                # Explicit close — see the comment above the
                # ``stream = await ...`` call. ``close()`` is idempotent
                # on the OpenAI SDK's AsyncStream, so it's safe to run
                # both on natural-end-of-iteration and on early break /
                # exception.
                close = getattr(stream, "close", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:
                        logger.debug("OpenAI stream close failed", exc_info=True)
                if reasoning_total:
                    logger.info(
                        "%s: captured %d chars of reasoning_content (model=%s)",
                        self._provider_display_name,
                        reasoning_total,
                        model,
                    )

            self._emit_stream_done()

        except Exception as e:
            # Detect DeepSeek's thinking-mode round-trip rejection
            # specifically and replace the raw 400 JSON with a clear,
            # actionable message. This fires when the conversation
            # history contains an assistant turn from a thinking-mode
            # model where ``reasoning_content`` wasn't captured (most
            # commonly: pre-fix conversations from before schema v5).
            err_text = str(e)
            if "reasoning_content" in err_text and "thinking mode" in err_text:
                logger.warning(
                    "%s: conversation history is missing reasoning_content "
                    "from a prior thinking-mode turn — likely a pre-fix "
                    "conversation. The user must start a new conversation "
                    "to continue with this model.",
                    self._provider_display_name,
                )
                friendly = (
                    "\n\n**This conversation can't continue on DeepSeek.**\n\n"
                    "It contains an earlier reply from a thinking-mode model "
                    "(DeepSeek's reasoner / V4-pro) that didn't store its "
                    "internal reasoning. DeepSeek now requires that data on "
                    "every follow-up turn — and the requirement applies to "
                    "the whole history, so switching to `deepseek-v4-flash` "
                    "in the dropdown won't help either (the old turn is "
                    "still in the transcript).\n\n"
                    "**To continue, pick one:**\n\n"
                    "- Click **+ New** in the sidebar to start a fresh "
                    "conversation on DeepSeek (new turns will round-trip "
                    "correctly), **or**\n"
                    "- Switch the model dropdown to a non-DeepSeek provider "
                    "(e.g. `gpt-5.5` or `claude-opus-4-7`) and retry — those "
                    "providers ignore the missing field."
                )
                self._event_bus.emit(
                    EVT_AI_ERROR,
                    error="conversation incompatible with thinking-mode model",
                )
                yield StreamChunk(delta_content=friendly)
                return

            # Network errors during a streaming request leak out
            # as raw ``httpx`` exceptions: the OpenAI SDK wraps
            # request-time failures into ``APIConnectionError`` /
            # ``APITimeoutError``, but once the stream is open,
            # mid-stream drops bubble up unwrapped. We saw this
            # with DeepSeek (200 OK followed by a mid-stream
            # ``httpx.ReadError`` with a 30-line stack trace in
            # the log). Catch both the request-side OpenAI wraps
            # and the response-side raw httpx errors here so the
            # user gets a friendly "try again" message; real bugs
            # (auth failures, protocol errors, etc.) still fall
            # through to ``_handle_stream_error``.
            #
            # Imports are module-level deps (``openai`` is the
            # whole point of this file; ``httpx`` is its transport)
            # so we import them inline here without a guard — both
            # are required by ``pyproject.toml`` and any import
            # failure would have killed the module load far above.
            import httpx
            from openai import APIConnectionError, APITimeoutError, RateLimitError

            network_errors: tuple[type, ...] = (
                APIConnectionError,
                APITimeoutError,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
            )

            if isinstance(e, network_errors):
                # Sanitised summary, not the raw exception text —
                # ``str(e)`` for httpx errors can include request
                # URLs, header values, and internal paths that
                # don't belong in the UI's event bus. The friendly
                # markdown body below is what the user actually
                # sees; the log line keeps the type name + a
                # trimmed-and-sanitised message for diagnostics.
                from polyglot_ai.core.security import sanitize_error

                sanitised = sanitize_error(err_text)[:200]
                # Logged at WARNING (not ERROR) because transient
                # network blips are expected and resolve on
                # retry. ERROR-level entries imply "user must
                # act" which isn't the case here.
                logger.warning(
                    "%s: streaming connection dropped (%s) — %s",
                    self._provider_display_name,
                    type(e).__name__,
                    sanitised,
                )
                self._event_bus.emit(
                    EVT_AI_ERROR,
                    error=f"{self._provider_display_name} connection dropped",
                )
                yield StreamChunk(
                    delta_content=(
                        f"\n\n**Couldn't finish reading the response from "
                        f"{self._provider_display_name}.**\n\n"
                        "The streaming connection dropped before the model "
                        "finished. Any text above this line is what made it "
                        "through before the drop; the rest didn't arrive. "
                        "Usually a transient blip — the provider timed out, "
                        "the network hiccupped, or their edge had a bad "
                        "second.\n\n"
                        "**Try:**\n\n"
                        "1. Send the prompt again — your previous turn is "
                        "preserved in the conversation.\n"
                        "2. If it keeps failing, switch to a different "
                        "provider in the model dropdown."
                    )
                )
                return

            # Rate limit — same pattern, separate friendly message.
            # OpenAI / DeepSeek surface this as ``RateLimitError``
            # at request time (it's an HTTP 429 with a JSON body,
            # so the SDK gets to wrap it before the stream opens).
            if isinstance(e, RateLimitError):
                from polyglot_ai.core.security import sanitize_error

                sanitised = sanitize_error(err_text)[:200]
                logger.warning(
                    "%s: rate limit hit — %s",
                    self._provider_display_name,
                    sanitised,
                )
                self._event_bus.emit(
                    EVT_AI_ERROR,
                    error=f"{self._provider_display_name} rate limit reached",
                )
                yield StreamChunk(
                    delta_content=(
                        f"\n\n**{self._provider_display_name} rate limit "
                        "reached.**\n\n"
                        "Wait a minute and try again, or switch to a "
                        "different provider in the model dropdown for now. "
                        "If you hit this often, the provider's pricing page "
                        "covers tier upgrades."
                    )
                )
                return

            yield self._handle_stream_error(e)

    async def test_connection(self) -> tuple[bool, str]:
        return await self._test_connection_via_list(self._client.models.list)
