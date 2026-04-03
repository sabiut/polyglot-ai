"""Base AI provider interface — all providers implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from polyglot_ai.core.ai.models import StreamChunk


class AIProvider(ABC):
    """Abstract base for AI providers (OpenAI, Anthropic, Google, etc.)."""

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
