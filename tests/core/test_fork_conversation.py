"""Regression tests for fork_conversation, including reasoning_content preservation.

F11: fork_conversation previously omitted reasoning_content from the SELECT and
INSERT, which caused forked conversations containing thinking-mode assistant turns
(DeepSeek R1 / V4-pro) to be missing their chain-of-thought. The next API call
from the forked conversation would then fail with a 'missing reasoning_content'
error.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_fork_preserves_reasoning_content(db):
    """Forking a conversation must copy reasoning_content on assistant turns."""
    conv_id = await db.create_conversation("Thinking chat", "deepseek-reasoner")

    await db.insert_message(conv_id, "user", content="What is 6*7?")
    assistant_msg_id = await db.insert_message(
        conv_id,
        "assistant",
        content="The answer is 42.",
        reasoning_content="Let me think step by step: 6 * 7 = 42.",
    )

    forked_conv_id = await db.fork_conversation(conv_id, assistant_msg_id)
    assert forked_conv_id != conv_id

    forked_messages = await db.get_messages(forked_conv_id)
    assert len(forked_messages) == 2

    forked_assistant = next(m for m in forked_messages if m["role"] == "assistant")
    assert forked_assistant["content"] == "The answer is 42."
    assert forked_assistant["reasoning_content"] == "Let me think step by step: 6 * 7 = 42."


@pytest.mark.asyncio
async def test_fork_preserves_null_reasoning_content(db):
    """Forking must not break messages that have no reasoning_content (NULL)."""
    conv_id = await db.create_conversation("Normal chat", "gpt-5.5")
    await db.insert_message(conv_id, "user", content="Hello")
    assistant_msg_id = await db.insert_message(conv_id, "assistant", content="Hi there!")

    forked_conv_id = await db.fork_conversation(conv_id, assistant_msg_id)
    forked_messages = await db.get_messages(forked_conv_id)

    assert len(forked_messages) == 2
    forked_assistant = next(m for m in forked_messages if m["role"] == "assistant")
    assert forked_assistant["content"] == "Hi there!"
    assert forked_assistant["reasoning_content"] is None


@pytest.mark.asyncio
async def test_fork_copies_only_messages_up_to_fork_point(db):
    """Only messages at or before the fork point are included in the fork."""
    conv_id = await db.create_conversation("Long chat", "gpt-5.5")
    await db.insert_message(conv_id, "user", content="First")
    msg2 = await db.insert_message(conv_id, "assistant", content="Reply")
    await db.insert_message(conv_id, "user", content="After fork point — should not copy")

    forked_conv_id = await db.fork_conversation(conv_id, msg2)
    forked_messages = await db.get_messages(forked_conv_id)
    assert len(forked_messages) == 2
    assert all(m["content"] != "After fork point — should not copy" for m in forked_messages)


@pytest.mark.asyncio
async def test_fork_conversation_title(db):
    """Forked conversation gets a '(fork)' suffix on the title."""
    conv_id = await db.create_conversation("My Chat", "gpt-5.5")
    msg_id = await db.insert_message(conv_id, "user", content="Hi")

    forked_conv_id = await db.fork_conversation(conv_id, msg_id)
    convs = await db.list_conversations()
    forked = next(c for c in convs if c["id"] == forked_conv_id)
    assert "fork" in forked["title"].lower()
