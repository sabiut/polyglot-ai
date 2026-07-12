"""Incremental assembly of streamed tool calls.

Providers stream a tool call as a sequence of partial chunks: the first
chunk for a call carries its ``id`` and function ``name``, and later
chunks append fragments of the JSON ``arguments`` string. Every chunk is
tagged with an integer ``index`` that ties the fragments of one call
together.

This accumulator centralises the reassembly that used to be copy-pasted
into :class:`~polyglot_ai.core.ai.agent.AgentLoop` and — twice — into the
chat panel's streaming loop. Keeping it in one place means the fragile
fragment-concatenation logic is implemented, and hardened, exactly once.
"""

from __future__ import annotations

import logging

from polyglot_ai.core.ai.models import ToolCall

logger = logging.getLogger(__name__)


class ToolCallAccumulator:
    """Reassembles streamed tool-call fragments into :class:`ToolCall` objects."""

    def __init__(self) -> None:
        # Insertion-ordered so the assembled calls keep provider order.
        self._data: dict[int, dict] = {}

    def add_chunk(self, tool_calls: list[dict] | None) -> None:
        """Fold one stream chunk's ``tool_calls`` list into the buffer.

        Accepts ``None``/empty for chunks that carry no tool-call data, so
        callers can pass ``chunk.tool_calls`` unconditionally.
        """
        if not tool_calls:
            return
        for tc in tool_calls:
            idx = tc.get("index", 0)
            incoming_id = tc.get("id") or ""
            entry = self._data.get(idx)

            # Hardening: a well-behaved provider sends a call's ``id`` once,
            # on its opening chunk, and never reuses an ``index`` for a
            # different call. If a *different* non-empty id lands on an
            # index we're already accumulating, the provider has reused the
            # slot — concatenating the new call's argument fragments onto
            # the old call would silently corrupt both. Detect it and give
            # the new call its own slot instead of trusting the index.
            if entry is not None and incoming_id and entry["id"] and incoming_id != entry["id"]:
                logger.warning(
                    "Tool-call index %s reused for a new id (%s, had %s) — "
                    "allocating a separate slot to avoid argument corruption",
                    idx,
                    incoming_id,
                    entry["id"],
                )
                idx = self._next_free_index()
                entry = None

            if entry is None:
                entry = {"id": incoming_id, "function": {"name": "", "arguments": ""}}
                self._data[idx] = entry

            if incoming_id:
                entry["id"] = incoming_id
            func = tc.get("function") or {}
            if func.get("name"):
                entry["function"]["name"] = func["name"]
            if func.get("arguments"):
                entry["function"]["arguments"] += func["arguments"]

    def _next_free_index(self) -> int:
        # Synthetic index above every key in use, so a reused-slot call
        # gets its own entry without clobbering anything already buffered.
        return max(self._data) + 1 if self._data else 0

    @property
    def empty(self) -> bool:
        return not self._data

    def summary(self) -> dict[int, dict]:
        """Compact, log-safe view: id, name, and argument length per call.

        Deliberately omits the raw ``arguments`` string, which can contain
        user data, so the result is safe to drop straight into a log line.
        """
        return {
            idx: {
                "id": e["id"],
                "name": e["function"]["name"],
                "args_len": len(e["function"]["arguments"]),
            }
            for idx, e in self._data.items()
        }

    def build(self) -> list[ToolCall]:
        """Materialise the buffered fragments into :class:`ToolCall` objects.

        Calls that never received a function name are dropped — a name-less
        tool call can't be dispatched and is always a provider artifact.
        """
        return [
            ToolCall(
                id=e["id"],
                function_name=e["function"]["name"],
                arguments=e["function"]["arguments"],
            )
            for e in self._data.values()
            if e["function"]["name"]
        ]
