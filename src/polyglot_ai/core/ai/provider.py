"""Base AI provider interface — all providers implement this."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Awaitable, Callable

from polyglot_ai.constants import EVT_AI_ERROR, EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

_MODEL_CACHE_TTL = 300  # seconds — cache model list for 5 minutes


class ModelListCache:
    """TTL cache for ``list_models`` results with fallback to defaults.

    All three providers had copy-pasted the same cache-and-fallback
    logic around their SDK's list-models call. That pattern lives here
    now so a behaviour change (e.g. cache TTL, logging format) only has
    to happen in one place. The per-provider differences — filtering,
    sorting, id normalisation — stay in the caller's ``fetcher``.
    """

    def __init__(self, defaults: list[str], display_name: str) -> None:
        self._defaults = list(defaults)
        self._display_name = display_name
        self._cached: list[str] | None = None
        self._cached_at: float = 0.0

    async def get(self, fetcher: Callable[[], Awaitable[list[str]]]) -> list[str]:
        """Return cached models, or fetch fresh ones via ``fetcher``.

        On any exception the defaults are returned — same behaviour as
        the original per-provider code. The fetcher is responsible for
        filtering to provider-relevant IDs; this helper only adds
        sorting and fallback.
        """
        now = time.time()
        if self._cached and (now - self._cached_at) < _MODEL_CACHE_TTL:
            return list(self._cached)
        try:
            fetched = await fetcher()
            result = sorted(fetched) if fetched else list(self._defaults)
            self._cached = result
            self._cached_at = now
            return list(result)
        except Exception:
            logger.exception("Failed to list %s models", self._display_name)
            return list(self._defaults)


class AIProvider(ABC):
    """Abstract base for AI providers (OpenAI, Anthropic, Google, etc.)."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. 'openai', 'anthropic', 'google'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'OpenAI', 'Anthropic', 'Google'."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Return available model IDs for this provider."""

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a chat completion, yielding StreamChunk objects."""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """Test the API connection. Returns (success, message)."""

    @abstractmethod
    def update_api_key(self, api_key: str) -> None:
        """Update the API key for this provider."""

    # ── Shared helpers for subclasses ───────────────────────────────

    def _handle_stream_error(self, exc: Exception) -> StreamChunk:
        """Log an error, emit EVT_AI_ERROR, and return an error StreamChunk."""
        from polyglot_ai.core.security import sanitize_error

        error_msg = sanitize_error(str(exc))
        logger.exception("%s API error", self.display_name)
        self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
        return StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")

    def _emit_stream_done(self) -> None:
        """Emit EVT_AI_STREAM_DONE."""
        self._event_bus.emit(EVT_AI_STREAM_DONE)

    async def _test_connection_via_list(
        self, list_fn: Callable[[], Awaitable[object]]
    ) -> tuple[bool, str]:
        """Standard connection test: call ``list_fn`` and report result.

        Used by every provider — list models (or anything cheap) and
        translate success/failure into the ``(ok, message)`` tuple the
        UI expects. Error messages are routed through ``sanitize_error``
        so API keys or endpoints can't leak into user-visible strings.
        """
        try:
            await list_fn()
            return True, "Connection successful"
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            return False, sanitize_error(str(e))

    def _emit_text_delta(self, text: str) -> StreamChunk:
        """Build a text-delta chunk and emit ``EVT_AI_STREAM_CHUNK``.

        Every provider needs to do both together — the UI subscribes to
        the event for incremental rendering, and the loop consumes the
        yielded chunk for accumulation. Doing this in one place
        prevents the "provider forgot to emit" bug class.
        """
        self._event_bus.emit(EVT_AI_STREAM_CHUNK, content=text)
        return StreamChunk(delta_content=text)

    @staticmethod
    def _tool_call_start_chunk(index: int, call_id: str, name: str) -> StreamChunk:
        """Canonical shape for the opening chunk of a tool call.

        ``id`` and ``name`` are set here; subsequent argument fragments
        use :meth:`_tool_call_args_chunk` with ``id=None``/``name=None``.
        Centralising the shape prevents the concat-bug class where a
        provider accidentally re-emits the id on later chunks and
        downstream reassembly double-counts it.
        """
        return StreamChunk(
            tool_calls=[
                {
                    "index": index,
                    "id": call_id,
                    "function": {"name": name, "arguments": ""},
                }
            ]
        )

    @staticmethod
    def _tool_call_args_chunk(index: int, args_fragment: str) -> StreamChunk:
        """Canonical shape for a tool-call argument continuation chunk."""
        return StreamChunk(
            tool_calls=[
                {
                    "index": index,
                    "id": None,
                    "function": {"name": None, "arguments": args_fragment},
                }
            ]
        )
