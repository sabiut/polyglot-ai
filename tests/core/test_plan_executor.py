"""Tests for ``PlanExecutor`` — plan-step execution with tool calling."""

import json

from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.plan_executor import (
    EVT_PLAN_DONE,
    EVT_PLAN_STEP_COMPLETED,
    EVT_PLAN_STEP_STARTED,
    PlanExecutor,
)
from polyglot_ai.core.ai.plan_models import Plan, PlanStep, PlanStatus, PlanStepStatus
from polyglot_ai.core.bridge import EventBus


class FakeProvider:
    """Yields one scripted list of chunks per stream_chat() call."""

    def __init__(self, turns: list[list[StreamChunk]]) -> None:
        self._turns = list(turns)

    async def stream_chat(self, messages, model, tools=None):
        for chunk in self._turns.pop(0):
            yield chunk


class FakeRegistry:
    def __init__(self, auto_approved: bool = True) -> None:
        self._auto_approved = auto_approved
        self.executed: list[tuple[str, str]] = []

    def get_tool_definitions(self):
        return [{"name": "file_write"}]

    def is_auto_approved(self, tool_name, args=None):
        return self._auto_approved

    async def execute(self, tool_name, args_str):
        self.executed.append((tool_name, args_str))
        return "ok"


def _plan() -> Plan:
    plan = Plan(
        title="Test plan",
        summary="",
        steps=[PlanStep(index=0, title="Do the thing", description="desc")],
    )
    plan.approve_all()
    return plan


def _tool_call_turn(arguments: str, call_id: str = "") -> list[StreamChunk]:
    """One assistant turn that requests a single tool call."""
    tc = {"index": 0, "function": {"name": "file_write", "arguments": arguments}}
    if call_id:
        tc["id"] = call_id
    return [
        StreamChunk(tool_calls=[tc]),
        StreamChunk(finish_reason="tool_calls"),
    ]


def _text_turn(text: str = "done") -> list[StreamChunk]:
    return [StreamChunk(delta_content=text), StreamChunk(finish_reason="stop")]


def _executor(provider, registry, bus=None) -> PlanExecutor:
    return PlanExecutor(
        provider=provider,
        model_id="test-model",
        tool_registry=registry,
        event_bus=bus or EventBus(),
    )


async def test_approval_callback_receives_raw_json_string():
    # The chat panel forwards the second argument verbatim to
    # InlineApprovalCard, which json.loads()es it — so it must be the
    # raw JSON string, never the parsed dict.
    raw_args = '{"path": "a.py", "content": "x"}'
    provider = FakeProvider([_tool_call_turn(raw_args, call_id="call_abc"), _text_turn()])
    registry = FakeRegistry(auto_approved=False)
    received: list = []

    async def on_tool_approval(tool_name, arguments):
        received.append((tool_name, arguments))
        return True

    await _executor(provider, registry).execute(_plan(), on_tool_approval=on_tool_approval)

    assert received == [("file_write", raw_args)]
    tool_name, arguments = received[0]
    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"path": "a.py", "content": "x"}
    # Approval granted → the tool actually ran.
    assert registry.executed and registry.executed[0][0] == "file_write"


async def test_rejected_approval_fails_step_without_executing():
    provider = FakeProvider([_tool_call_turn('{"path": "a.py"}', call_id="call_abc")])
    registry = FakeRegistry(auto_approved=False)

    async def on_tool_approval(tool_name, arguments):
        return False

    plan = _plan()
    await _executor(provider, registry).execute(plan, on_tool_approval=on_tool_approval)

    assert registry.executed == []
    assert plan.steps[0].status == PlanStepStatus.FAILED
    assert "rejected" in (plan.steps[0].result or "")
    assert plan.status == PlanStatus.PAUSED


async def test_missing_tool_call_id_is_consistent_across_messages():
    # When the provider streams no id, the fallback id recorded in the
    # assistant message must equal the tool_call_id of the tool-result
    # message — a mismatch makes the next API request invalid.
    provider = FakeProvider([_tool_call_turn('{"path": "a.py"}'), _text_turn()])
    registry = FakeRegistry()
    executor = _executor(provider, registry)

    await executor.execute(_plan())

    assistant_msgs = [m for m in executor._messages if m.get("tool_calls")]
    tool_msgs = [m for m in executor._messages if m.get("role") == "tool"]
    assert len(assistant_msgs) == 1 and len(tool_msgs) == 1
    assistant_id = assistant_msgs[0]["tool_calls"][0]["id"]
    assert assistant_id  # never empty
    assert tool_msgs[0]["tool_call_id"] == assistant_id


async def test_provider_supplied_tool_call_id_is_preserved():
    provider = FakeProvider([_tool_call_turn('{"path": "a.py"}', call_id="call_xyz"), _text_turn()])
    registry = FakeRegistry()
    executor = _executor(provider, registry)

    await executor.execute(_plan())

    assistant_msgs = [m for m in executor._messages if m.get("tool_calls")]
    tool_msgs = [m for m in executor._messages if m.get("role") == "tool"]
    assert assistant_msgs[0]["tool_calls"][0]["id"] == "call_xyz"
    assert tool_msgs[0]["tool_call_id"] == "call_xyz"


async def test_lifecycle_events_emitted_on_provided_bus():
    provider = FakeProvider([_text_turn()])
    bus = EventBus()
    events: list[tuple[str, dict]] = []
    for evt in (EVT_PLAN_STEP_STARTED, EVT_PLAN_STEP_COMPLETED, EVT_PLAN_DONE):
        bus.subscribe(evt, lambda _evt=evt, **kw: events.append((_evt, kw)))

    plan = _plan()
    await _executor(provider, FakeRegistry(), bus=bus).execute(plan)

    assert [name for name, _ in events] == [
        EVT_PLAN_STEP_STARTED,
        EVT_PLAN_STEP_COMPLETED,
        EVT_PLAN_DONE,
    ]
    assert all(kw["plan"] is plan for _, kw in events)
    assert plan.status == PlanStatus.COMPLETED
    assert plan.steps[0].status == PlanStepStatus.COMPLETED
