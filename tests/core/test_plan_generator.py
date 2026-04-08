"""Tests for ``PlanGenerator``.

Cover the parser (clean JSON, fenced JSON, free-text wrapping,
malformed payloads), the success/failure return shapes, and the
provider-resolution edge cases (no providers, model_id miss, empty
model list).
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.ai.provider_manager import ProviderManager
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.plan_generator import PlanGenerator
from polyglot_ai.core.tasks import Task, TaskKind


# ── Fakes ───────────────────────────────────────────────────────────


class _FakeProvider(AIProvider):
    """Minimal AIProvider that returns a canned streamed response."""

    def __init__(
        self,
        name: str,
        models: list[str],
        *,
        response: str = "",
        list_raises: BaseException | None = None,
        stream_raises: BaseException | None = None,
    ) -> None:
        super().__init__(EventBus())
        self._name = name
        self._models = models
        self._response = response
        self._list_raises = list_raises
        self._stream_raises = stream_raises
        self.last_messages: list[dict] | None = None
        self.last_model: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.capitalize()

    async def list_models(self) -> list[str]:
        if self._list_raises is not None:
            raise self._list_raises
        return list(self._models)

    async def stream_chat(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        max_tokens=4096,
        system_prompt=None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self.last_messages = messages
        self.last_model = model
        if self._stream_raises is not None:
            raise self._stream_raises
        # Yield in two chunks to mimic streaming.
        midpoint = len(self._response) // 2
        yield StreamChunk(delta_content=self._response[:midpoint])
        yield StreamChunk(delta_content=self._response[midpoint:])

    async def test_connection(self) -> tuple[bool, str]:  # pragma: no cover
        return True, "ok"

    def update_api_key(self, api_key: str) -> None:  # pragma: no cover
        pass


def _task(title="Add CSV export", description="Export user reports as CSV") -> Task:
    return Task.new("/tmp/proj", TaskKind.FEATURE, title, description)


def _make_pm_with(provider: _FakeProvider) -> ProviderManager:
    pm = ProviderManager()
    pm.register(provider)
    return pm


def _payload(steps: list[str]) -> str:
    return json.dumps({"steps": [{"text": s} for s in steps]})


# ── Parser ──────────────────────────────────────────────────────────


def test_parse_clean_json():
    raw = _payload(["Design", "Implement", "Write tests", "Document"])
    steps = PlanGenerator._parse(raw)
    assert [s.text for s in steps] == ["Design", "Implement", "Write tests", "Document"]


def test_parse_fenced_json():
    raw = (
        "Here you go:\n```json\n"
        + _payload(["Design", "Code", "Test", "Ship"])
        + "\n```\nGood luck!"
    )
    steps = PlanGenerator._parse(raw)
    assert len(steps) == 4
    assert steps[0].text == "Design"


def test_parse_braces_in_prose():
    """Falls back to extracting the first {...} block."""
    raw = "Sure! " + _payload(["Plan", "Build", "Ship"]) + " — let me know!"
    steps = PlanGenerator._parse(raw)
    assert len(steps) == 3


def test_parse_empty_returns_empty():
    assert PlanGenerator._parse("") == []
    assert PlanGenerator._parse("   ") == []


def test_parse_garbage_returns_empty():
    assert PlanGenerator._parse("not json at all") == []


def test_parse_below_minimum_steps_returns_empty():
    """Fewer than 3 steps is treated as a failed parse."""
    raw = _payload(["Only one"])
    assert PlanGenerator._parse(raw) == []
    raw = _payload(["A", "B"])
    assert PlanGenerator._parse(raw) == []


def test_parse_caps_at_max_steps():
    """More than 12 steps are truncated."""
    raw = _payload([f"Step {i}" for i in range(20)])
    steps = PlanGenerator._parse(raw)
    assert len(steps) == 12
    assert steps[0].text == "Step 0"
    assert steps[-1].text == "Step 11"


def test_parse_truncates_long_text():
    long = "x" * 500
    raw = _payload([long, "Short", "Another"])
    steps = PlanGenerator._parse(raw)
    assert len(steps[0].text) <= 240
    assert steps[0].text.endswith("…")


def test_parse_skips_blank_and_non_dict_entries():
    raw = json.dumps(
        {
            "steps": [
                {"text": ""},
                "not a dict",
                {"text": "valid 1"},
                {"text": "valid 2"},
                {"text": "valid 3"},
            ]
        }
    )
    steps = PlanGenerator._parse(raw)
    assert [s.text for s in steps] == ["valid 1", "valid 2", "valid 3"]


def test_parse_wrong_top_level_shape_returns_empty():
    assert PlanGenerator._parse('{"plan": [{"text": "x"}]}') == []
    assert PlanGenerator._parse('{"steps": "not a list"}') == []
    assert PlanGenerator._parse("[]") == []


# ── End-to-end generate() ──────────────────────────────────────────


async def test_generate_happy_path():
    provider = _FakeProvider(
        "openai", ["gpt-4"], response=_payload(["Design", "Implement", "Test", "Document"])
    )
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task())

    assert result.ok
    assert len(result.steps) == 4
    assert result.provider == "openai"
    assert result.model == "gpt-4"
    assert result.error == ""


async def test_generate_includes_task_context_in_prompt():
    provider = _FakeProvider(
        "openai",
        ["gpt-4"],
        response=_payload(["A", "B", "C", "D"]),
    )
    gen = PlanGenerator(_make_pm_with(provider))

    await gen.generate(_task(title="Custom Title", description="Custom desc here"))

    assert provider.last_messages is not None
    user = provider.last_messages[-1]["content"]
    assert "Custom Title" in user
    assert "Custom desc here" in user
    assert "feature" in user


async def test_generate_rejects_empty_title():
    provider = _FakeProvider("openai", ["gpt-4"], response=_payload(["A", "B", "C"]))
    gen = PlanGenerator(_make_pm_with(provider))
    bad_task = Task.new("/tmp/proj", TaskKind.FEATURE, "")
    bad_task.title = "   "

    result = await gen.generate(bad_task)
    assert not result.ok
    assert "untitled" in result.error.lower()
    # Provider was never asked.
    assert provider.last_messages is None


async def test_generate_no_providers_returns_error():
    pm = ProviderManager()
    gen = PlanGenerator(pm)

    result = await gen.generate(_task())
    assert not result.ok
    assert "No AI provider" in result.error


async def test_generate_provider_with_no_models_returns_error():
    provider = _FakeProvider("openai", [])
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task())
    assert not result.ok
    assert "no models" in result.error.lower()


async def test_generate_list_models_failure_returns_error():
    provider = _FakeProvider(
        "openai",
        [],
        list_raises=RuntimeError("API down"),
    )
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task())
    assert not result.ok
    assert "API down" in result.error


async def test_generate_stream_failure_returns_error():
    provider = _FakeProvider(
        "openai",
        ["gpt-4"],
        stream_raises=RuntimeError("network exploded"),
    )
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task())
    assert not result.ok
    assert "network exploded" in result.error
    assert result.provider == "openai"


async def test_generate_unparseable_response_returns_error():
    provider = _FakeProvider(
        "openai",
        ["gpt-4"],
        response="I refuse to follow instructions, here is some prose instead.",
    )
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task())
    assert not result.ok
    assert "parse" in result.error.lower()


async def test_generate_model_id_miss_falls_back_to_first_provider():
    """A model_id that doesn't resolve falls back, doesn't error."""
    provider = _FakeProvider(
        "openai",
        ["gpt-4"],
        response=_payload(["a", "b", "c", "d"]),
    )
    gen = PlanGenerator(_make_pm_with(provider))

    result = await gen.generate(_task(), model_id="totally:not-real")
    assert result.ok
    assert result.model == "gpt-4"
