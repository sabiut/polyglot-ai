"""UI tests for the Video Editor panel.

Covers the Qt-dependent surface that the helper tests in
``test_video_helpers.py`` can't reach:

* chip clicks prefill the prompt textarea
* the readiness state machine (no input / missing prompt / no
  ffmpeg → button disabled with the right tooltip)
* drag-drop accepts video URLs, ignores other payloads
* the prompt composer builds a well-formed message with Source
  info, paths, and the user's natural-language edit
* swallowed-exception fixes — main-window-less environments don't
  crash _raise_chat_window

These tests use ``pytest-qt``'s ``qtbot`` fixture, matching the
pattern in ``tests/ui/test_web_tests_view.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from polyglot_ai.ui.panels.video_panel import (
    QUICK_CHIPS,
    VideoMetadata,
    VideoPanel,
)


@pytest.fixture
def panel(qtbot):
    p = VideoPanel()
    qtbot.addWidget(p)
    p.show()
    return p


# ── Quick-action chips ──────────────────────────────────────────────


def test_chip_fills_prompt_textarea(panel):
    """Clicking a chip replaces the prompt with the chip's template.

    The unit-level test drives the slot directly because the chip
    button wiring (lambda → ``_apply_chip``) is mechanical and the
    behaviour we actually care about — "prompt textarea contains the
    template after click" — is fully observable through this entry
    point. The full button → slot connection is exercised by
    ``test_chip_button_signal_fires_apply_chip`` below.
    """
    _, _label, template = QUICK_CHIPS[0]  # First chip — Trim
    panel._apply_chip(template)
    assert panel._prompt_input.toPlainText() == template


def test_chip_button_signal_fires_apply_chip(panel, qtbot):
    """End-to-end: simulate the real user click on the chip QPushButton
    and assert the prompt textarea ended up with the chip's template.
    Catches a regression where someone disconnects the lambda or
    introduces a closure bug that captures the wrong template.
    """
    from PyQt6.QtWidgets import QPushButton

    chip_label_to_template = {label: template for _, label, template in QUICK_CHIPS}
    # The chip buttons live as QPushButton descendants of the panel
    # whose text contains the chip's label.
    found: dict[str, QPushButton] = {}
    for btn in panel.findChildren(QPushButton):
        for label in chip_label_to_template:
            if label in btn.text():
                found[label] = btn

    assert len(found) == len(QUICK_CHIPS), (
        f"expected {len(QUICK_CHIPS)} chip buttons, found {len(found)}: {list(found.keys())}"
    )

    # Pick one in the middle — covers the loop-closure late-binding
    # trap that would always set the prompt to the last chip's value.
    target_label = list(chip_label_to_template.keys())[2]
    qtbot.mouseClick(found[target_label], Qt.MouseButton.LeftButton)
    assert panel._prompt_input.toPlainText() == chip_label_to_template[target_label]


def test_chip_replaces_prior_prompt_contents(panel):
    """Chip click is a "replace, not append" operation. Two chip
    clicks in a row must leave only the second template, not a
    concatenation."""
    _, _, t1 = QUICK_CHIPS[0]
    _, _, t2 = QUICK_CHIPS[1]

    panel._apply_chip(t1)
    panel._apply_chip(t2)
    assert panel._prompt_input.toPlainText() == t2


def test_chip_overrides_user_typed_text(panel):
    """If the user typed something and then clicked a chip, the chip
    template wins. The chip is an intentional "give me a starting
    point" action — the user can edit further if they want both."""
    panel._prompt_input.setPlainText("some half-typed thought")
    _, _, template = QUICK_CHIPS[0]
    panel._apply_chip(template)
    assert panel._prompt_input.toPlainText() == template


def test_chip_focus_lands_in_prompt_textarea(panel):
    """After a chip click the prompt textarea has focus, so the user
    can immediately type follow-up text without an extra mouse move."""
    _, _, template = QUICK_CHIPS[0]
    panel._apply_chip(template)
    assert panel._prompt_input.hasFocus()


def test_every_chip_template_is_usable(panel):
    """Every shipped chip must produce a non-empty prompt and trigger
    a readiness recompute. Pinning this catches a regression where
    someone adds a chip with an empty template by mistake."""
    fake_video = Path("/tmp/clip.mp4")
    # Patch is_file so the readiness check passes without a real file
    with (
        patch.object(Path, "is_file", return_value=True),
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffmpeg",
        ),
    ):
        panel._input_path = fake_video
        for _, label, template in QUICK_CHIPS:
            panel._apply_chip(template)
            assert panel._prompt_input.toPlainText() == template
            ready, _ = panel._readiness()
            assert ready, f"chip {label!r} produced an unworkable prompt — _readiness rejected it"


# ── Readiness state machine ────────────────────────────────────────


def test_readiness_rejects_no_input(panel):
    ready, reason = panel._readiness()
    assert ready is False
    assert "pick a video" in reason.lower()


def test_readiness_rejects_missing_file(panel, tmp_path):
    panel._input_path = tmp_path / "vanished.mp4"  # doesn't exist
    ready, reason = panel._readiness()
    assert ready is False
    assert "can't find" in reason.lower()


def test_readiness_rejects_empty_prompt(panel, tmp_path):
    real = tmp_path / "clip.mp4"
    real.write_bytes(b"\x00")
    panel._input_path = real
    panel._prompt_input.setPlainText("")
    with patch(
        "polyglot_ai.ui.panels.video_panel.find_executable",
        return_value="/usr/bin/ffmpeg",
    ):
        ready, reason = panel._readiness()
    assert ready is False
    assert "describe" in reason.lower()


def test_readiness_rejects_missing_ffmpeg(panel, tmp_path):
    real = tmp_path / "clip.mp4"
    real.write_bytes(b"\x00")
    panel._input_path = real
    panel._prompt_input.setPlainText("Convert to MP4")
    with patch(
        "polyglot_ai.ui.panels.video_panel.find_executable",
        return_value=None,
    ):
        ready, reason = panel._readiness()
    assert ready is False
    assert "ffmpeg" in reason.lower()


def test_readiness_accepts_full_setup(panel, tmp_path):
    real = tmp_path / "clip.mp4"
    real.write_bytes(b"\x00")
    panel._input_path = real
    panel._prompt_input.setPlainText("Convert to MP4")
    with patch(
        "polyglot_ai.ui.panels.video_panel.find_executable",
        return_value="/usr/bin/ffmpeg",
    ):
        ready, reason = panel._readiness()
    assert ready is True
    assert reason == ""


# ── Drag-and-drop ──────────────────────────────────────────────────


def _make_drag_event(panel, urls, event_cls):
    """Build a Qt drag/drop event with given local-file URLs.

    Real Qt drag events carry a ``QMimeData`` payload that's awkward
    to construct directly; the panel only reads ``mimeData()``, so
    a duck-typed mock is sufficient and avoids depending on
    private Qt drag-API details that change between minor versions.
    """
    mime = MagicMock()
    mime.hasUrls.return_value = bool(urls)
    qurls = []
    for u in urls:
        qmock = MagicMock()
        qmock.toLocalFile.return_value = u
        qurls.append(qmock)
    mime.urls.return_value = qurls

    ev = MagicMock(spec=event_cls)
    ev.mimeData.return_value = mime
    return ev


def test_drag_enter_accepts_video_url(panel, tmp_path):
    from PyQt6.QtGui import QDragEnterEvent

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    event = _make_drag_event(panel, [str(f)], QDragEnterEvent)
    panel.dragEnterEvent(event)
    event.acceptProposedAction.assert_called_once()
    event.ignore.assert_not_called()


def test_drag_enter_rejects_pdf_url(panel, tmp_path):
    from PyQt6.QtGui import QDragEnterEvent

    f = tmp_path / "notes.pdf"
    f.write_bytes(b"\x00")
    event = _make_drag_event(panel, [str(f)], QDragEnterEvent)
    panel.dragEnterEvent(event)
    event.acceptProposedAction.assert_not_called()
    event.ignore.assert_called_once()


def test_drag_enter_with_no_urls_calls_ignore(panel):
    """Regression test — earlier code returned early without
    ignoring, leaving Qt's drop indicator stuck on screen."""
    from PyQt6.QtGui import QDragEnterEvent

    event = _make_drag_event(panel, [], QDragEnterEvent)
    panel.dragEnterEvent(event)
    event.ignore.assert_called_once()


def test_drop_applies_video_url(panel, tmp_path):
    from PyQt6.QtGui import QDropEvent

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    event = _make_drag_event(panel, [str(f)], QDropEvent)
    # Patch out the background probe — we only care that the drop
    # set the input path, not that the probe ran. ``MagicMock`` (sync)
    # rather than the default AsyncMock that ``patch`` would pick
    # because the original is async: AsyncMock would produce an
    # unawaited-coroutine warning when ``safe_task`` is also stubbed.
    sync_mock = MagicMock(return_value=None)
    with (
        patch.object(panel, "_probe_in_background", sync_mock),
        patch("polyglot_ai.ui.panels.video_panel.safe_task") as safe_task_stub,
    ):
        panel.dropEvent(event)
    assert panel._input_path is not None
    assert panel._input_path.name == "clip.mp4"
    event.acceptProposedAction.assert_called_once()
    # The probe coroutine was created and handed to safe_task —
    # exercising the path the panel actually takes in production.
    safe_task_stub.assert_called_once()


def test_drop_skips_non_video_urls(panel, tmp_path):
    from PyQt6.QtGui import QDropEvent

    f = tmp_path / "notes.pdf"
    f.write_bytes(b"\x00")
    event = _make_drag_event(panel, [str(f)], QDropEvent)
    panel.dropEvent(event)
    assert panel._input_path is None
    event.ignore.assert_called_once()


# ── Clear / reset ──────────────────────────────────────────────────


def test_clear_input_resets_state(panel, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    panel._input_path = f
    panel._input_metadata = VideoMetadata(
        duration_s=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=1_000_000,
    )
    panel._on_clear_input()
    assert panel._input_path is None
    assert panel._input_metadata is None
    # Process button should be disabled — no input
    ready, _ = panel._readiness()
    assert ready is False


# ── Metadata rendering ─────────────────────────────────────────────


def test_render_input_label_probing_state(panel, tmp_path):
    """During the background ffprobe, the label shows "Probing
    video metadata…" instead of the metadata line."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    panel._input_path = f
    panel._render_input_label(metadata=None, probing=True)
    assert "Probing video metadata" in panel._input_label.text()


def test_render_input_label_with_metadata(panel, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    panel._input_path = f
    md = VideoMetadata(
        duration_s=154.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=41_200_000,
    )
    panel._render_input_label(metadata=md, probing=False)
    text = panel._input_label.text()
    assert "1920×1080" in text
    # The probing hint must NOT appear once the probe has completed
    assert "Probing" not in text


def test_render_input_label_probe_done_no_metadata(panel, tmp_path):
    """When ffprobe fails (returns None), the label shows just name+path
    without leaving the probing hint up."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    panel._input_path = f
    panel._render_input_label(metadata=None, probing=False)
    text = panel._input_label.text()
    assert "clip.mp4" in text
    assert "Probing" not in text


def test_metadata_ready_drops_stale_result(panel, qtbot, tmp_path):
    """If the user picks file A, then file B before A's probe
    returns, A's metadata must NOT overwrite the active state."""
    file_a = tmp_path / "a.mp4"
    file_b = tmp_path / "b.mp4"
    file_a.write_bytes(b"\x00")
    file_b.write_bytes(b"\x00")

    panel._input_path = file_b  # user is now on file B
    stale_md = VideoMetadata(
        duration_s=10.0,
        width=640,
        height=480,
        fps=24.0,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=1000,
    )

    # Simulate what _probe_in_background does when it sees the
    # input_path mismatch: it must NOT overwrite metadata.
    # The guard lives in _probe_in_background, so we exercise that
    # path with a forced mismatch.
    import asyncio

    async def run_stale_probe():
        with patch(
            "polyglot_ai.ui.panels.video_panel.run_blocking",
            return_value=stale_md,
        ):
            # File A is the "stale" path — finish probing it while
            # the panel's already on file B.
            await panel._probe_in_background(file_a)

    # We can't drive a full asyncio loop from inside a pytest-qt
    # test easily, but we can simulate by calling the worker's
    # stale-check logic directly: same code path, simpler test.
    with patch(
        "polyglot_ai.ui.panels.video_panel.run_blocking",
        return_value=stale_md,
    ):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(panel._probe_in_background(file_a))
        finally:
            loop.close()

    # The current input is file_b; the probe result for file_a must
    # be discarded.
    assert panel._input_metadata is None


# ── Prompt composition / dispatch ──────────────────────────────────


def test_process_sends_well_formed_prompt_to_chat(panel, tmp_path, qtbot):
    """The handoff prompt must include input path, output path, the
    user's edit, source info (when metadata is present), and the
    explicit shell_exec / ffmpeg nudge."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    out = tmp_path / "out.mp4"

    panel._input_path = f
    panel._input_metadata = VideoMetadata(
        duration_s=154.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=41_200_000,
    )
    panel._prompt_input.setPlainText("Trim from 0:30 to 1:15")

    fake_chat = MagicMock()
    fake_chat.send_button = MagicMock()

    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffmpeg",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ),
        patch.object(panel, "_find_chat_panel", return_value=fake_chat),
        patch.object(panel, "_raise_chat_window"),
    ):
        panel._on_process()

    fake_chat.prefill_input.assert_called_once()
    message = fake_chat.prefill_input.call_args.args[0]
    assert str(f) in message  # input path
    assert str(out) in message  # output path
    assert "Trim from 0:30 to 1:15" in message
    assert "Source info" in message
    assert "1920×1080" in message
    assert "shell_exec" in message
    assert "ffmpeg" in message.lower()


def test_process_omits_source_info_when_metadata_missing(panel, tmp_path):
    """If ffprobe failed/wasn't installed, the prompt omits the
    Source info block rather than emitting a blank one."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    out = tmp_path / "out.mp4"

    panel._input_path = f
    panel._input_metadata = None
    panel._prompt_input.setPlainText("Convert to MP4")

    fake_chat = MagicMock()
    fake_chat.send_button = MagicMock()

    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffmpeg",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ),
        patch.object(panel, "_find_chat_panel", return_value=fake_chat),
        patch.object(panel, "_raise_chat_window"),
    ):
        panel._on_process()

    message = fake_chat.prefill_input.call_args.args[0]
    assert "Source info" not in message


def test_process_aborts_when_user_cancels_output_picker(panel, tmp_path):
    """If the user cancels the Save dialog, no chat dispatch happens
    and the panel stays where it was."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    panel._input_path = f
    panel._prompt_input.setPlainText("Convert to MP4")

    fake_chat = MagicMock()

    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffmpeg",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.QFileDialog.getSaveFileName",
            return_value=("", ""),  # User cancelled
        ),
        patch.object(panel, "_find_chat_panel", return_value=fake_chat),
    ):
        panel._on_process()

    fake_chat.prefill_input.assert_not_called()


def test_process_surfaces_missing_chat_panel(panel, tmp_path):
    """When the chat panel isn't reachable (rare — usually means the
    user closed the main window or hasn't initialised it yet) the
    status feed must show an actionable message rather than silently
    do nothing."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    out = tmp_path / "out.mp4"
    panel._input_path = f
    panel._prompt_input.setPlainText("Convert to MP4")

    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffmpeg",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ),
        patch.object(panel, "_find_chat_panel", return_value=None),
    ):
        panel._on_process()

    assert "chat panel" in panel._status.toPlainText().lower()


def test_raise_chat_window_handles_no_parent(panel):
    """The early-return path when there's no parent main window must
    not crash. Previously two bare except: pass blocks hid any
    misbehaviour here; now we just early-return cleanly."""
    # The panel's window() returns itself when standalone, with no
    # parent. The handler should detect that and return without
    # raising.
    panel._raise_chat_window()  # No assert — just must not raise.


# ── Trim confirmation flow ─────────────────────────────────────────


def test_trim_confirmed_fills_prompt(panel):
    """When the preview widget emits ``trim_confirmed`` with ms
    bounds, the panel's prompt textarea ends up with a "Trim from
    X to Y" instruction matching the chip-template style."""
    # 0:30 → 1:15 in milliseconds
    panel._on_trim_confirmed(30_000, 75_000)
    assert panel._prompt_input.toPlainText() == "Trim from 0:30 to 1:15"


def test_trim_confirmed_renders_hours_for_long_clips(panel):
    """A trim spanning into the hour range must format with the
    ``H:MM:SS`` form (not show a stale ``M:SS`` from a shorter
    span). Catches a regression in the formatter wiring."""
    panel._on_trim_confirmed(0, 3_661_000)  # 1:01:01
    assert panel._prompt_input.toPlainText() == "Trim from 0:00 to 1:01:01"


def test_trim_confirmed_replaces_prior_prompt(panel):
    """If the user had typed a prompt and then dragged trim
    handles, clicking "Use trim" overwrites rather than appends —
    same "replace not append" semantics as the chip clicks."""
    panel._prompt_input.setPlainText("Convert to MP4 and add subtitles")
    panel._on_trim_confirmed(10_000, 40_000)
    assert panel._prompt_input.toPlainText() == "Trim from 0:10 to 0:40"


def test_trim_confirmed_writes_status_feedback(panel):
    """The status feed should confirm the trim was set so the user
    can see something landed even if the prompt textarea is
    scrolled out of view."""
    panel._on_trim_confirmed(15_000, 45_000)
    status = panel._status.toPlainText()
    assert "0:15" in status
    assert "0:45" in status


def test_preview_load_skipped_when_widget_construction_fails(panel, tmp_path):
    """If QtMultimedia / gstreamer can't be initialised the panel
    must keep working with the preview container hidden — not
    crash the whole panel."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    panel._input_path = f
    panel._preview_init_failed = True  # Force the fail path
    panel._load_preview()
    assert panel._preview_container.isVisible() is False


def test_clear_input_hides_preview(panel, tmp_path):
    """Resetting the input clears the preview widget AND hides
    the container so the wizard returns to a clean empty state."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    panel._input_path = f
    panel._preview_container.setVisible(True)
    panel._on_clear_input()
    assert panel._preview_container.isVisible() is False
    assert panel._input_path is None
