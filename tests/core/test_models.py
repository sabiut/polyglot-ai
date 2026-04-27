"""Tests for the AI conversation data models."""

from polyglot_ai.core.ai.models import Message, StreamChunk, ToolCall


# ── Message.to_api_dict ─────────────────────────────────────────────


def test_assistant_with_reasoning_emits_field():
    """DeepSeek's thinking-mode models require ``reasoning_content``
    to be echoed back on subsequent turns. Verify the assistant
    message serialiser includes the field when present."""
    msg = Message(
        role="assistant",
        content="42 is the answer.",
        reasoning_content="Let me think... 6 * 7 = 42.",
    )
    out = msg.to_api_dict()
    assert out["role"] == "assistant"
    assert out["content"] == "42 is the answer."
    assert out["reasoning_content"] == "Let me think... 6 * 7 = 42."


def test_assistant_without_reasoning_omits_field():
    """Standard chat models don't return reasoning_content. The
    serialised dict must not include the key at all in that case —
    OpenAI is lenient about unknown fields, but DeepSeek's strict
    mode would reject ``reasoning_content: null`` on a non-thinking
    request."""
    msg = Message(role="assistant", content="Hello.")
    out = msg.to_api_dict()
    assert "reasoning_content" not in out


def test_user_message_never_includes_reasoning():
    """Even if a Message was constructed with reasoning_content (a
    misuse, but defend against it), the to_api_dict path only emits
    it for assistant messages — user/system/tool roles must not
    carry that field to the API."""
    msg = Message(
        role="user",
        content="What is 6 * 7?",
        reasoning_content="(programmer error: should never end up here)",
    )
    out = msg.to_api_dict()
    assert "reasoning_content" not in out


def test_tool_message_never_includes_reasoning():
    msg = Message(
        role="tool",
        content="42",
        tool_call_id="call_abc",
        reasoning_content="(also should never be here)",
    )
    out = msg.to_api_dict()
    assert "reasoning_content" not in out


def test_assistant_with_reasoning_and_tool_calls():
    """When an assistant turn includes both reasoning and tool calls
    (common with thinking-mode models that decide to call a tool),
    both must appear in the serialised output so the next request can
    correctly resume."""
    msg = Message(
        role="assistant",
        content=None,
        reasoning_content="I need to compute this — let me call the calculator.",
        tool_calls=[ToolCall(id="call_1", function_name="calc", arguments='{"expr":"6*7"}')],
    )
    out = msg.to_api_dict()
    assert out["reasoning_content"].startswith("I need to compute")
    assert out["tool_calls"][0]["id"] == "call_1"


# ── StreamChunk ─────────────────────────────────────────────────────


def test_stream_chunk_carries_delta_reasoning():
    """The streaming-side carrier for chain-of-thought deltas. Each
    chunk emitted by the OpenAIClient may include a reasoning delta
    when the underlying provider sends one (e.g. DeepSeek's
    ``reasoning_content`` stream-delta extension)."""
    chunk = StreamChunk(delta_content="The answer", delta_reasoning="Let me think")
    assert chunk.delta_content == "The answer"
    assert chunk.delta_reasoning == "Let me think"


def test_stream_chunk_defaults_have_no_reasoning():
    """Default-constructed chunks (used for usage-only chunks, etc.)
    must not falsely advertise a reasoning delta."""
    chunk = StreamChunk()
    assert chunk.delta_reasoning is None
