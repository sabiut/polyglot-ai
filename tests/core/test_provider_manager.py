"""Tests for ``ProviderManager.get_all_models`` parallel gather + failure isolation.

The whole point of the parallel refactor is that one broken provider
cannot block or poison the dropdown. These tests pin that contract.
They also cover registration ordering, the happy empty-registry path,
and ``get_provider_for_model`` prefix-resolution precedence (which is
billing-sensitive: picking ``openai_oauth`` over ``openai`` matters).
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.ai.provider_manager import ProviderManager
from polyglot_ai.core.bridge import EventBus


class _FakeProvider(AIProvider):
    """Minimal AIProvider that returns a canned model list.

    Implements every abstract method so the class is instantiable; only
    ``list_models`` is exercised. ``delay`` lets tests assert that
    ``get_all_models`` actually runs list_models concurrently.
    """

    def __init__(
        self,
        name: str,
        models: list[str],
        *,
        delay: float = 0.0,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__(EventBus())
        self._name = name
        self._models = models
        self._delay = delay
        self._raises = raises

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.capitalize()

    async def list_models(self) -> list[str]:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return list(self._models)

    async def stream_chat(self, *args, **kwargs) -> AsyncGenerator:  # pragma: no cover
        if False:
            yield None

    async def test_connection(self) -> tuple[bool, str]:  # pragma: no cover
        return True, "ok"

    def update_api_key(self, api_key: str) -> None:  # pragma: no cover
        pass


# ── get_all_models ──────────────────────────────────────────────────


async def test_get_all_models_empty_registry():
    pm = ProviderManager()
    assert await pm.get_all_models() == []


async def test_get_all_models_returns_entries_in_registration_order():
    pm = ProviderManager()
    pm.register(_FakeProvider("alpha", ["a1", "a2"]))
    pm.register(_FakeProvider("beta", ["b1"]))

    entries = await pm.get_all_models()
    assert [(e.provider_name, e.model_id) for e in entries] == [
        ("alpha", "a1"),
        ("alpha", "a2"),
        ("beta", "b1"),
    ]


async def test_get_all_models_isolates_failing_provider():
    """A provider that raises must not prevent the others from returning.

    This is the core regression contract of the rewrite — a future
    refactor that drops ``return_exceptions=True`` from the gather
    call would flip this test red.
    """
    pm = ProviderManager()
    pm.register(_FakeProvider("good", ["g1", "g2"]))
    pm.register(_FakeProvider("broken", [], raises=RuntimeError("boom")))
    pm.register(_FakeProvider("also_good", ["ok1"]))

    entries = await pm.get_all_models()
    pairs = [(e.provider_name, e.model_id) for e in entries]
    assert pairs == [("good", "g1"), ("good", "g2"), ("also_good", "ok1")]


async def test_get_all_models_all_providers_fail_returns_empty_list():
    pm = ProviderManager()
    pm.register(_FakeProvider("a", [], raises=RuntimeError("nope")))
    pm.register(_FakeProvider("b", [], raises=ValueError("nope")))

    entries = await pm.get_all_models()
    assert entries == []


async def test_get_all_models_runs_concurrently():
    """Two 50ms fetches should finish in well under 100ms if parallel.

    Uses a comfortable margin so this isn't flaky on slow CI.
    """
    pm = ProviderManager()
    pm.register(_FakeProvider("slow1", ["m1"], delay=0.05))
    pm.register(_FakeProvider("slow2", ["m2"], delay=0.05))

    loop = asyncio.get_event_loop()
    start = loop.time()
    entries = await pm.get_all_models()
    elapsed = loop.time() - start

    assert {e.model_id for e in entries} == {"m1", "m2"}
    assert elapsed < 0.09, f"parallel gather should take ~0.05s, took {elapsed:.3f}s"


# ── get_provider_for_model — billing-sensitive precedence ──────────


def test_get_provider_for_model_explicit_canonical():
    pm = ProviderManager()
    pm.register(_FakeProvider("openai", []))
    result = pm.get_provider_for_model("openai:gpt-4")
    assert result is not None
    provider, model_id = result
    assert provider.name == "openai"
    assert model_id == "gpt-4"


def test_get_provider_for_model_oauth_wins_over_apikey_for_gpt():
    """If both openai_oauth and openai are registered, OAuth wins.

    This is the subscription-vs-API-key routing rule: we prefer not
    to burn API credits when the user has a subscription session.
    """
    pm = ProviderManager()
    pm.register(_FakeProvider("openai", []))
    pm.register(_FakeProvider("openai_oauth", []))
    result = pm.get_provider_for_model("gpt-4o")
    assert result is not None
    provider, _ = result
    assert provider.name == "openai_oauth"


def test_get_provider_for_model_oauth_wins_over_apikey_for_claude():
    pm = ProviderManager()
    pm.register(_FakeProvider("anthropic", []))
    pm.register(_FakeProvider("claude_oauth", []))
    result = pm.get_provider_for_model("claude-3-5-sonnet")
    assert result is not None
    provider, _ = result
    assert provider.name == "claude_oauth"


def test_get_provider_for_model_unknown_returns_none():
    pm = ProviderManager()
    pm.register(_FakeProvider("openai", []))
    assert pm.get_provider_for_model("totally-unknown-model") is None


def test_get_provider_for_model_canonical_split_falls_back_to_prefix_map():
    """``openai:gpt-4o`` with no ``openai`` registered falls back cleanly.

    After the canonical split, the fallback runs against the stripped
    model id (``"gpt-4o"``), so it matches the gpt- prefix and
    resolves to whichever openai variant is registered — in this case
    ``openai_oauth``. Pins the resolution precedence.
    """
    pm = ProviderManager()
    pm.register(_FakeProvider("openai_oauth", []))
    result = pm.get_provider_for_model("openai:gpt-4o")
    assert result is not None
    provider, model_id = result
    assert provider.name == "openai_oauth"
    assert model_id == "gpt-4o"
