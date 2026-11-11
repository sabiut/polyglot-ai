"""The chat agent loop: how many tool rounds the AI gets per message.

Before: ``_stream_followup`` had a ``_depth > 5`` guard but nothing ever
passed ``_depth``, so the loop recursed without limit until Python's
recursion limit silently ended it (~480 rounds). Now the loop is
iterative, stops at ``ai.max_tool_rounds`` (default 50) with a Continue
button, and pauses if the model repeats the identical call.
"""

from __future__ import annotations

import asyncio

import pytest

from polyglot_ai.core.ai.models import Conversation, Message, StreamChunk
from polyglot_ai.core.settings import DEFAULTS
from polyglot_ai.ui.panels.chat_panel import ChatPanel


class _ToolLoopProvider:
    """Asks for a tool on every turn until ``stop_after`` calls, then answers."""

    display_name = "Fake"

    def __init__(self, stop_after: int, *, same_args: bool = False) -> None:
        self.calls = 0
        self.stop_after = stop_after
        self.same_args = same_args

    async def stream_chat(self, *, messages, model, tools=None, system_prompt=None, **kw):
        self.calls += 1
        if self.calls < self.stop_after:
            path = "a.py" if self.same_args else f"f{self.calls}.py"
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": f"call_{self.calls}",
                        "function": {"name": "file_read", "arguments": f'{{"path": "{path}"}}'},
                    }
                ]
            )
        else:
            yield StreamChunk(delta_content="All done.")


class _Registry:
    def __init__(self) -> None:
        self.executed = 0

    def needs_approval(self, name, args=None):
        return False

    def is_auto_approved(self, name, args=None):
        return True

    async def execute(self, name, arguments):
        self.executed += 1
        return "contents"


class _ProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider_for_model(self, full_id):
        return self._provider, "m"


def _make_panel(qtbot, provider, registry, max_rounds=None):
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel._db = None
    panel._provider_manager = _ProviderManager(provider)
    panel._tool_registry = registry
    panel._tools = []
    panel._mcp_client = None
    panel._context_builder = None
    panel._plan_mode = False
    panel._search_mode = False
    panel._active_task = None
    panel._task_manager = None
    panel._get_selected_model = lambda: ("fake:m", "M")
    if max_rounds is not None:
        original = panel._setting
        panel._setting = lambda key: max_rounds if key == "ai.max_tool_rounds" else original(key)
    conv = Conversation(title="t", model="fake:m")
    conv.messages.append(Message(role="user", content="go"))
    panel._current_conversation = conv
    return panel, conv


def _continue_button(panel):
    from PyQt6.QtWidgets import QPushButton

    buttons = panel.findChildren(QPushButton, "agentContinueBtn")
    return buttons[-1] if buttons else None


def test_default_budget_is_generous():
    assert DEFAULTS["ai.max_tool_rounds"] == 50


@pytest.mark.asyncio
async def test_finishes_when_the_model_stops_calling_tools(qtbot):
    provider = _ToolLoopProvider(stop_after=12)
    registry = _Registry()
    panel, conv = _make_panel(qtbot, provider, registry)
    await panel._stream_response()
    assert registry.executed == 11  # well past the old intended cap of 6
    assert provider.calls == 12
    assert conv.messages[-1].role == "assistant"
    assert conv.messages[-1].content == "All done."
    assert _continue_button(panel) is None
    assert panel._streaming is False


@pytest.mark.asyncio
async def test_pauses_at_the_budget_with_a_continue_button(qtbot):
    provider = _ToolLoopProvider(stop_after=1000)
    registry = _Registry()
    panel, conv = _make_panel(qtbot, provider, registry, max_rounds=8)
    await panel._stream_response()
    assert registry.executed == 8
    # First turn + 7 follow-ups; the 8th round's results wait for Continue.
    assert provider.calls == 8
    assert conv.messages[-1].role == "tool"
    btn = _continue_button(panel)
    assert btn is not None and btn.isEnabled()
    assert "8 more rounds" in btn.text()
    assert panel._streaming is False


@pytest.mark.asyncio
async def test_continue_runs_another_budget(qtbot):
    provider = _ToolLoopProvider(stop_after=13)
    registry = _Registry()
    panel, conv = _make_panel(qtbot, provider, registry, max_rounds=8)
    await panel._stream_response()
    assert registry.executed == 8

    _continue_button(panel).click()
    assert panel._streaming is True
    await panel._stream_task
    assert registry.executed == 12
    assert conv.messages[-1].content == "All done."
    assert panel._streaming is False
    assert panel._stream_task is None


@pytest.mark.asyncio
async def test_continue_is_refused_after_a_new_message(qtbot):
    provider = _ToolLoopProvider(stop_after=1000)
    panel, conv = _make_panel(qtbot, provider, _Registry(), max_rounds=5)
    await panel._stream_response()
    conv.messages.append(Message(role="user", content="actually, stop"))
    btn = _continue_button(panel)
    btn.click()
    assert panel._stream_task is None
    assert not btn.isEnabled()
    assert btn.text() == "Nothing to continue"


@pytest.mark.asyncio
async def test_identical_repeated_calls_pause_the_loop(qtbot):
    provider = _ToolLoopProvider(stop_after=1000, same_args=True)
    registry = _Registry()
    panel, conv = _make_panel(qtbot, provider, registry, max_rounds=50)
    await panel._stream_response()
    assert registry.executed == ChatPanel._REPEAT_LIMIT
    assert _continue_button(panel) is not None


@pytest.mark.asyncio
async def test_long_runs_no_longer_recurse(qtbot):
    """300 rounds would have nested ~900 frames under the old recursion."""
    provider = _ToolLoopProvider(stop_after=301)
    registry = _Registry()
    panel, conv = _make_panel(qtbot, provider, registry, max_rounds=500)
    await panel._stream_response()
    assert registry.executed == 300
    assert conv.messages[-1].content == "All done."


@pytest.mark.asyncio
async def test_followup_failure_is_shown_not_swallowed(qtbot):
    class _FailingFollowup(_ToolLoopProvider):
        async def stream_chat(self, **kw):
            self.calls += 1
            if self.calls == 1:
                yield StreamChunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "file_read", "arguments": '{"path": "a"}'},
                        }
                    ]
                )
                return
            raise RuntimeError("rate limited")
            yield  # pragma: no cover

    panel, _conv = _make_panel(qtbot, _FailingFollowup(stop_after=99), _Registry())
    shown = []
    panel._add_system_message = shown.append
    await panel._stream_response()
    assert any("follow-up failed" in s and "rate limited" in s for s in shown)
    assert panel._streaming is False


@pytest.mark.asyncio
async def test_stop_during_a_long_run_ends_cleanly(qtbot):
    class _SlowRegistry(_Registry):
        async def execute(self, name, arguments):
            self.executed += 1
            if self.executed == 4:
                await asyncio.sleep(10)
            return "contents"

    provider = _ToolLoopProvider(stop_after=1000)
    registry = _SlowRegistry()
    panel, conv = _make_panel(qtbot, provider, registry)
    panel._stream_task = asyncio.ensure_future(panel._stream_response())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if registry.executed == 4:
            break
    panel._stop_generation()
    await asyncio.wait([panel._stream_task], timeout=5)
    assert registry.executed == 4
    assert panel._streaming is False


@pytest.mark.asyncio
async def test_aws_call_is_approved_and_run_with_the_same_pinned_arguments(qtbot):
    """The AWS panel's profile/region are filled in once, before the
    approval card, so the card names the account and the command that
    runs is the one that was approved — even if the selection moves on
    while the card is waiting."""
    import json

    from polyglot_ai.core import panel_state

    class _AwsProvider:
        display_name = "Fake"
        calls = 0

        async def stream_chat(self, *, messages, model, tools=None, system_prompt=None, **kw):
            self.calls += 1
            if self.calls == 1:
                call = {"name": "aws_cli", "arguments": '{"command": "ec2 stop-instances"}'}
                yield StreamChunk(tool_calls=[{"index": 0, "id": "call_1", "function": call}])
            else:
                yield StreamChunk(delta_content="Stopped.")

    class _Gated(_Registry):
        ran: list[str] = []

        def needs_approval(self, name, args=None):
            return True

        async def execute(self, name, arguments):
            self.ran.append(arguments)
            return "{}"

    registry = _Gated()
    panel, conv = _make_panel(qtbot, _AwsProvider(), registry)
    shown = []

    async def approve(tool_name, arguments):
        shown.append(arguments)
        panel_state.set_aws_context("prod", "us-east-1")  # user switches while it waits
        return True

    panel._request_tool_approval = approve
    panel_state.set_aws_context("staging", "eu-west-1")
    try:
        await panel._stream_response()
    finally:
        panel_state.clear()

    assert json.loads(shown[0]) == {
        "command": "ec2 stop-instances",
        "profile": "staging",
        "region": "eu-west-1",
    }
    assert registry.ran == shown  # exactly what was approved
