"""Tests for the ``terminal_read`` tool and its panel_state plumbing."""

from __future__ import annotations

import json

import pytest

from polyglot_ai.core import panel_state
from polyglot_ai.core.ai.tools.panel_tools import _TERMINAL_MAX_CHARS, terminal_read


@pytest.fixture(autouse=True)
def clean_panel_state():
    panel_state.clear()
    yield
    panel_state.clear()


@pytest.mark.asyncio
async def test_unavailable_when_no_reader_registered() -> None:
    result = json.loads(await terminal_read({}))
    assert result["available"] is False
    assert "No terminal" in result["message"]


@pytest.mark.asyncio
async def test_unavailable_when_reader_returns_none() -> None:
    panel_state.set_terminal_reader(lambda: None)
    result = json.loads(await terminal_read({}))
    assert result["available"] is False


@pytest.mark.asyncio
async def test_returns_full_buffer_when_under_limit() -> None:
    panel_state.set_terminal_reader(lambda: "$ ls\nfoo.py\nbar.py")
    result = json.loads(await terminal_read({}))
    assert result["available"] is True
    assert result["text"] == "$ ls\nfoo.py\nbar.py"
    assert result["total_lines"] == 3
    assert result["returned_lines"] == 3
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_tails_last_n_lines() -> None:
    buffer = "\n".join(f"line-{i}" for i in range(300))
    panel_state.set_terminal_reader(lambda: buffer)
    result = json.loads(await terminal_read({"lines": 5}))
    assert result["text"].splitlines() == [f"line-{i}" for i in range(295, 300)]
    assert result["total_lines"] == 300
    assert result["returned_lines"] == 5
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_default_is_100_lines_and_bad_args_tolerated() -> None:
    buffer = "\n".join(f"line-{i}" for i in range(250))
    panel_state.set_terminal_reader(lambda: buffer)
    result = json.loads(await terminal_read({"lines": "not-a-number"}))
    assert result["returned_lines"] == 100
    assert result["text"].splitlines()[0] == "line-150"


@pytest.mark.asyncio
async def test_lines_capped_at_1000() -> None:
    buffer = "\n".join(f"line-{i}" for i in range(1500))
    panel_state.set_terminal_reader(lambda: buffer)
    result = json.loads(await terminal_read({"lines": 99999}))
    assert result["returned_lines"] == 1000


@pytest.mark.asyncio
async def test_char_cap_keeps_newest_output() -> None:
    # One enormous "line" (e.g. a progress bar redrawn in place) —
    # the char cap must kick in even though the line count is tiny.
    buffer = "x" * (_TERMINAL_MAX_CHARS * 2) + "END"
    panel_state.set_terminal_reader(lambda: buffer)
    result = json.loads(await terminal_read({}))
    assert len(result["text"]) == _TERMINAL_MAX_CHARS
    assert result["text"].endswith("END")  # tail-biased: newest survives
    assert result["truncated"] is True


def test_reader_registration_roundtrip() -> None:
    assert panel_state.read_terminal() is None
    panel_state.set_terminal_reader(lambda: "hello")
    assert panel_state.read_terminal() == "hello"
    panel_state.set_terminal_reader(None)
    assert panel_state.read_terminal() is None


def test_terminal_read_is_registered() -> None:
    """The tool is defined, auto-approved, and standalone-dispatchable."""
    from polyglot_ai.core.ai.tools import _STANDALONE_TOOL_NAMES
    from polyglot_ai.core.ai.tools.definitions import AUTO_APPROVE, TOOL_DEFINITIONS

    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert "terminal_read" in names
    assert "terminal_read" in AUTO_APPROVE
    assert "terminal_read" in _STANDALONE_TOOL_NAMES
