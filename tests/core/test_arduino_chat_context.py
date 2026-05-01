"""Tests for the Arduino → AI chat context bridge.

Pin two things the chat panel relies on:

- ``panel_state.set_last_arduino_state`` / ``get_last_arduino_state``
  round-trip cleanly and survive a ``clear()``.
- ``ContextBuilder._render_panel_state_block`` includes the Arduino
  slice when a snapshot is published, with the project entry, the
  board status, the upload-readiness line, and a fenced code block
  when source is available.

Doesn't fork the panel itself — the snapshot shape is pure data,
so we publish dicts directly. That keeps the test independent of
Qt and the service.
"""

from __future__ import annotations

from polyglot_ai.core import panel_state
from polyglot_ai.core.ai.context import ContextBuilder


class TestPanelStateRoundTrip:
    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def test_round_trip(self):
        snap = {"loaded": False, "toolchains": {}, "board": None}
        panel_state.set_last_arduino_state(snap)
        out = panel_state.get_last_arduino_state()
        assert out == snap
        # Returned dict must be a copy — mutating it can't affect
        # the next consumer.
        out["loaded"] = True
        again = panel_state.get_last_arduino_state()
        assert again is not None and again["loaded"] is False

    def test_clear_resets_arduino_state(self):
        panel_state.set_last_arduino_state({"loaded": True})
        panel_state.clear()
        assert panel_state.get_last_arduino_state() is None


class TestContextRenderArduinoBlock:
    def setup_method(self):
        panel_state.clear()

    def teardown_method(self):
        panel_state.clear()

    def _build(self, snapshot: dict) -> str:
        panel_state.set_last_arduino_state(snapshot)
        return ContextBuilder().build_system_prompt()

    def test_empty_state_renders_no_project_line(self):
        prompt = self._build(
            {
                "loaded": False,
                "toolchains": {
                    "can_cpp": True,
                    "can_micropython": False,
                    "can_circuitpython": False,
                },
                "board": None,
            }
        )
        assert "Arduino panel:" in prompt
        assert "No project loaded yet" in prompt

    def test_loaded_state_includes_entry_language_board(self):
        prompt = self._build(
            {
                "loaded": True,
                "entry_file": "/home/sam/projects/blink/blink.ino",
                "project_dir": "/home/sam/projects/blink",
                "language": "cpp",
                "language_display": "C++",
                "ready_to_upload": True,
                "blocker": None,
                "code": "void setup() {}\nvoid loop() {}\n",
                "board": {
                    "display_name": "Arduino Uno",
                    "fqbn": "arduino:avr:uno",
                    "port": "/dev/ttyUSB0",
                },
                "toolchains": {
                    "can_cpp": True,
                    "can_micropython": False,
                    "can_circuitpython": False,
                },
            }
        )
        # The block must surface every dimension the model needs to
        # answer "what's loaded / what's connected / fix this code".
        assert "blink.ino" in prompt
        assert "C++" in prompt
        assert "Arduino Uno" in prompt
        assert "/dev/ttyUSB0" in prompt
        assert "ready to upload" in prompt
        # Code goes in a fenced block so the model treats it as code,
        # not prose.
        assert "```" in prompt
        assert "void setup()" in prompt

    def test_blocker_shows_in_status_line(self):
        prompt = self._build(
            {
                "loaded": True,
                "entry_file": "/x/y.ino",
                "project_dir": "/x",
                "language": "cpp",
                "language_display": "C++",
                "ready_to_upload": False,
                "blocker": "Plug in your board first.",
                "code": "",
                "board": None,
                "toolchains": {
                    "can_cpp": True,
                    "can_micropython": False,
                    "can_circuitpython": False,
                },
            }
        )
        assert "not ready" in prompt
        assert "Plug in your board first" in prompt
        # No board → friendly explanation, not a stray "None" leak.
        assert "none yet" in prompt

    def test_no_arduino_state_produces_no_arduino_block(self):
        # Sanity: with nothing published, the panel-state block
        # should be empty (no review / workflow / arduino).
        prompt = ContextBuilder().build_system_prompt()
        assert "Arduino panel" not in prompt
        assert "--- PANEL STATE ---" not in prompt

    def test_buffer_source_warns_about_unsaved_edits(self):
        prompt = self._build(
            {
                "loaded": True,
                "entry_file": "/x/y/blink.ino",
                "project_dir": "/x/y",
                "language": "cpp",
                "language_display": "C++",
                "ready_to_upload": True,
                "blocker": None,
                "code": "void setup() { /* edited */ }\n",
                # The new field — flips the prompt to warn about
                # unsaved edits and tells the model to nudge the
                # user to save before uploading.
                "code_source": "buffer",
                "board": None,
                "toolchains": {},
            }
        )
        assert "unsaved" in prompt.lower()
        assert "Ctrl+S" in prompt or "save" in prompt.lower()

    def test_disk_source_does_not_warn(self):
        prompt = self._build(
            {
                "loaded": True,
                "entry_file": "/x/y/blink.ino",
                "project_dir": "/x/y",
                "language": "cpp",
                "language_display": "C++",
                "ready_to_upload": True,
                "blocker": None,
                "code": "void setup(){}\n",
                "code_source": "disk",
                "board": None,
                "toolchains": {},
            }
        )
        assert "unsaved" not in prompt.lower()
