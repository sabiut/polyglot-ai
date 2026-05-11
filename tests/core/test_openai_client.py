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
    client._reasoning_prefixes = ("o1", "o4")
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


# ── error-handling regression tests ────────────────────────────────
#
# Lock in the friendly catches for ``httpx`` streaming-network drops
# and ``openai.RateLimitError`` so a future refactor can't quietly
# revert to dumping 30-line stack traces into the chat. The original
# trigger was a DeepSeek mid-stream connection drop seen in
# production (200 OK, then ``httpx.ReadError`` six seconds later);
# the rate-limit case is by symmetry with the request-time wrap the
# SDK does for HTTP 429 responses.


class _RaisingMidStream:
    """Async iterator that yields nothing before raising ``exc``.

    Mirrors the production scenario where the HTTP request to the
    provider succeeded (200 OK), the SDK opened the stream, and
    then the underlying TCP connection died before any chunk
    could be parsed.
    """

    def __init__(self, exc):
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


class _RaisingCompletions:
    """``chat.completions`` whose ``create`` returns a raising stream."""

    def __init__(self, exc):
        self._exc = exc

    async def create(self, **kwargs):
        return _RaisingMidStream(self._exc)


class _RaisingAtCreateCompletions:
    """``chat.completions`` whose ``create`` raises before the stream opens.

    This is the rate-limit shape — the SDK wraps the 429 response
    into ``RateLimitError`` and raises it at ``create`` time, not
    during iteration.
    """

    def __init__(self, exc):
        self._exc = exc

    async def create(self, **kwargs):
        raise self._exc


def _make_client_with_exc(exc, raise_at_create: bool = False):
    """Build a client whose underlying SDK raises ``exc`` on next stream chunk.

    Set ``raise_at_create=True`` for errors that fire before the
    stream opens (the SDK's request-time wraps like
    ``RateLimitError`` / ``APIConnectionError``).
    """
    bus = EventBus()
    client = OpenAIClient.__new__(OpenAIClient)
    AIProvider.__init__(client, bus)
    client._provider_name = "deepseek"
    client._provider_display_name = "DeepSeek"
    client._default_models = ["deepseek-v4-pro"]
    client._model_filter = ("deepseek",)
    client._enable_stream_options = True
    client._reasoning_prefixes = ()
    client._base_url = None
    fake_completions = (
        _RaisingAtCreateCompletions(exc) if raise_at_create else _RaisingCompletions(exc)
    )
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
    return client, bus


@pytest.mark.asyncio
async def test_httpx_read_error_mid_stream_yields_friendly_message():
    """The exact production failure mode: 200 OK then ``httpx.ReadError``.

    Asserts (a) the user-facing chunk is the friendly markdown,
    not a stack trace, (b) the event bus gets a sanitised
    summary rather than the raw exception text, and (c) the
    stream ends cleanly (no unhandled re-raise).
    """
    import httpx

    client, bus = _make_client_with_exc(httpx.ReadError("connection reset by peer"))
    bus_errors: list[str] = []
    from polyglot_ai.constants import EVT_AI_ERROR

    bus.subscribe(EVT_AI_ERROR, lambda error: bus_errors.append(error))

    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-v4-pro",
    ):
        out.append(chunk)

    bodies = [c.delta_content for c in out if c.delta_content]
    combined = "".join(bodies)

    # Friendly markdown made it through
    assert "Couldn't finish reading the response from DeepSeek" in combined
    # Partial-content acknowledgement (suggestion #5 from the review)
    assert "Any text above this line" in combined
    # No leaked stack trace tokens
    assert "Traceback" not in combined
    assert "httpcore" not in combined
    # Event bus got a *summary*, not the raw exception text
    assert bus_errors == ["DeepSeek connection dropped"]


@pytest.mark.asyncio
async def test_httpx_connect_error_takes_same_friendly_path():
    """Sister httpx errors (ConnectError, RemoteProtocolError, …) flow the same way."""
    import httpx

    client, _ = _make_client_with_exc(httpx.ConnectError("dns failed"))
    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-v4-pro",
    ):
        out.append(chunk)
    body = "".join(c.delta_content for c in out if c.delta_content)
    assert "Couldn't finish reading" in body


@pytest.mark.asyncio
async def test_openai_rate_limit_error_yields_friendly_message():
    """``RateLimitError`` at request time gets its own friendly branch."""
    from openai import RateLimitError

    # ``RateLimitError`` needs a fake response to construct cleanly.
    # The test only cares that ``isinstance(e, RateLimitError)`` fires;
    # constructing via ``__new__`` skips the response requirement.
    exc = RateLimitError.__new__(RateLimitError)
    Exception.__init__(exc, "you are being rate limited")

    client, bus = _make_client_with_exc(exc, raise_at_create=True)
    bus_errors: list[str] = []
    from polyglot_ai.constants import EVT_AI_ERROR

    bus.subscribe(EVT_AI_ERROR, lambda error: bus_errors.append(error))

    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-v4-pro",
    ):
        out.append(chunk)

    body = "".join(c.delta_content for c in out if c.delta_content)
    assert "rate limit reached" in body.lower()
    assert "switch to a different provider" in body.lower()
    assert "Traceback" not in body
    # Sanitised event-bus summary
    assert bus_errors == ["DeepSeek rate limit reached"]


@pytest.mark.asyncio
async def test_unknown_exception_falls_through_to_generic_handler():
    """Non-network, non-rate-limit errors hit ``_handle_stream_error``."""

    class _RandomFail(Exception):
        pass

    client, _ = _make_client_with_exc(_RandomFail("something else broke"))
    out = []
    async for chunk in client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-v4-pro",
    ):
        out.append(chunk)
    body = "".join(c.delta_content for c in out if c.delta_content)
    # Generic handler prefixes with "**Error:**" — that's the
    # signal we hit the fallback path, not one of the friendly
    # branches.
    assert "**Error:**" in body
