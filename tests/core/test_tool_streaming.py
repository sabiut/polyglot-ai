"""Tests for ``ToolCallAccumulator`` — streamed tool-call reassembly."""

from polyglot_ai.core.ai.tool_streaming import ToolCallAccumulator


def _chunk(index, *, id=None, name=None, args=None):
    """Build a one-element ``tool_calls`` list like a provider would yield."""
    tc = {"index": index, "function": {}}
    if id is not None:
        tc["id"] = id
    if name is not None:
        tc["function"]["name"] = name
    if args is not None:
        tc["function"]["arguments"] = args
    return [tc]


class TestToolCallAccumulator:
    def test_single_call_assembled_from_fragments(self):
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="call_1", name="file_read"))
        acc.add_chunk(_chunk(0, args='{"path":'))
        acc.add_chunk(_chunk(0, args='"a.py"}'))
        calls = acc.build()
        assert len(calls) == 1
        assert calls[0].id == "call_1"
        assert calls[0].function_name == "file_read"
        assert calls[0].arguments == '{"path":"a.py"}'

    def test_continuation_without_id_appends_to_open_call(self):
        # Later fragments carry no id — they must append to the open call,
        # not be mistaken for a new one.
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="call_1", name="shell_exec", args='{"cmd":'))
        acc.add_chunk(_chunk(0, args='"ls"}'))
        calls = acc.build()
        assert len(calls) == 1
        assert calls[0].arguments == '{"cmd":"ls"}'

    def test_multiple_calls_on_distinct_indices_keep_order(self):
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="a", name="file_read", args="{}"))
        acc.add_chunk(_chunk(1, id="b", name="git_status", args="{}"))
        calls = acc.build()
        assert [c.id for c in calls] == ["a", "b"]
        assert [c.function_name for c in calls] == ["file_read", "git_status"]

    def test_name_less_calls_are_dropped(self):
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="x", args="{}"))  # never gets a name
        assert acc.build() == []

    def test_empty_input_yields_no_calls(self):
        acc = ToolCallAccumulator()
        acc.add_chunk(None)
        acc.add_chunk([])
        assert acc.empty
        assert acc.build() == []

    def test_index_reuse_for_new_id_does_not_corrupt_args(self):
        # The hardening case: a misbehaving provider reuses index 0 for a
        # second, distinct call. The two calls' arguments must not merge.
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="first", name="file_read", args='{"a":1}'))
        acc.add_chunk(_chunk(0, id="second", name="git_status", args='{"b":2}'))
        calls = acc.build()
        assert {c.id for c in calls} == {"first", "second"}
        first = next(c for c in calls if c.id == "first")
        second = next(c for c in calls if c.id == "second")
        assert first.arguments == '{"a":1}'
        assert second.arguments == '{"b":2}'

    def test_summary_is_log_safe(self):
        acc = ToolCallAccumulator()
        acc.add_chunk(_chunk(0, id="a", name="file_read", args="123"))
        s = acc.summary()
        assert s[0] == {"id": "a", "name": "file_read", "args_len": 3}
        # The raw argument string must not appear in the summary.
        assert "123" not in str({k: v for k, v in s[0].items() if k != "args_len"})
