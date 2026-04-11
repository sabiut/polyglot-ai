"""Tests for ``OpenAIClient.stream_chat`` — tool-call assembly.

Mocks the openai SDK's ``chat.completions.create(stream=True)`` call
to feed scripted chat-completion chunks into the provider and assert
the resulting ``StreamChunk`` sequence.

Focused on the concat bug class from ``feedback_streaming_bugs.md``:
the OpenAI streaming protocol emits a tool call as one "start" delta
carrying id+name, followed by N argument-fragment deltas that share
the same ``index`` but have ``id=None`` and ``name=None``. The
provider must pass that shape through verbatim so downstream
reassembly does not double-count id/name or collapse indices.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polyglot_ai.constants import EVT_AI_STREAM_CHUNK, EVT_AI_STREAM_DONE
from polyglot_ai.core.ai.client import OpenAIClient
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


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _AIter(self._chunks)


class _FakeChat:
    def __init__(self, chunks):
        self.completions = _FakeCompletions(chunks)


class _FakeOpenAISDK:
    def __init__(self, chunks):
        self.chat = _FakeChat(chunks)


def _make_client(chunks):
    bus = EventBus()
    client = OpenAIClient.__new__(OpenAIClient)
    AIProvider.__init__(client, bus)
    client._provider_name = "openai"
    client._provider_display_name = "OpenAI"
    client._default_models = ["gpt-5.4"]
    client._model_filter = ("gpt-5",)
    client._enable_stream_options = True
    client._reasoning_prefixes = ("o1", "o3", "o4")
    client._base_url = None
    client._client = _FakeOpenAISDK(chunks)
    return client, bus


# ── chunk builders ──────────────────────────────────────────────────


def _chunk(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _usage_chunk(prompt=1, completion=2, total=3):
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
    return SimpleNamespace(choices=[], usage=usage)


def _tc(index, *, tc_id=None, name=None, arguments=""):
    return SimpleNamespace(
        index=index,
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ── tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_text_stream_yields_deltas_and_emits_stream_done():
    chunks = [
        _chunk(content="Hello"),
        _chunk(content=" world"),
        _chunk(finish_reason="stop"),
        _usage_chunk(prompt=10, completion=2, total=12),
    ]
    client, bus = _make_client(chunks)

    seen: list[str] = []
    done_hits: list[bool] = []
    bus.subscribe(EVT_AI_STREAM_CHUNK, lambda content: seen.append(content))
    bus.subscribe(EVT_AI_STREAM_DONE, lambda: done_hits.append(True))

    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.4",
    ):
        out.append(chunk)

    deltas = [c.delta_content for c in out if c.delta_content is not None]
    assert deltas == ["Hello", " world"]
    assert seen == ["Hello", " world"]
    assert done_hits == [True]

    finishes = [c.finish_reason for c in out if c.finish_reason]
    assert finishes == ["stop"]

    usage_chunks = [c for c in out if c.usage]
    assert usage_chunks and usage_chunks[-1].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
    }


@pytest.mark.asyncio
async def test_single_tool_call_split_across_deltas_preserves_index_and_args():
    """One tool call split across three fragment deltas: the first
    carries id+name, the rest carry id=None/name=None but the same
    index. Concatenating arguments must reconstruct the exact JSON.
    """
    chunks = [
        _chunk(tool_calls=[_tc(0, tc_id="call_1", name="edit_file", arguments='{"pa')]),
        _chunk(tool_calls=[_tc(0, arguments='th": "a.py"')]),
        _chunk(tool_calls=[_tc(0, arguments=', "content": "x"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    client, _ = _make_client(chunks)

    out = []
    async for chunk in client.stream_chat(messages=[], model="gpt-5.4"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 3
    assert all(tc.tool_calls[0]["index"] == 0 for tc in tool_chunks)

    assert tool_chunks[0].tool_calls[0]["id"] == "call_1"
    assert tool_chunks[0].tool_calls[0]["function"]["name"] == "edit_file"
    for tc in tool_chunks[1:]:
        assert tc.tool_calls[0]["id"] is None
        assert tc.tool_calls[0]["function"]["name"] is None

    reconstructed = "".join(tc.tool_calls[0]["function"]["arguments"] for tc in tool_chunks)
    assert reconstructed == '{"path": "a.py", "content": "x"}'

    finishes = [c.finish_reason for c in out if c.finish_reason]
    assert finishes == ["tool_calls"]


@pytest.mark.asyncio
async def test_two_sequential_tool_calls_keep_distinct_indices():
    """Two parallel/sequential tool calls in one response get index=0
    and index=1 from the SDK. The provider must surface them unchanged
    so downstream reassembly does not merge them into one call.
    """
    chunks = [
        _chunk(tool_calls=[_tc(0, tc_id="call_A", name="read_file", arguments='{"pa')]),
        _chunk(tool_calls=[_tc(1, tc_id="call_B", name="read_file", arguments='{"pa')]),
        _chunk(tool_calls=[_tc(0, arguments='th":"a"}')]),
        _chunk(tool_calls=[_tc(1, arguments='th":"b"}')]),
        _chunk(finish_reason="tool_calls"),
    ]
    client, _ = _make_client(chunks)

    out = []
    async for chunk in client.stream_chat(messages=[], model="gpt-5.4"):
        out.append(chunk)

    tool_chunks = [c for c in out if c.tool_calls]
    assert len(tool_chunks) == 4

    by_idx: dict[int, list] = {0: [], 1: []}
    for tc in tool_chunks:
        by_idx[tc.tool_calls[0]["index"]].append(tc.tool_calls[0])

    assert by_idx[0][0]["id"] == "call_A"
    assert by_idx[0][0]["function"]["name"] == "read_file"
    assert "".join(t["function"]["arguments"] for t in by_idx[0]) == '{"path":"a"}'

    assert by_idx[1][0]["id"] == "call_B"
    assert by_idx[1][0]["function"]["name"] == "read_file"
    assert "".join(t["function"]["arguments"] for t in by_idx[1]) == '{"path":"b"}'
