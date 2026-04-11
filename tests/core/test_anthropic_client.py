"""Tests for ``AnthropicClient.stream_chat`` — tool-call assembly.

Mocks the anthropic SDK's ``messages.stream()`` async context manager
to feed scripted events into the provider and assert the resulting
``StreamChunk`` sequence.

Focused on the concat bug class flagged in ``feedback_streaming_bugs.md``:
a single tool_use block split across multiple ``partial_json`` deltas
must surface as one complete call per tool index, and two sequential
tool_use blocks must get distinct tool indices even when Anthropic's
global content-block numbering leaves gaps for intermixed text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polyglot_ai.constants import EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.ai.anthropic_client import AnthropicClient
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus


# ── fake SDK ────────────────────────────────────────────────────────


class _FakeMessagesStream:
    """Async context manager mimicking ``anthropic.messages.stream()``."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for e in self._events:
            yield e

    async def get_final_message(self):
        return None


class _FakeMessages:
    def __init__(self, stream_obj):
        self._stream = stream_obj
        self.last_kwargs = None

    def stream(self, **kwargs):
        self.last_kwargs = kwargs
        return self._stream


class _FakeAnthropicSDK:
    def __init__(self, events):
        self.messages = _FakeMessages(_FakeMessagesStream(events))


def _make_client(events):
    bus = EventBus()
    client = AnthropicClient.__new__(AnthropicClient)
    AIProvider.__init__(client, bus)
    client._client = _FakeAnthropicSDK(events)
    return client, bus


# ── event builders ──────────────────────────────────────────────────


def _text_start(index):
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="text"),
    )


def _text_delta(index, text):
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(text=text),
    )


def _tool_start(index, call_id, name):
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="tool_use", id=call_id, name=name),
    )


def _tool_delta(index, partial):
    # No ``text`` attribute — the provider branches on hasattr(delta, "text").
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(partial_json=partial),
    )


# ── tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_text_stream_yields_deltas_and_emits_stream_done():
    events = [
        _text_start(0),
        _text_delta(0, "Hello"),
        _text_delta(0, " world"),
    ]
    client, bus = _make_client(events)

    seen_chunks: list[str] = []
    done_hits: list[bool] = []
    bus.subscribe(EVT_AI_STREAM_CHUNK, lambda content: seen_chunks.append(content))
    bus.subscribe(EVT_AI_STREAM_DONE, lambda: done_hits.append(True))

    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-opus-4-6",
    ):
        out.append(chunk)

    deltas = [c.delta_content for c in out if c.delta_content is not None]
    assert deltas == ["Hello", " world"]
    assert seen_chunks == ["Hello", " world"]
    assert done_hits == [True]
    assert all(c.tool_calls is None for c in out)


@pytest.mark.asyncio
async def test_single_tool_call_split_across_deltas_assembles_cleanly():
    """One tool_use block split across three partial_json deltas must
    yield four tool chunks (1 start + 3 args) that all share index=0,
    with id/name only on the start chunk, and whose argument fragments
    concatenate back to the exact original JSON.
    """
    events = [
        _tool_start(0, "toolu_01", "edit_file"),
        _tool_delta(0, '{"pa'),
        _tool_delta(0, 'th": "a.py"'),
        _tool_delta(0, ', "content": "x"}'),
    ]
    client, _ = _make_client(events)

    out = []
    async for chunk in client.stream_chat(messages=[], model="claude-opus-4-6"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 4
    assert all(tc.tool_calls[0]["index"] == 0 for tc in tool_chunks)

    assert tool_chunks[0].tool_calls[0]["id"] == "toolu_01"
    assert tool_chunks[0].tool_calls[0]["function"]["name"] == "edit_file"
    assert tool_chunks[0].tool_calls[0]["function"]["arguments"] == ""

    for tc in tool_chunks[1:]:
        assert tc.tool_calls[0]["id"] is None
        assert tc.tool_calls[0]["function"]["name"] is None

    reconstructed = "".join(tc.tool_calls[0]["function"]["arguments"] for tc in tool_chunks)
    assert reconstructed == '{"path": "a.py", "content": "x"}'


@pytest.mark.asyncio
async def test_two_sequential_tool_calls_get_distinct_indices():
    """Anthropic numbers ALL content blocks globally; the provider must
    remap so only tool_use blocks get sequential tool indices 0, 1.
    A leading text block at block index 0 must not shift the first
    tool off of tool-index 0.
    """
    events = [
        _text_start(0),
        _text_delta(0, "thinking..."),
        _tool_start(1, "toolu_A", "read_file"),
        _tool_delta(1, '{"path":"a"}'),
        _tool_start(2, "toolu_B", "read_file"),
        _tool_delta(2, '{"path":"b"}'),
    ]
    client, _ = _make_client(events)

    out = []
    async for chunk in client.stream_chat(messages=[], model="claude-opus-4-6"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 4

    assert tool_chunks[0].tool_calls[0]["index"] == 0
    assert tool_chunks[0].tool_calls[0]["id"] == "toolu_A"
    assert tool_chunks[1].tool_calls[0]["index"] == 0
    assert tool_chunks[1].tool_calls[0]["function"]["arguments"] == '{"path":"a"}'

    assert tool_chunks[2].tool_calls[0]["index"] == 1
    assert tool_chunks[2].tool_calls[0]["id"] == "toolu_B"
    assert tool_chunks[3].tool_calls[0]["index"] == 1
    assert tool_chunks[3].tool_calls[0]["function"]["arguments"] == '{"path":"b"}'

    deltas = [c.delta_content for c in out if c.delta_content]
    assert deltas == ["thinking..."]
