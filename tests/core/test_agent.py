"""Tests for ``AgentLoop`` — the multi-turn tool-calling orchestrator.

These tests exercise the provider-agnostic loop in
``polyglot_ai.core.ai.agent`` without hitting any real LLM. They use:

* A ``FakeProvider`` that scripts ``stream_chat`` to yield a fixed list
  of ``StreamChunk`` objects per iteration. This lets each test drive
  the loop through a deterministic conversation.
* A ``FakeToolRegistry`` matching the subset of ``ToolRegistry`` that
  ``AgentLoop`` actually calls: ``get_tool_definitions``,
  ``needs_approval``, ``execute``.
* The real ``EventBus``.

Bug-class motivation
--------------------
``feedback_streaming_bugs.md`` records a recurring class of streaming
bugs where tool-call id/name/arguments fragments arrive across multiple
chunks (OpenAI-style) and get double-counted or dropped during
reassembly. ``test_tool_call_fragments_concatenate`` locks in the
expected reassembly behavior at the loop layer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable

import pytest

from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_TOOL_CALL_REQUEST,
    EVT_APPROVAL_REQUESTED,
    EVT_APPROVAL_RESPONSE,
    MAX_AGENT_ITERATIONS,
)
from polyglot_ai.core.ai.agent import AgentLoop
from polyglot_ai.core.ai.models import Conversation, Message, StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus


# ── Test doubles ────────────────────────────────────────────────────


class FakeProvider(AIProvider):
    """Provider that replays a scripted sequence of chunk lists.

    Each call to ``stream_chat`` consumes the next sublist from
    ``self.scripts``. Test helpers push scripts onto this list in the
    order they'll be consumed by the loop.
    """

    def __init__(self, event_bus: EventBus, scripts: list[list[StreamChunk]]):
        super().__init__(event_bus)
        self.scripts = list(scripts)
        self.call_count = 0
        self.last_messages: list[dict] | None = None
        self.last_tools: list[dict] | None = None

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake"

    async def list_models(self) -> list[str]:
        return ["fake-model"]

    async def stream_chat(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        max_tokens=4096,
        system_prompt=None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self.call_count += 1
        self.last_messages = messages
        self.last_tools = tools
        if not self.scripts:
            # Default: empty assistant turn so the loop exits
            return
        script = self.scripts.pop(0)
        for chunk in script:
            yield chunk

    async def test_connection(self):
        return True, "ok"

    def update_api_key(self, api_key: str) -> None:
        return None


@dataclass
class FakeToolRegistry:
    """Minimal tool-registry stand-in for the agent loop.

    The loop only uses three methods, so we implement just those.
    ``execute_handler`` lets each test plug in its own result function.
    """

    tool_defs: list[dict] = field(default_factory=list)
    approval_required: set[str] = field(default_factory=set)
    execute_handler: Callable[[str, str], str] | None = None
    executions: list[tuple[str, str]] = field(default_factory=list)

    def get_tool_definitions(self) -> list[dict]:
        return self.tool_defs

    def needs_approval(self, tool_name: str) -> bool:
        return tool_name in self.approval_required

    async def execute(self, tool_name: str, arguments: str) -> str:
        self.executions.append((tool_name, arguments))
        if self.execute_handler is not None:
            return self.execute_handler(tool_name, arguments)
        return f"ok:{tool_name}"


# ── Chunk helpers ───────────────────────────────────────────────────


def _content_chunk(text: str) -> StreamChunk:
    return StreamChunk(delta_content=text)


def _tool_call_start(
    index: int, call_id: str, name: str, finish_reason: str | None = None
) -> StreamChunk:
    return StreamChunk(
        tool_calls=[
            {
                "index": index,
                "id": call_id,
                "function": {"name": name, "arguments": ""},
            }
        ],
        finish_reason=finish_reason,
    )


def _tool_call_args(index: int, fragment: str) -> StreamChunk:
    return StreamChunk(
        tool_calls=[
            {
                "index": index,
                # OpenAI-style delta: no id/name after the first chunk
                "id": None,
                "function": {"name": None, "arguments": fragment},
            }
        ]
    )


def _finish(reason: str) -> StreamChunk:
    return StreamChunk(finish_reason=reason)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def conv() -> Conversation:
    c = Conversation(id=1, title="Test", model="fake-model")
    c.messages.append(Message(role="user", content="hi"))
    return c


# ── Tests ───────────────────────────────────────────────────────────


async def test_plain_text_response_exits_after_one_turn(bus, conv):
    """No tool calls + finish_reason=stop → loop exits after one iteration."""
    provider = FakeProvider(
        bus,
        scripts=[[_content_chunk("hello "), _content_chunk("world"), _finish("stop")]],
    )
    tools = FakeToolRegistry()
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    assert provider.call_count == 1
    assert len(conv.messages) == 2  # user + assistant
    assert conv.messages[-1].role == "assistant"
    assert conv.messages[-1].content == "hello world"
    assert conv.messages[-1].tool_calls is None


async def test_tool_call_fragments_concatenate(bus, conv):
    """Streaming tool-call fragments must reassemble into one call.

    Locks in the loop-level fix for the concat-bug class described in
    ``feedback_streaming_bugs.md``: id+name arrive once on the opening
    chunk, then argument fragments arrive on later chunks sharing the
    same ``index`` but with id=None/name=None. The final ToolCall must
    carry the original id+name and the concatenated arguments.
    """
    script_iter1 = [
        _tool_call_start(0, "call_abc", "file_read"),
        _tool_call_args(0, '{"pa'),
        _tool_call_args(0, 'th":"'),
        _tool_call_args(0, 'README.md"}'),
        _finish("tool_calls"),
    ]
    # After tool result, the model says done
    script_iter2 = [_content_chunk("all done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])

    tools = FakeToolRegistry(
        tool_defs=[{"type": "function", "function": {"name": "file_read"}}],
        execute_handler=lambda name, args: "file contents",
    )
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    # Provider called twice (initial call + after tool result)
    assert provider.call_count == 2

    # Assistant turn 1 has the reassembled tool call
    assistant_msg = conv.messages[1]
    assert assistant_msg.role == "assistant"
    assert assistant_msg.tool_calls is not None
    assert len(assistant_msg.tool_calls) == 1
    tc = assistant_msg.tool_calls[0]
    assert tc.id == "call_abc"
    assert tc.function_name == "file_read"
    assert tc.arguments == '{"path":"README.md"}'

    # Tool result message was appended
    tool_msg = conv.messages[2]
    assert tool_msg.role == "tool"
    assert tool_msg.tool_call_id == "call_abc"
    assert tool_msg.content == "file contents"

    # Final assistant turn
    final = conv.messages[3]
    assert final.role == "assistant"
    assert final.content == "all done"

    # Tool was executed with the full concatenated args
    assert tools.executions == [("file_read", '{"path":"README.md"}')]


async def test_parallel_tool_calls_reassemble_independently(bus, conv):
    """Two tool calls at different indexes must not bleed into each other."""
    script_iter1 = [
        _tool_call_start(0, "call_a", "file_read"),
        _tool_call_start(1, "call_b", "file_write"),
        _tool_call_args(0, '{"path":"a"}'),
        _tool_call_args(1, '{"path":"b","content":"x"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(execute_handler=lambda n, a: f"ran {n}")
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    assistant = conv.messages[1]
    assert assistant.tool_calls is not None
    assert len(assistant.tool_calls) == 2
    by_id = {tc.id: tc for tc in assistant.tool_calls}
    assert by_id["call_a"].function_name == "file_read"
    assert by_id["call_a"].arguments == '{"path":"a"}'
    assert by_id["call_b"].function_name == "file_write"
    assert by_id["call_b"].arguments == '{"path":"b","content":"x"}'

    # Both tool-result messages appended, in the order the calls were emitted
    assert conv.messages[2].tool_call_id == "call_a"
    assert conv.messages[3].tool_call_id == "call_b"


async def test_approval_granted_executes_tool(bus, conv):
    """When a tool needs approval and user approves, it runs normally."""
    script_iter1 = [
        _tool_call_start(0, "call_1", "shell_exec"),
        _tool_call_args(0, '{"command":"ls"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("ok"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(
        approval_required={"shell_exec"},
        execute_handler=lambda n, a: "shell output",
    )
    loop = AgentLoop(provider, tools, bus)

    # Auto-approve on approval request
    approval_requests: list[dict] = []

    def on_request(**kwargs):
        approval_requests.append(kwargs)
        bus.emit(EVT_APPROVAL_RESPONSE, approved=True, request_id=kwargs["request_id"])

    bus.subscribe(EVT_APPROVAL_REQUESTED, on_request)

    await loop.run(conv)

    assert len(approval_requests) == 1
    assert approval_requests[0]["tool_name"] == "shell_exec"
    assert tools.executions == [("shell_exec", '{"command":"ls"}')]
    # Tool result is the real output, not a rejection string
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert tool_msg.content == "shell output"


async def test_approval_rejected_skips_tool(bus, conv):
    """When user rejects, tool is not executed and a rejection message is recorded."""
    script_iter1 = [
        _tool_call_start(0, "call_1", "shell_exec"),
        _tool_call_args(0, '{"command":"rm -rf /"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("understood"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(approval_required={"shell_exec"})
    loop = AgentLoop(provider, tools, bus)

    def on_request(**kwargs):
        bus.emit(EVT_APPROVAL_RESPONSE, approved=False, request_id=kwargs["request_id"])

    bus.subscribe(EVT_APPROVAL_REQUESTED, on_request)

    await loop.run(conv)

    # execute() must NOT have been called
    assert tools.executions == []
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert "rejected" in tool_msg.content.lower()


async def test_approval_mismatched_request_id_is_ignored(bus, conv, monkeypatch):
    """An approval response with a stale/wrong request_id must not unblock the wait.

    Guards against race between overlapping approval prompts. We shorten
    the timeout so the test doesn't hang for 5 minutes when the mismatched
    response is correctly ignored.
    """
    import polyglot_ai.core.ai.agent as agent_mod

    monkeypatch.setattr(agent_mod, "APPROVAL_TIMEOUT", 0.2)

    script_iter1 = [
        _tool_call_start(0, "call_1", "shell_exec"),
        _tool_call_args(0, '{"command":"ls"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(approval_required={"shell_exec"})
    loop = AgentLoop(provider, tools, bus)

    def on_request(**kwargs):
        # Fire a WRONG request_id first, then never send the right one
        bus.emit(EVT_APPROVAL_RESPONSE, approved=True, request_id="stale-id")

    bus.subscribe(EVT_APPROVAL_REQUESTED, on_request)

    await loop.run(conv)

    # Tool should NOT have executed — the mismatched response was ignored
    # and the real wait timed out.
    assert tools.executions == []
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert "timed out" in tool_msg.content.lower()


async def test_approval_timeout_skips_tool(bus, conv, monkeypatch):
    """No approval response before APPROVAL_TIMEOUT → tool is skipped."""
    import polyglot_ai.core.ai.agent as agent_mod

    monkeypatch.setattr(agent_mod, "APPROVAL_TIMEOUT", 0.1)

    script_iter1 = [
        _tool_call_start(0, "call_1", "shell_exec"),
        _tool_call_args(0, '{"command":"ls"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("moving on"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(approval_required={"shell_exec"})
    loop = AgentLoop(provider, tools, bus)

    # No approval subscriber → request falls on the floor, times out
    await loop.run(conv)

    assert tools.executions == []
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert "timed out" in tool_msg.content.lower()


async def test_max_iterations_stops_loop(bus, conv):
    """Loop exits cleanly when MAX_AGENT_ITERATIONS is reached."""
    # Every script keeps asking for another tool call → would loop forever
    infinite_scripts = [
        [
            _tool_call_start(0, f"call_{i}", "file_read"),
            _tool_call_args(0, '{"path":"x"}'),
            _finish("tool_calls"),
        ]
        for i in range(MAX_AGENT_ITERATIONS + 5)
    ]
    provider = FakeProvider(bus, scripts=infinite_scripts)
    tools = FakeToolRegistry(execute_handler=lambda n, a: "x")
    loop = AgentLoop(provider, tools, bus)

    errors: list[str] = []
    bus.subscribe(EVT_AI_ERROR, lambda error, **_: errors.append(error))

    await loop.run(conv)

    assert provider.call_count == MAX_AGENT_ITERATIONS
    assert any("Max iterations" in e for e in errors)
    # Last assistant message states that the cap was reached
    last_assistant = [m for m in conv.messages if m.role == "assistant"][-1]
    assert "maximum" in last_assistant.content.lower()


async def test_provider_exception_emits_error_and_exits(bus, conv):
    """If the provider raises, the loop logs, emits EVT_AI_ERROR, and exits."""

    class BoomProvider(FakeProvider):
        async def stream_chat(self, *args, **kwargs):
            self.call_count += 1
            raise RuntimeError("provider blew up")
            yield  # make it an async generator (unreachable)

    provider = BoomProvider(bus, scripts=[])
    tools = FakeToolRegistry()
    loop = AgentLoop(provider, tools, bus)

    errors: list[str] = []
    bus.subscribe(EVT_AI_ERROR, lambda error, **_: errors.append(error))

    await loop.run(conv)

    assert errors, "expected EVT_AI_ERROR to be emitted"
    # Loop must release the running flag even on error
    assert loop._running is False


async def test_reentrant_run_is_noop(bus, conv):
    """Calling run() while already running must be rejected, not double-start."""
    # First call will never finish on its own — we'll release it manually.
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowProvider(FakeProvider):
        async def stream_chat(self, *args, **kwargs):
            self.call_count += 1
            started.set()
            await release.wait()
            yield _content_chunk("done")
            yield _finish("stop")

    provider = SlowProvider(bus, scripts=[])
    tools = FakeToolRegistry()
    loop = AgentLoop(provider, tools, bus)

    task = asyncio.create_task(loop.run(conv))
    await started.wait()

    # Second call should return immediately without touching the provider
    await loop.run(conv)
    assert provider.call_count == 1

    release.set()
    await task


async def test_unsubscribes_after_run(bus, conv):
    """Loop must unsubscribe its approval listener when it exits."""
    provider = FakeProvider(bus, scripts=[[_content_chunk("hi"), _finish("stop")]])
    tools = FakeToolRegistry()
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    # Firing an approval response after the loop has exited must not
    # crash or leak state — and the loop should no longer be subscribed.
    assert loop._subscribed is False
    # Sanity: bus has no agent callback left (other subscribers are fine)
    assert all(
        cb is not loop._on_approval for cb in bus._subscribers.get(EVT_APPROVAL_RESPONSE, [])
    )


async def test_system_prompt_prepended_to_messages(bus, conv):
    """When system_prompt is set, it must be the first message sent to the provider."""
    provider = FakeProvider(bus, scripts=[[_content_chunk("ok"), _finish("stop")]])
    tools = FakeToolRegistry()
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv, system_prompt="you are helpful")

    assert provider.last_messages is not None
    assert provider.last_messages[0] == {"role": "system", "content": "you are helpful"}
    # Then the user message
    assert provider.last_messages[1]["role"] == "user"


async def test_tool_call_request_event_emitted(bus, conv):
    """Every executed tool call emits EVT_AI_TOOL_CALL_REQUEST."""
    script_iter1 = [
        _tool_call_start(0, "call_1", "file_read"),
        _tool_call_args(0, '{"path":"a"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(execute_handler=lambda n, a: "x")
    loop = AgentLoop(provider, tools, bus)

    events: list[dict] = []
    bus.subscribe(EVT_AI_TOOL_CALL_REQUEST, lambda **kw: events.append(kw))

    await loop.run(conv)

    assert len(events) == 1
    assert events[0]["tool_name"] == "file_read"
    assert events[0]["arguments"] == '{"path":"a"}'


async def test_empty_content_is_stored_as_none(bus, conv):
    """An assistant turn with only tool calls must have content=None, not ''.

    Providers differ on how they treat empty content; the loop must
    normalise to ``None`` so that ``to_api_dict`` doesn't send an empty
    string that some APIs reject alongside tool_calls.
    """
    script_iter1 = [
        _tool_call_start(0, "call_1", "file_read"),
        _tool_call_args(0, '{"path":"a"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(execute_handler=lambda n, a: "x")
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    assistant = conv.messages[1]
    assert assistant.tool_calls is not None
    assert assistant.content is None


async def test_structured_events_cover_a_full_turn(bus, conv, caplog):
    """The structured log stream should emit turn_start, tool_call,
    tool_result (outcome=executed), and turn_end for a normal
    tool-using turn. Ops dashboards and post-hoc debugging rely on
    this shape being stable.
    """
    script_iter1 = [
        _tool_call_start(0, "call_1", "file_read"),
        _tool_call_args(0, '{"path":"a"}'),
        _finish("tool_calls"),
    ]
    script_iter2 = [_content_chunk("done"), _finish("stop")]
    provider = FakeProvider(bus, scripts=[script_iter1, script_iter2])
    tools = FakeToolRegistry(execute_handler=lambda n, a: "ok")
    loop = AgentLoop(provider, tools, bus)

    import json
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="polyglot_ai.agent.events"):
        await loop.run(conv)

    # Every message logged to the events logger should be valid JSON
    events = []
    for rec in caplog.records:
        if rec.name == "polyglot_ai.agent.events":
            try:
                events.append(json.loads(rec.getMessage()))
            except json.JSONDecodeError:
                pytest.fail(f"non-JSON event log: {rec.getMessage()!r}")

    event_kinds = [e["event"] for e in events]
    # At minimum: two turn_start/turn_end pairs, plus one tool_call +
    # tool_result for the single tool invocation.
    assert event_kinds.count("turn_start") == 2
    assert event_kinds.count("turn_end") == 2
    assert event_kinds.count("tool_call") == 1
    assert event_kinds.count("tool_result") == 1

    tool_result = next(e for e in events if e["event"] == "tool_result")
    assert tool_result["outcome"] == "executed"
    assert tool_result["tool_name"] == "file_read"
    # Sizes are integers, not raw content — make sure we log metadata
    # not secrets.
    assert isinstance(tool_result["result_chars"], int)
    assert "result" not in tool_result  # the raw result must NOT be logged


async def test_finish_reason_stop_with_tool_calls_still_exits(bus, conv):
    """If finish_reason is 'stop' (not 'tool_calls'), the loop must not re-invoke.

    Some providers return partial tool_calls data but finish with 'stop'.
    The loop's contract is: only re-invoke when finish_reason is
    'tool_calls' or 'tool_use'. This pins that behaviour.
    """
    script_iter1 = [
        _tool_call_start(0, "call_1", "file_read"),
        _tool_call_args(0, '{"path":"a"}'),
        _finish("stop"),  # NOT tool_calls
    ]
    provider = FakeProvider(bus, scripts=[script_iter1])
    tools = FakeToolRegistry(execute_handler=lambda n, a: "x")
    loop = AgentLoop(provider, tools, bus)

    await loop.run(conv)

    assert provider.call_count == 1
    assert tools.executions == []  # tool was NOT executed
