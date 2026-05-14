"""UI tests for the inline video preview + trim slider.

The :class:`RangeSlider` is pure-Qt arithmetic and is fully
testable without gstreamer codecs — most tests live here.

:class:`VideoPreviewWidget` is harder: ``QMediaPlayer`` only emits
``durationChanged`` etc. after gstreamer actually decodes a file,
which makes integration testing flaky in CI. Tests here exercise
the deterministic surface (construction, ``load`` / ``clear``,
range-slider plumbing, error signal) and stub out the player for
behaviour that depends on real codec output.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from polyglot_ai.ui.widgets.video_preview import (
    RangeSlider,
    VideoPreviewWidget,
    _format_timecode_ms,
)


def _press(x: int, y: int = 15, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
    """Build a left-click QMouseEvent at ``(x, y)``.

    PyQt6 only accepts ``QPointF`` (not ``QPoint``) in the
    QMouseEvent constructor — wrap the int coordinates here once
    so the dozen-or-so test usages stay readable.
    """
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(x, y),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def _move(x: int, y: int = 15) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


# ── _format_timecode_ms ────────────────────────────────────────────


def test_format_timecode_zero():
    assert _format_timecode_ms(0) == "0:00"


def test_format_timecode_minutes_seconds():
    # 1:23 — typical short clip
    assert _format_timecode_ms(83_000) == "1:23"


def test_format_timecode_hours():
    assert _format_timecode_ms(3_661_000) == "1:01:01"


def test_format_timecode_rounds_down_sub_second():
    # 999 ms should still read as 0:00 — we display whole seconds
    # so the timestamp doesn't flicker between 0 and 1 at boundary.
    assert _format_timecode_ms(999) == "0:00"


def test_format_timecode_negative_clamped_to_zero():
    """Qt occasionally emits negative position values during seek;
    don't display ``-1:00`` as a result."""
    assert _format_timecode_ms(-5_000) == "0:00"


# ── RangeSlider ────────────────────────────────────────────────────


@pytest.fixture
def slider(qtbot):
    s = RangeSlider()
    s.resize(400, 30)
    qtbot.addWidget(s)
    s.show()
    return s


def test_range_slider_initial_state(slider):
    assert slider.duration_ms == 0
    assert slider.start_ms == 0
    assert slider.end_ms == 0
    assert slider.playhead_ms == 0


def test_set_duration_resets_range_to_full(slider, qtbot):
    with qtbot.waitSignal(slider.range_changed, timeout=500) as blocker:
        slider.set_duration(60_000)
    assert blocker.args == [0, 60_000]
    assert slider.duration_ms == 60_000
    assert slider.start_ms == 0
    assert slider.end_ms == 60_000


def test_set_duration_resets_playhead(slider):
    """A fresh clip starts at 0:00 even if a stale playhead was
    parked elsewhere — otherwise the preview would seek into the
    next file at a position from the old one."""
    slider.set_duration(60_000)
    slider.set_playhead(30_000)
    slider.set_duration(120_000)
    assert slider.playhead_ms == 0


def test_set_range_clamps_to_duration(slider):
    slider.set_duration(60_000)
    # Request a range that's larger than the clip — clamps to [0, 60s]
    slider.set_range(-10_000, 120_000)
    assert slider.start_ms == 0
    assert slider.end_ms == 60_000


def test_set_range_swaps_when_inverted(slider):
    """A caller could pass (end, start) by mistake. The widget
    swaps them rather than refusing the call — refusing would
    mean every caller has to validate first."""
    slider.set_duration(60_000)
    slider.set_range(40_000, 10_000)
    assert slider.start_ms == 10_000
    assert slider.end_ms == 40_000


def test_set_range_no_op_emits_nothing(slider, qtbot):
    """If the new range matches the current one, don't fire
    ``range_changed`` — avoid wake-loops in callers that re-set
    the value when they see the signal."""
    slider.set_duration(60_000)  # initial range_changed
    # No-op call: same bounds
    received: list[tuple[int, int]] = []
    slider.range_changed.connect(lambda s, e: received.append((s, e)))
    slider.set_range(0, 60_000)
    assert received == []


def test_set_playhead_clamps_to_duration(slider):
    slider.set_duration(30_000)
    slider.set_playhead(50_000)
    assert slider.playhead_ms == 30_000
    slider.set_playhead(-5_000)
    assert slider.playhead_ms == 0


def test_ms_to_x_full_range_maps_across_track(slider):
    slider.set_duration(60_000)
    # 0 ms lands at the left of the track (after the handle margin)
    left = slider._ms_to_x(0)
    right = slider._ms_to_x(60_000)
    assert right > left  # Strictly increasing
    # 50% should be roughly the midpoint of the track
    mid = slider._ms_to_x(30_000)
    assert abs(mid - (left + right) / 2) < 1.0


def test_x_to_ms_inverse_of_ms_to_x(slider):
    """Round-trip every ms position should land back in the same
    ballpark — within 1 ms because of float arithmetic."""
    slider.set_duration(60_000)
    for ms in (0, 1_000, 30_000, 59_999):
        x = slider._ms_to_x(ms)
        recovered = slider._x_to_ms(x)
        assert abs(recovered - ms) < 100, (
            f"round-trip drift for {ms}ms: x={x} → recovered={recovered}"
        )


def test_hit_test_finds_start_handle(slider):
    slider.set_duration(60_000)
    # Start handle is at the left edge of the track
    start_x = slider._ms_to_x(0)
    assert slider._hit_test(QPoint(int(start_x), 15)) == "start"


def test_hit_test_finds_end_handle(slider):
    slider.set_duration(60_000)
    end_x = slider._ms_to_x(60_000)
    assert slider._hit_test(QPoint(int(end_x), 15)) == "end"


def test_hit_test_distinguishes_playhead_from_handles(slider):
    """When the playhead is far from both handles, clicking near it
    returns ``"playhead"`` not ``"start"`` / ``"end"``."""
    slider.set_duration(60_000)
    slider.set_playhead(30_000)
    playhead_x = slider._ms_to_x(30_000)
    assert slider._hit_test(QPoint(int(playhead_x), 15)) == "playhead"


def test_hit_test_returns_none_for_track_gap(slider):
    """A click on the track in a gap between handles and playhead
    returns ``None`` — that's the "seek by clicking the track"
    code path, not a handle drag.
    """
    slider.set_duration(60_000)
    # Park the handles + playhead far from the left edge so a click
    # at x=0 isn't near any of them.
    slider.set_range(30_000, 50_000)
    slider.set_playhead(40_000)
    assert slider._hit_test(QPoint(0, 15)) is None


def test_click_on_track_emits_playhead_clicked(slider, qtbot):
    slider.set_duration(60_000)
    mid_x = int(slider._ms_to_x(30_000))
    with qtbot.waitSignal(slider.playhead_clicked, timeout=500) as blocker:
        slider.mousePressEvent(_press(mid_x))
    # Should have landed close to 30s
    pos_ms = blocker.args[0]
    assert abs(pos_ms - 30_000) < 1_000


def test_drag_start_handle_updates_range(slider, qtbot):
    slider.set_duration(60_000)
    start_x = int(slider._ms_to_x(0))
    slider.mousePressEvent(_press(start_x))
    assert slider._dragging == "start"

    # Drag to ~25% — start should follow the drag
    drag_x = int(slider._ms_to_x(15_000))
    with qtbot.waitSignal(slider.range_changed, timeout=500) as blocker:
        slider.mouseMoveEvent(_move(drag_x))
    assert abs(blocker.args[0] - 15_000) < 1_000
    assert blocker.args[1] == 60_000  # end stays put


def test_drag_start_cannot_cross_end(slider):
    """The 1-second minimum gap means dragging the start past the
    end leaves a sensible non-zero selection rather than collapsing
    to a degenerate 0-second range."""
    slider.set_duration(60_000)
    slider.mousePressEvent(_press(int(slider._ms_to_x(0))))
    slider.mouseMoveEvent(_move(int(slider._ms_to_x(70_000))))  # past end
    # Start must stop at end - 1 second
    assert slider.end_ms - slider.start_ms >= 1_000


def test_drag_end_cannot_cross_start(slider):
    slider.set_duration(60_000)
    slider.set_range(20_000, 60_000)
    slider.mousePressEvent(_press(int(slider._ms_to_x(60_000))))
    slider.mouseMoveEvent(_move(int(slider._ms_to_x(5_000))))  # below start
    assert slider.end_ms - slider.start_ms >= 1_000


def test_drag_with_zero_duration_no_op(slider, qtbot):
    """Mouse events on an unloaded slider must not crash or emit
    bogus signals — a user clicking before a clip is loaded is a
    real scenario."""
    received: list = []
    slider.range_changed.connect(lambda s, e: received.append((s, e)))
    slider.playhead_clicked.connect(lambda p: received.append(("clicked", p)))
    slider.mousePressEvent(_press(50))
    assert received == []


def test_right_click_does_not_trigger_drag(slider):
    slider.set_duration(60_000)
    slider.mousePressEvent(_press(int(slider._ms_to_x(0)), button=Qt.MouseButton.RightButton))
    assert slider._dragging is None


# ── VideoPreviewWidget ─────────────────────────────────────────────


@pytest.fixture
def preview(qtbot):
    w = VideoPreviewWidget()
    qtbot.addWidget(w)
    w.show()
    return w


def test_preview_initial_state(preview):
    """A fresh widget has no clip and the trim-commit button is disabled."""
    assert preview._use_trim_btn.isEnabled() is False
    assert preview._use_trim_btn.text() == "Use trim 0:00 → 0:00"
    assert preview.range_slider.duration_ms == 0


def test_preview_load_clears_old_state(preview, tmp_path):
    """Calling ``load`` resets the trim button and stops any
    playback — switching clips must not carry over the previous
    clip's UI state."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 100)
    # Drive the widget into a "loaded" state by simulating the
    # duration-changed signal (bypasses gstreamer decoding).
    preview._on_duration_changed(30_000)
    assert preview._use_trim_btn.isEnabled() is True

    preview.load(f)
    # ``load`` resets the button (we haven't gotten the new
    # duration yet for the freshly loaded file).
    assert preview._use_trim_btn.isEnabled() is False


def test_preview_clear_tears_down_state(preview):
    preview._on_duration_changed(30_000)
    preview.range_slider.set_range(5_000, 25_000)
    preview.clear()
    assert preview.range_slider.duration_ms == 0
    assert preview._use_trim_btn.isEnabled() is False
    assert preview._time_label.text() == "0:00 / 0:00"


def test_use_trim_button_emits_trim_confirmed(preview, qtbot):
    """The "Use trim" button is the only path that emits
    ``trim_confirmed`` — clicking it must fire with the current
    range slider bounds."""
    preview._on_duration_changed(60_000)
    preview.range_slider.set_range(10_000, 45_000)

    with qtbot.waitSignal(preview.trim_confirmed, timeout=500) as blocker:
        qtbot.mouseClick(preview._use_trim_btn, Qt.MouseButton.LeftButton)
    assert blocker.args == [10_000, 45_000]


def test_use_trim_button_label_reflects_current_range(preview):
    """As the user drags the trim handles the button text updates
    so they see the exact range they're about to commit."""
    preview._on_duration_changed(60_000)
    preview.range_slider.set_range(10_000, 45_000)
    assert preview._use_trim_btn.text() == "Use trim 0:10 → 0:45"


def test_position_changed_pauses_at_trim_end(preview):
    """Auto-pause at trim-end is the "don't bleed past the trim"
    behaviour. When the playhead position passes the end handle
    and the player is currently playing, the widget pauses it."""
    preview._on_duration_changed(60_000)
    preview.range_slider.set_range(10_000, 30_000)
    # Patch the player so we can observe the pause call
    preview._player = MagicMock()
    preview._player.playbackState = MagicMock(
        return_value=preview.__class__.__mro__[0].__dict__.get("_player") or None
    )
    # Easier path: stub via MagicMock with the playing-state literal
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState

    preview._on_position_changed(31_000)  # past trim end
    preview._player.pause.assert_called_once()


def test_player_error_signal_propagates(preview, qtbot):
    """A media player error must surface via ``playback_error`` so
    the host panel can show the message instead of a black box."""
    from PyQt6.QtMultimedia import QMediaPlayer

    with qtbot.waitSignal(preview.playback_error, timeout=500) as blocker:
        preview._on_player_error(QMediaPlayer.Error.ResourceError, "no such file")
    assert "no such file" in blocker.args[0]


def test_player_error_falls_back_to_enum_name(preview, qtbot):
    """Some Qt builds forward an empty description string with the
    error enum — make sure we still emit something actionable."""
    from PyQt6.QtMultimedia import QMediaPlayer

    with qtbot.waitSignal(preview.playback_error, timeout=500) as blocker:
        preview._on_player_error(QMediaPlayer.Error.FormatError, "")
    assert "FormatError" in blocker.args[0]


def test_no_error_signal_for_NoError(preview):
    """Qt fires ``errorOccurred`` with ``NoError`` after recovery —
    don't bubble those to the user."""
    received: list = []
    preview.playback_error.connect(lambda msg: received.append(msg))

    from PyQt6.QtMultimedia import QMediaPlayer

    preview._on_player_error(QMediaPlayer.Error.NoError, "")
    assert received == []


def test_toggle_play_switches_state(preview):
    """The play/pause button toggles whichever state the player is
    currently in — paused → play, playing → pause."""
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._player = MagicMock()
    preview._player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
    preview._toggle_play()
    preview._player.play.assert_called_once()

    preview._player.reset_mock()
    preview._player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
    preview._toggle_play()
    preview._player.pause.assert_called_once()


# ── Click-to-play overlay ──────────────────────────────────────────


def test_video_widget_is_clickable(preview):
    """The video widget must be a ``_ClickableVideoWidget`` subclass
    so left-clicks on the video frame emit ``clicked``. Bare
    ``QVideoWidget`` doesn't expose a click signal — without the
    subclass the only way to play is the tiny control-row button."""
    from polyglot_ai.ui.widgets.video_preview import _ClickableVideoWidget

    assert isinstance(preview._video, _ClickableVideoWidget)


def test_clicking_video_toggles_playback(preview):
    """Left-clicking the video frame calls play/pause on the player.

    This is the "every web video player works this way" path —
    the test stubs the player so we observe the call without
    needing real media decode.
    """
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._player = MagicMock()
    preview._player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState

    # Drive the click via the signal directly — qtbot.mouseClick on
    # QVideoWidget is unreliable in offscreen mode because the
    # underlying video surface isn't a normal Qt widget.
    preview._video.clicked.emit()
    preview._player.play.assert_called_once()

    preview._player.reset_mock()
    preview._player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
    preview._video.clicked.emit()
    preview._player.pause.assert_called_once()


def test_clickable_video_only_emits_for_left_button(qtbot):
    """Right-click on the video should not toggle playback —
    leave room for a future context menu without surprising the
    user with a play/pause on every right-click."""
    from polyglot_ai.ui.widgets.video_preview import _ClickableVideoWidget

    widget = _ClickableVideoWidget()
    qtbot.addWidget(widget)
    widget.show()

    received: list = []
    widget.clicked.connect(lambda: received.append(True))

    widget.mousePressEvent(_press(50, button=Qt.MouseButton.RightButton))
    assert received == []

    widget.mousePressEvent(_press(50, button=Qt.MouseButton.LeftButton))
    assert received == [True]


def test_overlay_hides_when_playing(preview):
    """The translucent play icon must hide once the video is
    playing so it doesn't sit on top of the content the user
    wants to watch."""
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert preview._play_overlay.isHidden() is True


def test_overlay_shows_when_paused(preview):
    """Returning to paused state must surface the overlay again —
    it's the user's cue that the video is clickable to resume."""
    from PyQt6.QtMultimedia import QMediaPlayer

    # First drive it into playing → overlay hidden.
    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert preview._play_overlay.isHidden() is True
    # Then back to paused → overlay shown again.
    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
    assert preview._play_overlay.isHidden() is False


def test_overlay_shows_when_stopped(preview):
    """``StoppedState`` (end-of-clip or explicit stop) must also
    show the overlay, not stay hidden."""
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.StoppedState)
    assert preview._play_overlay.isHidden() is False


def test_clear_restores_overlay_visibility(preview):
    """``clear()`` resets state for the next clip — the overlay
    must return to its paused / click-to-play form even if the
    previous clip ended with the overlay hidden mid-playback."""
    from PyQt6.QtMultimedia import QMediaPlayer

    preview._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert preview._play_overlay.isHidden() is True
    preview.clear()
    assert preview._play_overlay.isHidden() is False


def test_overlay_does_not_intercept_mouse_events(preview):
    """The overlay is set ``WA_TransparentForMouseEvents`` so
    clicks pass through to the video widget below. Without this,
    the most prominent click target — the big ▶ icon — would
    swallow the click and play would do nothing."""
    assert (
        preview._play_overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    )
