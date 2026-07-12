"""Regression: a turn's messages must survive a Stop pressed during tool
execution.

The bug: when the model's first turn calls a tool with no accompanying
text (``full_content == ""``), the assistant Message (carrying the
tool_calls) is appended to the in-memory conversation but not yet
persisted. Hitting Stop while a tool ran propagated CancelledError out of
``_execute_tool_calls`` to a handler whose ``if full_content and ...``
guard was False, so ``_persist_conversation`` never ran. The turn — plus
the record of already-executed, side-effecting tool calls — vanished on
reload.

These tests drive ``_stream_response`` with a fake provider + tool
registry and a real (temp) database, cancel mid-tool-execution, and assert
the assistant turn was written to the DB.
"""

from __future__ import annotations

import asyncio

import pytest

from polyglot_ai.core.ai.models import Conversation, Message
from polyglot_ai.core.database import Database
from polyglot_ai.ui.panels.chat_panel import ChatPanel


class _FakeProvider:
    display_name = "Anthropic"

    def __init__(self, tool_name: str = "shell_exec") -> None:
        self._tool_name = tool_name
        self.followup_calls = 0

    async def stream_chat(self, *, messages, model, tools=None, system_prompt=None):
        # First call: emit a single tool call and NO text (the bug's
        # trigger). Subsequent (follow-up) calls: nothing — but the test
        # cancels before we get here.
        if self.followup_calls == 0:
            self.followup_calls += 1
            from polyglot_ai.core.ai.models import StreamChunk

            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": self._tool_name, "arguments": '{"command": "ls"}'},
                    }
                ]
            )
            return
        return
        yield  # pragma: no cover — makes this an async generator


class _BlockingRegistry:
    """Tool registry stub whose execute() blocks until released, so the
    test can cancel the task while a tool is 'running'."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def needs_approval(self, name, args=None):
        return False

    def is_auto_approved(self, name, args=None):
        return True

    async def execute(self, name, arguments):
        self.entered.set()
        await self.release.wait()  # blocks until the test releases it
        return "done"


class _ProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider_for_model(self, full_id):
        return self._provider, "claude-x"


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "t.db")
    await database.init()
    yield database
    await database.close()


def _make_panel(qtbot, db, provider, registry):
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel._db = db
    panel._provider_manager = _ProviderManager(provider)
    panel._tool_registry = registry
    panel._tools = []
    panel._mcp_client = None
    panel._context_builder = None
    panel._plan_mode = False
    panel._search_mode = False
    panel._active_task = None
    panel._task_manager = None
    panel._get_selected_model = lambda: ("anthropic:claude-x", "Claude X")
    conv = Conversation(title="t", model="anthropic:claude-x")
    conv.messages.append(Message(role="user", content="list files"))
    panel._current_conversation = conv
    panel._persisted_message_count = 0
    return panel, conv


@pytest.mark.asyncio
async def test_cancel_during_tool_execution_persists_turn(qtbot, db):
    provider = _FakeProvider()
    registry = _BlockingRegistry()
    panel, conv = _make_panel(qtbot, db, provider, registry)

    task = asyncio.ensure_future(panel._stream_response())
    # Wait until the tool is mid-execution, then Stop.
    await asyncio.wait_for(registry.entered.wait(), timeout=5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The conversation row + the user and assistant(tool_calls) messages
    # must be in the database, not just in memory.
    assert conv.id is not None, "conversation was never persisted"
    rows = await db.get_messages(conv.id)
    roles = [r["role"] for r in rows]
    assert "user" in roles
    assert "assistant" in roles, f"assistant turn dropped on cancel: {roles}"
    # The assistant row carries the tool call that actually ran.
    assistant_rows = [r for r in rows if r["role"] == "assistant"]
    assert any(r.get("tool_calls") for r in assistant_rows), "tool_calls not persisted"


@pytest.mark.asyncio
async def test_cancel_does_not_duplicate_assistant_turn(qtbot, db):
    """The committed assistant turn must be persisted exactly once — no
    second text-only assistant message appended by the cancel handler."""
    provider = _FakeProvider()
    registry = _BlockingRegistry()
    panel, conv = _make_panel(qtbot, db, provider, registry)

    task = asyncio.ensure_future(panel._stream_response())
    await asyncio.wait_for(registry.entered.wait(), timeout=5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    rows = await db.get_messages(conv.id)
    assistant_rows = [r for r in rows if r["role"] == "assistant"]
    assert len(assistant_rows) == 1, f"assistant turn duplicated: {len(assistant_rows)}"
