"""Inline video preview + dual-handle trim slider.

Provides two reusable widgets:

* :class:`RangeSlider` — a horizontal track with two draggable
  handles representing a [start, end] selection plus a playhead
  indicator. Qt doesn't ship a built-in range slider, and the
  rough edge cases (handles crossing, playhead drag vs handle
  drag, click-to-seek vs click-near-handle) make a hand-rolled
  widget cleaner than gluing two ``QSlider`` instances together.

* :class:`VideoPreviewWidget` — a ``QVideoWidget`` driven by a
  ``QMediaPlayer``, with a play/pause button, a current/duration
  time label, the range slider for trim selection, and an
  emitter for "user picked these times — go put them in the
  prompt". Designed to live inside ``VideoPanel`` step 1.

Design goals:

* **Robust on Linux.** QtMultimedia relies on gstreamer plugins;
  the widget surfaces missing-codec failures clearly instead of
  showing a silent black rectangle.
* **No state surprises.** The widget exposes a tiny API
  (``load(path)``, ``clear()``, ``trim_selected`` signal). The
  panel owns *whether* a clip is loaded; the widget owns *how*
  it plays.
* **Cheap to test.** The range slider is fully testable without
  gstreamer (it's pure Qt arithmetic), and the player widget
  exposes its state via plain properties so tests can drive it
  with stubs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


# ── RangeSlider ────────────────────────────────────────────────────


class RangeSlider(QWidget):
    """Horizontal track with two trim handles and a playhead.

    The widget stores **integer milliseconds** for all positions
    (start, end, playhead, total duration). Milliseconds match
    Qt's ``QMediaPlayer`` units exactly, so the slider can speak
    to the player without per-frame conversion.

    Signals:

    * ``range_changed(start_ms, end_ms)`` — emitted while the user
      is dragging a handle or after a programmatic ``set_range``.
    * ``playhead_dragged(position_ms)`` — emitted when the user
      drags the playhead. Distinct from a "click to seek" because
      the widget receives many move events during a drag; the
      caller may want to throttle seeks accordingly.
    * ``playhead_clicked(position_ms)`` — emitted on a click that
      isn't near either trim handle. Used to seek the player.

    Handle hit-testing has a generous tolerance (~12 px on either
    side) because handle widths are small and touchpad clicks
    rarely land dead-centre.
    """

    range_changed = pyqtSignal(int, int)
    playhead_dragged = pyqtSignal(int)
    playhead_clicked = pyqtSignal(int)

    # Visual constants — slim track with chunky handles so the
    # drag affordance is obvious without dominating the layout.
    _TRACK_HEIGHT = 6
    _HANDLE_WIDTH = 14
    _HANDLE_HEIGHT = 22
    _PLAYHEAD_WIDTH = 3
    _HIT_TOLERANCE = 12  # px around a handle that still grabs it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_ms: int = 0
        self._start_ms: int = 0
        self._end_ms: int = 0
        self._playhead_ms: int = 0
        # ``_dragging`` is the active drag target: "start", "end",
        # "playhead", or None. The first drag event picks the
        # closest handle within tolerance; subsequent moves stay
        # locked to that handle until release.
        self._dragging: str | None = None

        self.setMinimumHeight(self._HANDLE_HEIGHT + 8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    # ── Public API ────────────────────────────────────────────────

    def set_duration(self, duration_ms: int) -> None:
        """Set the total clip length. Resets start/end to full range."""
        self._duration_ms = max(0, int(duration_ms))
        # New clip → reset selection to "whole clip" so a user who
        # never touches the slider gets sensible defaults.
        self._start_ms = 0
        self._end_ms = self._duration_ms
        self._playhead_ms = 0
        self.update()
        self.range_changed.emit(self._start_ms, self._end_ms)

    def set_playhead(self, position_ms: int) -> None:
        """Move the playhead indicator. Called by the player on tick."""
        new_pos = max(0, min(position_ms, self._duration_ms))
        if new_pos != self._playhead_ms:
            self._playhead_ms = new_pos
            self.update()

    def set_range(self, start_ms: int, end_ms: int) -> None:
        """Programmatically set the trim selection.

        Clamps to the duration and enforces ``start <= end`` so a
        caller can't drive the widget into an impossible state.
        """
        if self._duration_ms <= 0:
            return
        new_start = max(0, min(int(start_ms), self._duration_ms))
        new_end = max(0, min(int(end_ms), self._duration_ms))
        if new_start > new_end:
            new_start, new_end = new_end, new_start
        if (new_start, new_end) != (self._start_ms, self._end_ms):
            self._start_ms = new_start
            self._end_ms = new_end
            self.update()
            self.range_changed.emit(self._start_ms, self._end_ms)

    @property
    def start_ms(self) -> int:
        return self._start_ms

    @property
    def end_ms(self) -> int:
        return self._end_ms

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def playhead_ms(self) -> int:
        return self._playhead_ms

    # ── Geometry helpers ──────────────────────────────────────────

    def _track_rect(self) -> QRectF:
        """Inner rectangle for the painted track (excludes handle margins)."""
        # Leave room for the handles to overshoot the track ends
        # without clipping — easier to draw cleanly than to inset
        # handle positions during paint.
        margin = self._HANDLE_WIDTH / 2
        y = (self.height() - self._TRACK_HEIGHT) / 2
        return QRectF(
            margin,
            y,
            max(0.0, self.width() - 2 * margin),
            self._TRACK_HEIGHT,
        )

    def _ms_to_x(self, ms: int) -> float:
        """Map a millisecond position to an x-pixel on the track."""
        track = self._track_rect()
        if self._duration_ms <= 0 or track.width() <= 0:
            return track.left()
        frac = max(0.0, min(1.0, ms / self._duration_ms))
        return track.left() + frac * track.width()

    def _x_to_ms(self, x: float) -> int:
        """Inverse of :meth:`_ms_to_x` — pixel to millisecond."""
        track = self._track_rect()
        if track.width() <= 0:
            return 0
        frac = (x - track.left()) / track.width()
        frac = max(0.0, min(1.0, frac))
        return int(frac * self._duration_ms)

    def _hit_test(self, pos: QPoint) -> str | None:
        """What's under ``pos``? Returns ``"start"``, ``"end"``,
        ``"playhead"``, or ``None`` when the cursor isn't near any
        draggable element.

        Hit order matters: handles win over playhead so a user can
        still grab a handle when the playhead happens to be parked
        on top of it.
        """
        x = pos.x()
        start_x = self._ms_to_x(self._start_ms)
        end_x = self._ms_to_x(self._end_ms)
        playhead_x = self._ms_to_x(self._playhead_ms)

        if abs(x - start_x) <= self._HIT_TOLERANCE:
            return "start"
        if abs(x - end_x) <= self._HIT_TOLERANCE:
            return "end"
        if abs(x - playhead_x) <= self._HIT_TOLERANCE:
            return "playhead"
        return None

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        if self._duration_ms <= 0:
            return  # No clip loaded — render an empty widget.

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = self._track_rect()

        # Inactive track — full width, muted colour.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tc.get("border_subtle")))
        painter.drawRoundedRect(track, 3, 3)

        # Active (selected) range — accent colour band between handles.
        start_x = self._ms_to_x(self._start_ms)
        end_x = self._ms_to_x(self._end_ms)
        active = QRectF(start_x, track.top(), max(0.0, end_x - start_x), track.height())
        painter.setBrush(QColor(tc.get("accent_primary")))
        painter.drawRoundedRect(active, 3, 3)

        # Playhead — vertical line full-height (matches a video scrub bar).
        playhead_x = self._ms_to_x(self._playhead_ms)
        painter.setPen(QPen(QColor(tc.get("text_heading")), self._PLAYHEAD_WIDTH))
        painter.drawLine(
            int(playhead_x),
            int(track.top() - 4),
            int(playhead_x),
            int(track.bottom() + 4),
        )

        # Trim handles — small filled rectangles + a thin border so
        # they read as "draggable" against the active band.
        painter.setPen(QPen(QColor(tc.get("text_heading")), 1))
        painter.setBrush(QColor(tc.get("bg_surface")))
        handle_y = (self.height() - self._HANDLE_HEIGHT) / 2
        for x in (start_x, end_x):
            handle = QRectF(
                x - self._HANDLE_WIDTH / 2,
                handle_y,
                self._HANDLE_WIDTH,
                self._HANDLE_HEIGHT,
            )
            painter.drawRoundedRect(handle, 3, 3)
            # Two vertical grip dashes inside the handle so it
            # reads as draggable even at small sizes.
            painter.setPen(QPen(QColor(tc.get("text_muted")), 1))
            painter.drawLine(
                int(x - 2), int(handle_y + 6), int(x - 2), int(handle_y + self._HANDLE_HEIGHT - 6)
            )
            painter.drawLine(
                int(x + 2), int(handle_y + 6), int(x + 2), int(handle_y + self._HANDLE_HEIGHT - 6)
            )
            painter.setPen(QPen(QColor(tc.get("text_heading")), 1))

        painter.end()

    # ── Mouse handling ────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._duration_ms <= 0:
            return
        target = self._hit_test(event.pos())
        if target is None:
            # Click on the track itself (not on a handle) is a seek.
            new_pos = self._x_to_ms(event.pos().x())
            self._playhead_ms = new_pos
            self.update()
            self.playhead_clicked.emit(new_pos)
            return
        self._dragging = target

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging is None or self._duration_ms <= 0:
            return
        ms = self._x_to_ms(event.pos().x())
        if self._dragging == "start":
            # Prevent the start handle from crossing the end handle.
            # Enforce a minimum 1-second selection so the trim chip
            # always produces a non-degenerate range.
            min_end_gap_ms = 1_000
            self._start_ms = max(0, min(ms, self._end_ms - min_end_gap_ms))
            self.range_changed.emit(self._start_ms, self._end_ms)
            self.update()
        elif self._dragging == "end":
            min_end_gap_ms = 1_000
            self._end_ms = min(
                self._duration_ms,
                max(ms, self._start_ms + min_end_gap_ms),
            )
            self.range_changed.emit(self._start_ms, self._end_ms)
            self.update()
        elif self._dragging == "playhead":
            self._playhead_ms = max(0, min(ms, self._duration_ms))
            self.update()
            self.playhead_dragged.emit(self._playhead_ms)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = None

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, self._HANDLE_HEIGHT + 8)


# ── VideoPreviewWidget ─────────────────────────────────────────────


class _ClickableVideoWidget(QVideoWidget):
    """A ``QVideoWidget`` that emits ``clicked`` on left mouse press.

    Vanilla ``QVideoWidget`` doesn't expose a click signal — the
    user can stare at the video forever and the only way to play
    is to find the tiny play button in the controls row. This
    subclass makes the video itself a click target, matching how
    every web video player works (YouTube, Vimeo, native media
    apps). The control-row play button stays as a redundant
    affordance so keyboard / accessibility users still have a
    discoverable control.
    """

    clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _format_timecode_ms(ms: int) -> str:
    """Render milliseconds as ``M:SS`` or ``H:MM:SS`` for display."""
    total_s = max(0, int(ms / 1000))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class VideoPreviewWidget(QWidget):
    """Inline video preview with trim selection.

    Composition:

    * top: ``QVideoWidget`` for the actual frame display
    * bottom: play/pause + current/duration label + "Use trim →
      X to Y" button + range slider

    Signals:

    * ``trim_confirmed(start_ms, end_ms)`` — user pressed the
      "Use trim" button. The host panel converts this into a
      prompt-fill action.
    * ``playback_error(message)`` — surfaces media-player errors
      (codec missing, file gone, format unsupported) so the host
      can show the failure instead of a silent black rectangle.
    """

    trim_confirmed = pyqtSignal(int, int)
    playback_error = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._wire_player()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Video display area — a stacked container with the
        # clickable video widget at the bottom and a translucent
        # "▶" overlay on top. Both layers sit at the same position
        # via ``QStackedLayout.StackingMode.StackAll``, and the
        # overlay sets ``WA_TransparentForMouseEvents`` so clicks
        # fall through to the video widget below. Net effect: the
        # user clicks anywhere on the video to play / pause, and
        # the overlay is just visual feedback.
        self._video_container = QWidget()
        self._video_container.setMinimumHeight(220)
        self._video_container.setStyleSheet("background: #000000;")
        self._video_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        stack = QStackedLayout(self._video_container)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)

        self._video = _ClickableVideoWidget()
        self._video.clicked.connect(self._toggle_play)
        stack.addWidget(self._video)

        # Overlay play indicator. Centred in the stack via the
        # alignment QSS — keeping the QLabel itself small means
        # only the icon bubble is visible, not a full-frame
        # translucent layer. ``WA_TransparentForMouseEvents`` lets
        # the click pass through to the video widget below.
        self._play_overlay = QLabel("▶")
        self._play_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._play_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._play_overlay.setFixedSize(96, 96)
        self._play_overlay.setStyleSheet(
            "QLabel {"
            "  color: rgba(255, 255, 255, 230);"
            "  background: rgba(0, 0, 0, 140);"
            "  border: 2px solid rgba(255, 255, 255, 200);"
            "  border-radius: 48px;"
            "  font-size: 42px;"
            "  padding-left: 8px;"  # nudge the ▶ glyph optically
            "}"
        )
        # Wrapper holds the fixed-size overlay centred inside the
        # stacking layer — without the wrapper the overlay would
        # stretch to fill the whole container.
        overlay_wrap = QWidget()
        overlay_wrap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        wrap_layout = QVBoxLayout(overlay_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(self._play_overlay, alignment=Qt.AlignmentFlag.AlignCenter)
        stack.addWidget(overlay_wrap)

        outer.addWidget(self._video_container, stretch=1)

        # Range slider — visible only when a clip is loaded with a
        # non-zero duration, so the controls aren't crowded.
        self._slider = RangeSlider()
        outer.addWidget(self._slider)

        # Controls row: play/pause, time, "use trim" button.
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(36, 30)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; border: 1px solid "
            f"{tc.get('border_subtle')}; border-radius: 4px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        self._play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_btn)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-family: monospace; font-size: {tc.FONT_SM}px; "
            f"background: transparent;"
        )
        controls.addWidget(self._time_label)

        controls.addStretch(1)

        # "Use trim" button — appears unconditionally once a clip is
        # loaded. Reads its label live as the user drags handles, so
        # the user sees the exact range they're about to commit.
        self._use_trim_btn = QPushButton("Use trim 0:00 → 0:00")
        self._use_trim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_trim_btn.setStyleSheet(
            f"QPushButton {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; "
            f"border-radius: 4px; padding: 5px 12px; "
            f"font-size: {tc.FONT_SM}px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
            f"QPushButton:disabled {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_muted')}; }}"
        )
        self._use_trim_btn.setEnabled(False)
        self._use_trim_btn.clicked.connect(self._on_use_trim)
        controls.addWidget(self._use_trim_btn)

        outer.addLayout(controls)

    def _wire_player(self) -> None:
        """Construct the QMediaPlayer + QAudioOutput and wire signals.

        The audio output is mandatory in Qt 6 — without one
        explicitly attached, the player runs muted **and** some
        backends refuse to start the video pipeline at all.
        """
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setVideoOutput(self._video)
        self._player.setAudioOutput(self._audio)
        # Modest default volume — full blast on first play is a
        # universally bad first impression.
        self._audio.setVolume(0.7)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        self._slider.playhead_clicked.connect(self._player.setPosition)
        self._slider.playhead_dragged.connect(self._player.setPosition)
        self._slider.range_changed.connect(self._on_range_changed)

    # ── Public API ────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """Point the player at a new file. Stops any current playback."""
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._use_trim_btn.setEnabled(False)
        self._use_trim_btn.setText("Use trim 0:00 → 0:00")

    def clear(self) -> None:
        """Tear down playback state — used when the user resets input."""
        self._player.stop()
        self._player.setSource(QUrl())
        self._slider.set_duration(0)
        self._use_trim_btn.setEnabled(False)
        self._use_trim_btn.setText("Use trim 0:00 → 0:00")
        self._time_label.setText("0:00 / 0:00")
        self._play_btn.setText("▶")
        # Overlay returns to its "paused / click to play" state
        # so the next loaded clip shows the affordance again.
        self._play_overlay.setVisible(True)

    @property
    def range_slider(self) -> RangeSlider:
        """Exposed for tests that need to drive the slider directly."""
        return self._slider

    # ── Internal slots ────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")
        # Overlay is a visual "click to play" cue — hide it while
        # the video is actually playing so it doesn't sit on top
        # of the content the user wants to watch.
        self._play_overlay.setVisible(not playing)

    def _on_position_changed(self, position_ms: int) -> None:
        self._slider.set_playhead(position_ms)
        self._refresh_time_label()
        # Auto-pause at end-of-trim. The user picked these bounds
        # deliberately; we don't want playback to drift past the
        # trim end while they're inspecting it.
        end = self._slider.end_ms
        if (
            end > 0
            and position_ms >= end
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._player.pause()

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._slider.set_duration(duration_ms)
        self._refresh_time_label()
        # Once the player reports a non-zero duration, the clip
        # decoded successfully — enable the trim-commit button.
        self._use_trim_btn.setEnabled(duration_ms > 0)
        self._refresh_use_trim_label()

    def _on_range_changed(self, start_ms: int, end_ms: int) -> None:
        self._refresh_use_trim_label()

    def _on_player_error(self, error: QMediaPlayer.Error, description: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        # ``description`` is sometimes empty when Qt forwards the
        # error from a gstreamer plugin failure — fall back to the
        # enum name so the user still sees something actionable.
        msg = description.strip() or error.name
        logger.warning("video preview player error: %s — %s", error.name, msg)
        self.playback_error.emit(msg)

    def _on_use_trim(self) -> None:
        self.trim_confirmed.emit(self._slider.start_ms, self._slider.end_ms)

    # ── Display helpers ───────────────────────────────────────────

    def _refresh_time_label(self) -> None:
        current = self._slider.playhead_ms
        total = self._slider.duration_ms
        self._time_label.setText(f"{_format_timecode_ms(current)} / {_format_timecode_ms(total)}")

    def _refresh_use_trim_label(self) -> None:
        start = self._slider.start_ms
        end = self._slider.end_ms
        self._use_trim_btn.setText(
            f"Use trim {_format_timecode_ms(start)} → {_format_timecode_ms(end)}"
        )
