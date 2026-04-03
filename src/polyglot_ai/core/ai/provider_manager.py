"""Provider manager — registry of all AI providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from polyglot_ai.core.ai.provider import AIProvider

logger = logging.getLogger(__name__)


@dataclass
class ModelEntry:
    """A model in the combined dropdown."""

    provider_name: str
    provider_display: str
    model_id: str

    @property
    def display_text(self) -> str:
        return f"{self.model_id}"

    @property
    def full_id(self) -> str:
        """Unique identifier: provider:model"""
        return f"{self.provider_name}:{self.model_id}"


class ProviderManager:
    """Manages multiple AI providers and their models."""

    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider) -> None:
        self._providers[provider.name] = provider
        logger.info("Registered AI provider: %s", provider.display_name)

    def unregister(self, name: str) -> None:
        if name in self._providers:
            logger.info("Unregistered AI provider: %s", name)
            del self._providers[name]

    def get_provider(self, name: str) -> AIProvider | None:
        return self._providers.get(name)

    def get_all_providers(self) -> list[AIProvider]:
        return list(self._providers.values())

    def get_provider_for_model(self, full_id: str) -> tuple[AIProvider, str] | None:
        """Given 'provider:model_id', return (provider, model_id).

        The canonical format is 'provider_name:model_id'.
        Fallback guessing is only for legacy compatibility.
        """
        model_id = full_id
        if ":" in full_id:
            provider_name, model_id = full_id.split(":", 1)
            provider = self._providers.get(provider_name)
            if provider:
                return provider, model_id

        # Fallback: guess provider from model prefix
        # Use explicit priority order to avoid ambiguity
        prefix_map = [
            # Prefer subscription provider over API key provider
            ("openai_oauth", ("gpt-", "o1", "o3", "o4")),
            ("openai", ("gpt-", "o1", "o3", "o4")),
            ("claude_oauth", ("claude",)),  # Subscription preferred over API key
            ("anthropic", ("claude",)),
            ("google", ("gemini",)),
            ("xai", ("grok",)),
        ]
        for pname, prefixes in prefix_map:
            if any(model_id.startswith(p) for p in prefixes):
                provider = self._providers.get(pname)
                if provider:
                    return provider, model_id
        return None

    async def get_all_models(self) -> list[ModelEntry]:
        """Fetch models from all registered providers."""
        entries: list[ModelEntry] = []
        for provider in self._providers.values():
            try:
                models = await provider.list_models()
                for model_id in models:
                    entries.append(
                        ModelEntry(
                            provider_name=provider.name,
                            provider_display=provider.display_name,
                            model_id=model_id,
                        )
                    )
            except Exception:
                logger.exception("Failed to fetch models from %s", provider.display_name)
        return entries

    @property
    def has_providers(self) -> bool:
        return len(self._providers) > 0
