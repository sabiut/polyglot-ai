"""Base AI provider interface — all providers implement this."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from polyglot_ai.constants import EVT_AI_ERROR, EVT_AI_STREAM_DONE
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


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

    def _test_connection_via_models(self, client) -> tuple[bool, str]:
        """Common test_connection pattern shared by OpenAI-compatible providers."""
        # This is a sync helper; callers should await the actual models.list()
        raise NotImplementedError("Use in async context")
