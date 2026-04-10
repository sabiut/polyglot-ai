"""Tests for ``GoogleClient.stream_chat`` — tool-call assembly.

Mocks the google.genai SDK's ``aio.models.generate_content_stream``
async iterator to feed scripted chunks into the provider and assert
the resulting ``StreamChunk`` sequence.

Unlike Anthropic/OpenAI, Gemini emits each function call atomically
in a single chunk (no partial_json fragments). The concat bug shape
flagged in ``feedback_streaming_bugs.md`` here is instead: the
provider must assign a running tool-call index so two function calls
appearing in the same or different chunks get distinct indices and
do NOT collide on index=0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polyglot_ai.constants import EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.ai.google_client import GoogleClient
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus


# ── fake SDK ────────────────────────────────────────────────────────


class _AIter:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for it in self._items:
            yield it


class _FakeGenaiModels:
    def __init__(self, chunks):
        self._chunks = chunks
        self.last_kwargs = None

    def generate_content_stream(self, **kwargs):
        self.last_kwargs = kwargs
        return _AIter(self._chunks)


class _FakeGenaiAio:
    def __init__(self, chunks):
        self.models = _FakeGenaiModels(chunks)


class _FakeGenaiClient:
    def __init__(self, chunks):
        self.aio = _FakeGenaiAio(chunks)


def _make_client(chunks):
    bus = EventBus()
    client = GoogleClient.__new__(GoogleClient)
    AIProvider.__init__(client, bus)
    client._api_key = "test"
    client._client = _FakeGenaiClient(chunks)
    return client, bus


# ── chunk builders ──────────────────────────────────────────────────


def _text_chunk(text):
    return SimpleNamespace(text=text, candidates=[], usage_metadata=None)


def _fcall(name, args):
    return SimpleNamespace(function_call=SimpleNamespace(name=name, args=args))


def _tool_chunk(*fcalls):
    parts = list(fcalls)
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
    return SimpleNamespace(text=None, candidates=[candidate], usage_metadata=None)


def _usage_chunk(prompt=1, completion=2, total=3):
    um = SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=completion,
        total_token_count=total,
    )
    return SimpleNamespace(text=None, candidates=[], usage_metadata=um)


# ── tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_text_stream_yields_deltas_and_emits_stream_done():
    chunks = [
        _text_chunk("Hello"),
        _text_chunk(" world"),
        _usage_chunk(prompt=5, completion=2, total=7),
    ]
    client, bus = _make_client(chunks)

    seen: list[str] = []
    done_hits: list[bool] = []
    bus.subscribe(EVT_AI_STREAM_CHUNK, lambda content: seen.append(content))
    bus.subscribe(EVT_AI_STREAM_DONE, lambda: done_hits.append(True))

    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-3.1-pro-preview",
    ):
        out.append(chunk)

    deltas = [c.delta_content for c in out if c.delta_content is not None]
    assert deltas == ["Hello", " world"]
    assert seen == ["Hello", " world"]
    assert done_hits == [True]
    assert all(c.tool_calls is None for c in out)

    usage_chunks = [c for c in out if c.usage]
    assert usage_chunks and usage_chunks[-1].usage == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }


@pytest.mark.asyncio
async def test_single_function_call_emits_one_tool_chunk_with_index_zero():
    """Gemini sends a complete function_call atomically. The provider
    must emit a single tool chunk at index 0 with JSON-encoded args.
    """
    chunks = [
        _tool_chunk(_fcall("edit_file", {"path": "a.py", "content": "x"})),
    ]
    client, _ = _make_client(chunks)

    out = []
    async for chunk in client.stream_chat(messages=[], model="gemini-3.1-pro-preview"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 1

    tc = tool_chunks[0].tool_calls[0]
    assert tc["index"] == 0
    assert tc["function"]["name"] == "edit_file"
    assert tc["id"] == "call_edit_file_0"

    import json

    assert json.loads(tc["function"]["arguments"]) == {
        "path": "a.py",
        "content": "x",
    }


@pytest.mark.asyncio
async def test_two_sequential_function_calls_get_distinct_running_indices():
    """Two function calls — whether emitted in the same chunk or in
    separate chunks — must receive distinct, monotonically increasing
    tool-call indices. This is the regression guard for the prior bug
    where both calls landed at index 0 and the second overwrote the
    first downstream.
    """
    # Mix: first call in its own chunk, second call in a later chunk
    chunks = [
        _tool_chunk(_fcall("read_file", {"path": "a"})),
        _text_chunk("..."),
        _tool_chunk(_fcall("read_file", {"path": "b"})),
    ]
    client, _ = _make_client(chunks)

    out = []
    async for chunk in client.stream_chat(messages=[], model="gemini-3.1-pro-preview"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 2

    first = tool_chunks[0].tool_calls[0]
    second = tool_chunks[1].tool_calls[0]

    assert first["index"] == 0
    assert second["index"] == 1
    # Distinct ids — the running-counter fix in feedback_streaming_bugs.md
    assert first["id"] != second["id"]

    import json

    assert json.loads(first["function"]["arguments"]) == {"path": "a"}
    assert json.loads(second["function"]["arguments"]) == {"path": "b"}

    # Intervening text came through in order
    deltas = [c.delta_content for c in out if c.delta_content]
    assert deltas == ["..."]
