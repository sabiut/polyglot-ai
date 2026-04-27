"""Tests for Database."""

import pytest


@pytest.mark.asyncio
async def test_schema_created(db):
    row = await db.fetchone("SELECT MAX(version) as v FROM schema_version")
    assert row["v"] == 5


@pytest.mark.asyncio
async def test_settings_crud(db):
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        ("test_key", '"test_value"'),
    )
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", ("test_key",))
    assert row["value"] == '"test_value"'


@pytest.mark.asyncio
async def test_conversation_and_messages(db):
    conv_id = await db.create_conversation("Test Chat", "gpt-4o")
    assert conv_id is not None

    msg_id = await db.insert_message(conv_id, "user", content="Hello")
    assert msg_id is not None

    messages = await db.get_messages(conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_conversation_list(db):
    await db.create_conversation("Chat 1", "gpt-4o")
    await db.create_conversation("Chat 2", "gpt-4o")
    convs = await db.list_conversations()
    assert len(convs) == 2


@pytest.mark.asyncio
async def test_message_with_tool_calls(db):
    conv_id = await db.create_conversation("Test", "gpt-4o")
    tool_calls = [{"id": "call_1", "function": {"name": "file_read", "arguments": "{}"}}]
    await db.insert_message(conv_id, "assistant", tool_calls=tool_calls)
    messages = await db.get_messages(conv_id)
    assert messages[0]["tool_calls"] == tool_calls


@pytest.mark.asyncio
async def test_reasoning_content_round_trip(db):
    """Thinking-mode models (DeepSeek R1 / V4-pro) emit reasoning_content
    alongside the visible content. The DB must persist it on the assistant
    turn so the next API call can echo it back — DeepSeek rejects the
    request otherwise.
    """
    conv_id = await db.create_conversation("Test", "deepseek-v4-pro")
    await db.insert_message(
        conv_id,
        "assistant",
        content="The answer is 42.",
        reasoning_content="Let me think... 6 * 7 = 42.",
    )
    messages = await db.get_messages(conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "The answer is 42."
    assert messages[0]["reasoning_content"] == "Let me think... 6 * 7 = 42."


@pytest.mark.asyncio
async def test_reasoning_content_defaults_to_null(db):
    """Messages from non-thinking-mode providers should not carry a
    spurious empty string for reasoning_content — the DB column is
    nullable and the absent case must round-trip as None.
    """
    conv_id = await db.create_conversation("Test", "gpt-5.5")
    await db.insert_message(conv_id, "assistant", content="Hello")
    messages = await db.get_messages(conv_id)
    assert messages[0]["reasoning_content"] is None


@pytest.mark.asyncio
async def test_audit_log(db):
    await db.log_audit("test_event", {"key": "value"})
    rows = await db.fetchall("SELECT * FROM audit_log")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "test_event"
