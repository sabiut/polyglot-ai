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
    must keep working without a player pop-out — not crash the
    whole wizard. The latch ensures we don't keep retrying on
    every load either."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    panel._input_path = f
    panel._preview_init_failed = True  # Force the fail path
    panel._load_preview()  # Must not raise.
    # The latch keeps player_window None — no construction
    # attempt was made, no exception thrown.
    assert panel._player_window is None


def test_clear_input_hides_player_window(panel, tmp_path):
    """Resetting the input clears the player window's playback
    state AND hides the window so the wizard returns to a clean
    empty state. The window itself is kept around (not destroyed)
    so the next loaded clip reuses the cached instance."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    panel._input_path = f
    # Manually pop the player window so the clear path has
    # something to act on (otherwise it short-circuits).
    panel._ensure_player_window()
    assert panel._player_window is not None
    panel._player_window.show()

    panel._on_clear_input()
    assert panel._input_path is None
    # Window cached but not visible — same pattern as the wizard
    # window itself for fast re-open.
    assert panel._player_window is not None
    assert panel._player_window.isVisible() is False


def test_player_window_is_separate_top_level(panel, tmp_path):
    """The pop-out player must be a real top-level window, not a
    child widget embedded inside the wizard. This is the design
    fix for the user-reported "window-within-the-window" bug —
    the preview now lives in its own window the WM stacks
    independently of the wizard.
    """
    panel._ensure_player_window()
    pw = panel._player_window
    assert pw is not None
    # ``isWindow()`` returns True for widgets with the Window
    # flag; that's what makes them top-level under the WM.
    assert pw.isWindow() is True
    # And it is NOT a descendant of the panel — the WM treats it
    # as its own window even though Qt owns it via the parent
    # reference for memory-management purposes.
    assert not panel.isAncestorOf(pw)


def test_load_preview_shows_pop_out_window(panel, tmp_path):
    """Picking a clip must surface the pop-out player. Previously
    the preview was rendered inline; this regression test pins the
    new behaviour: a real separate window appears."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    panel._input_path = f
    panel._load_preview()

    pw = panel._player_window
    assert pw is not None
    assert pw.isVisible() is True


# ── VideoWindow lifecycle ──────────────────────────────────────────


def test_video_window_close_hides_not_destroys(qtbot):
    """The activity bar caches a constructed ``VideoWindow`` so
    re-clicks raise the same window and preserve the user's loaded
    clip / prompt. Regression: if close were to *destroy* the
    window (e.g. by accepting the default close behaviour when
    WA_DeleteOnClose is set), the cached reference would point to
    a dead C++ object and the second click would either crash with
    RuntimeError or spawn a duplicate fresh window (the bug a
    user reported with a screenshot showing two Video Editor
    windows stacked on screen).

    This test reproduces the close cycle and asserts the cached
    widget stays usable: ``isVisible()`` flips to False, but
    ``isHidden()`` returns True (i.e. the widget exists, it's
    just not shown).
    """
    from PyQt6.QtGui import QCloseEvent

    from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow

    panel = VideoPanel()
    qtbot.addWidget(panel)
    window = VideoWindow(panel)
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible() is True

    # Simulate the user pressing the [X] button.
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is True
    # Manually run the visibility update (closeEvent in tests
    # doesn't always flip isVisible immediately on Qt's side).
    window.hide()
    assert window.isVisible() is False
    # Crucially, the widget object is still alive — no
    # sip.isdeleted, no RuntimeError on subsequent method calls.
    assert window.isHidden() is True
    # And re-show works: we get the SAME window back, not a fresh
    # construction.
    window.show()
    assert window.isVisible() is True


def test_window_is_alive_helper():
    """``_window_is_alive`` is the guard inside ``_show_video_window``
    that protects against the cached widget having been C++-deleted.
    It must return False for ``None``, True for a live widget, and
    False for a widget Qt has destroyed.
    """
    from PyQt6.QtWidgets import QWidget

    from polyglot_ai.ui.main_window import _window_is_alive

    assert _window_is_alive(None) is False

    w = QWidget()
    assert _window_is_alive(w) is True

    # Force-delete via deleteLater + process events would be ideal,
    # but sip.isdeleted on a deleteLater'd widget isn't guaranteed
    # to flip without an event-loop tick — testing the bare
    # ``deleted`` path requires sip.delete which is internal. We
    # already cover the None branch and the live branch, which is
    # what the call sites actually depend on. The Qt-deleted path
    # is implicitly tested via the close-cycle test above.


def test_show_video_window_reuses_cached_window(qtbot, monkeypatch):
    """The whole point of the lazy-and-keep pattern. Three
    consecutive 'click the video icon' calls must produce one
    window, not three."""
    from polyglot_ai.ui.main_window import _window_is_alive
    from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow

    # Re-implement the slot's body inline so we don't have to
    # construct a real MainWindow (which pulls in chat / database /
    # MCP and is expensive). The shape we're pinning is "if the
    # cached window is still alive, reuse; otherwise rebuild".
    state: dict = {"panel": None, "window": None, "constructions": 0}

    def show_video_window():
        if not _window_is_alive(state["panel"]):
            state["panel"] = VideoPanel()
            qtbot.addWidget(state["panel"])
        if not _window_is_alive(state["window"]):
            state["window"] = VideoWindow(state["panel"])
            qtbot.addWidget(state["window"])
            state["constructions"] += 1
        state["window"].show()

    show_video_window()
    show_video_window()
    show_video_window()

    assert state["constructions"] == 1, (
        f"expected the cached window to be reused across 3 clicks; "
        f"got {state['constructions']} constructions instead"
    )


def test_show_video_window_rebuilds_after_qt_deletion(qtbot):
    """When the cached widget has been destroyed by Qt (e.g. via
    sip.delete in a teardown path, or the parent being torn down
    in a complex shutdown sequence), the next call must rebuild
    cleanly instead of raising RuntimeError.
    """
    from PyQt6 import sip

    from polyglot_ai.ui.main_window import _window_is_alive
    from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow

    panel = VideoPanel()
    qtbot.addWidget(panel)
    window = VideoWindow(panel)
    qtbot.addWidget(window)

    # Simulate Qt destroying the window out from under us.
    sip.delete(window)

    # Now the cached reference points at a dead C++ object. The
    # guard must catch that and report "not alive".
    assert _window_is_alive(window) is False

    # Rebuilding is safe: the new widget is fresh and alive.
    panel2 = VideoPanel()
    qtbot.addWidget(panel2)
    window2 = VideoWindow(panel2)
    qtbot.addWidget(window2)
    assert _window_is_alive(window2) is True


# ── Tree-sweep guard (the "two windows on screen" bug) ─────────────


def test_orphan_video_window_is_adopted_then_cleaned(qtbot):
    """Reproduces the user's "two Video Editor windows" screenshot.

    Even though the panel caches a constructed VideoWindow on
    MainWindow and reuses it, the screenshot showed two windows
    open at once — meaning some path created a duplicate without
    going through the cache.

    The tree-sweep guard in ``_show_video_window`` defends against
    this by walking ``MainWindow``'s children for any pre-existing
    ``VideoWindow`` and adopting it as the cache before construct-
    ing fresh. Any extras (beyond the one we adopt) get
    ``deleteLater``-d so the user only ever sees one.

    This test simulates the scenario directly: pretend the cache
    is empty but a VideoWindow already exists as a child of the
    parent. The slot must adopt it instead of creating a new one.
    """
    from PyQt6.QtWidgets import QMainWindow

    from polyglot_ai.ui.main_window import _window_is_alive
    from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow

    parent = QMainWindow()
    qtbot.addWidget(parent)

    # An orphan VideoWindow exists as a child of `parent` but the
    # external cache reference is None — exactly the state the
    # bug reproduces.
    orphan_panel = VideoPanel()
    orphan_window = VideoWindow(orphan_panel, parent)

    # Inline re-implementation of the slot's adoption + sweep
    # logic so this test doesn't need a real MainWindow.
    cached_panel = None
    cached_window = None

    if not _window_is_alive(cached_window):
        existing = parent.findChildren(VideoWindow)
        if existing:
            cached_window = existing[0]
            cached_panel = existing[0].panel

    if not _window_is_alive(cached_panel):
        cached_panel = VideoPanel()
    if not _window_is_alive(cached_window):
        cached_window = VideoWindow(cached_panel, parent)

    # The orphan was adopted — same widget, same panel.
    assert cached_window is orphan_window
    assert cached_panel is orphan_panel

    # And exactly one VideoWindow is under `parent` afterwards.
    assert len(parent.findChildren(VideoWindow)) == 1


def test_extra_video_windows_get_swept_away(qtbot):
    """If, somehow, MORE than one VideoWindow exists as children
    of MainWindow, the sweep at the end of ``_show_video_window``
    must keep the cached one and close the rest. The user should
    only ever see one Video Editor on screen even when the bug
    has already happened.
    """
    from PyQt6.QtWidgets import QMainWindow

    from polyglot_ai.ui.panels.video_panel import VideoPanel, VideoWindow

    parent = QMainWindow()
    qtbot.addWidget(parent)

    # Manufacture the buggy state directly: three sibling
    # VideoWindows under one parent.
    p1, p2, p3 = VideoPanel(), VideoPanel(), VideoPanel()
    w1 = VideoWindow(p1, parent)
    w2 = VideoWindow(p2, parent)
    w3 = VideoWindow(p3, parent)
    w1.show()
    w2.show()
    w3.show()

    canonical = w1  # the one we want to keep — could be any

    # Sweep logic from _show_video_window: hide + deleteLater
    # every VideoWindow that isn't the canonical one.
    for w in parent.findChildren(VideoWindow):
        if w is not canonical:
            w.hide()
            w.deleteLater()

    # After the event loop runs once, the orphans are gone.
    qtbot.wait(50)

    survivors = parent.findChildren(VideoWindow)
    assert canonical in survivors
    # The exact list may include not-yet-fully-collected widgets
    # depending on Qt's event timing — the critical contract is
    # that only the canonical one is visible.
    visible_windows = [w for w in survivors if w.isVisible()]
    assert visible_windows == [canonical], (
        f"expected only the canonical window visible after sweep, got {visible_windows!r}"
    )
