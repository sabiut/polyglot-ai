"""Tests for the Arduino AI tools.

Covers:

- Approval policy: read tools auto-approve, mutation tools require
  approval. Pinned to catch a future careless edit that flips a
  compile/upload tool into the auto-approve set.
- Standalone-mode dispatch: the tools work without a project being
  open in the IDE, like the rest of the panel.
- Tool definitions: every Arduino tool the dispatcher branches on
  has a matching entry in ``TOOL_DEFINITIONS``.
- ``arduino_get_state`` returns useful JSON whether or not the panel
  has published a snapshot.
- ``arduino_compile`` / ``arduino_upload`` short-circuit gracefully
  when no project is loaded (clear error, not a stack trace).

Doesn't fork ``arduino-cli`` or talk to a real board — those need
hardware and aren't useful in CI. The hardware paths are exercised
via the service tests.
"""

from __future__ import annotations

import asyncio
import json

from polyglot_ai.core import panel_state
from polyglot_ai.core.ai.tools import ToolRegistry
from polyglot_ai.core.ai.tools.arduino_tools import (
    arduino_compile,
    arduino_get_state,
    arduino_upload,
)
from polyglot_ai.core.ai.tools.definitions import (
    AUTO_APPROVE,
    REQUIRES_APPROVAL,
    TOOL_DEFINITIONS,
)


def _run(coro):
    # ``asyncio.run`` creates a fresh loop and closes it cleanly,
    # which keeps these tests independent of any sibling test
    # that already ran the global event loop to completion.
    return asyncio.run(coro)


class TestApprovalPolicy:
    """Read tools auto-approve; mutation tools never do."""

    def test_state_reads_auto_approve(self):
        assert "arduino_get_state" in AUTO_APPROVE
        assert "arduino_list_boards" in AUTO_APPROVE
        assert "arduino_get_state" not in REQUIRES_APPROVAL
        assert "arduino_list_boards" not in REQUIRES_APPROVAL

    def test_compile_and_upload_require_approval(self):
        # Pin the safety stance: a stray PR moving these into
        # AUTO_APPROVE would let the model flash hardware without
        # the user clicking through. The fail message names the
        # tool to make the regression obvious in CI logs.
        assert "arduino_compile" in REQUIRES_APPROVAL, "arduino_compile must require approval"
        assert "arduino_upload" in REQUIRES_APPROVAL, "arduino_upload must require approval"
        assert "arduino_compile" not in AUTO_APPROVE
        assert "arduino_upload" not in AUTO_APPROVE


class TestRegistryGate:
    """Standalone-mode dispatch lets the chat call these without an open project."""

    def test_registry_marks_arduino_tools_auto_approved_when_safe(self):
        reg = ToolRegistry()
        assert reg.is_auto_approved("arduino_get_state") is True
        assert reg.is_auto_approved("arduino_list_boards") is True
        # Mutations stay gated — even in standalone mode.
        assert reg.is_auto_approved("arduino_compile") is False
        assert reg.is_auto_approved("arduino_upload") is False
        assert reg.needs_approval("arduino_compile") is True
        assert reg.needs_approval("arduino_upload") is True


class TestToolDefinitions:
    """Every dispatched name must be advertised in TOOL_DEFINITIONS."""

    def _names(self) -> set[str]:
        return {d["function"]["name"] for d in TOOL_DEFINITIONS}

    def test_all_four_arduino_tools_have_definitions(self):
        names = self._names()
        for tool in (
            "arduino_get_state",
            "arduino_list_boards",
            "arduino_compile",
            "arduino_upload",
        ):
            assert tool in names, f"{tool} missing from TOOL_DEFINITIONS"


class TestGetStateNoSnapshot:
    """The tool must work even before the panel has published anything."""

    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def test_returns_loaded_false_with_explanatory_note(self):
        # No snapshot → JSON with ``loaded: false`` and a note that
        # tells the model what to do (open the panel). The model
        # should not have to handle the absence as a hard error.
        out = _run(arduino_get_state({}))
        data = json.loads(out)
        assert data["loaded"] is False
        assert "panel" in data["note"].lower()


class TestMutationsWithoutProject:
    """Compile / upload must fail gracefully when nothing is loaded."""

    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def test_compile_without_project_explains_what_to_do(self):
        out = _run(arduino_compile({}))
        # Plain-language error; mentions "load" / "panel" so the
        # model passes the right instruction back to the user.
        assert "no project" in out.lower() or "load" in out.lower()

    def test_upload_without_project_explains_what_to_do(self):
        out = _run(arduino_upload({}))
        assert "no project" in out.lower() or "load" in out.lower()


class TestScaffoldTools:
    """``arduino_load_starter`` and ``arduino_create_blank``."""

    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def test_list_starters_returns_json_with_known_slug(self):
        from polyglot_ai.core.ai.tools.arduino_tools import arduino_list_starters

        out = _run(arduino_list_starters({}))
        data = json.loads(out)
        slugs = {entry["slug"] for entry in data}
        assert "blink-cpp" in slugs

    def test_load_starter_unknown_slug(self):
        from polyglot_ai.core.ai.tools.arduino_tools import arduino_load_starter

        out = _run(arduino_load_starter({"slug": "no-such-thing"}))
        # Plain-language error AND lists the valid options so the
        # model can recover on the next turn.
        assert "Unknown starter" in out
        assert "blink-cpp" in out

    def test_load_starter_writes_files_and_publishes(self, tmp_path):
        from polyglot_ai.core.ai.tools.arduino_tools import arduino_load_starter

        out = _run(arduino_load_starter({"slug": "blink-cpp", "target_dir": str(tmp_path)}))
        assert "Loaded starter" in out
        assert (tmp_path / "blink" / "blink.ino").is_file()
        snapshot = panel_state.get_last_arduino_state()
        assert snapshot is not None and snapshot["loaded"] is True
        assert snapshot["entry_file"].endswith("blink.ino")
        assert snapshot["language"] == "cpp"

    def test_create_blank_writes_boilerplate_and_publishes(self, tmp_path):
        from polyglot_ai.core.ai.tools.arduino_tools import arduino_create_blank

        out = _run(
            arduino_create_blank(
                {
                    "name": "demo",
                    "language": "cpp",
                    "target_dir": str(tmp_path),
                }
            )
        )
        assert "Created blank" in out
        entry = tmp_path / "demo" / "demo.ino"
        assert entry.is_file()
        snapshot = panel_state.get_last_arduino_state()
        assert snapshot is not None
        assert snapshot["entry_file"] == str(entry)
        # Boilerplate, not real code — the AI should know.
        assert snapshot["ready_to_upload"] is False

    def test_create_blank_rejects_bad_language(self, tmp_path):
        from polyglot_ai.core.ai.tools.arduino_tools import arduino_create_blank

        out = _run(
            arduino_create_blank({"name": "demo", "language": "rust", "target_dir": str(tmp_path)})
        )
        assert "language" in out.lower()
        # Bad input must NOT publish stale state.
        assert panel_state.get_last_arduino_state() is None


class TestCompileSnapshotHandling:
    """When a snapshot says C++, compile picks the right path."""

    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def test_python_project_redirected_to_upload(self, tmp_path):
        # Even with a project loaded, compile only makes sense for
        # C++. For a Python project, the tool should explain that
        # rather than try to fork arduino-cli.
        py = tmp_path / "main.py"
        py.write_text("print('hi')\n")
        panel_state.set_last_arduino_state(
            {
                "loaded": True,
                "entry_file": str(py),
                "project_dir": str(tmp_path),
                "language": "micropython",
                "language_display": "Python (MicroPython)",
                "ready_to_upload": False,
                "blocker": "no board",
                "code": py.read_text(),
                "board": {
                    "display_name": "ESP32",
                    "fqbn": "esp32:esp32:esp32",
                    "port": "/dev/ttyUSB0",
                },
                "toolchains": {},
            }
        )
        out = _run(arduino_compile({}))
        assert "compile" in out.lower()
        assert "python" in out.lower() or "micropython" in out.lower()
        # And the message points the model at the right tool.
        assert "arduino_upload" in out
