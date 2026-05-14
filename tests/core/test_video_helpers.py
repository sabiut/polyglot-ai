"""Unit tests for the helpers inside ``ui/panels/video_panel.py``.

These cover the pure-Python pieces — duration / size formatters,
ffprobe JSON parsing, and the ``VideoMetadata.short_summary``
rendering — that don't need a Qt event loop. The Qt-dependent
panel behaviour (chips, drag-drop, readiness state machine) lives
in ``tests/ui/test_video_panel.py``.

The panel itself is 800+ lines of Qt code; carving out the pure
helpers into focused tests means they can run in CI without an X
server and regressions get pinpointed quickly.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch


from polyglot_ai.ui.panels.video_panel import (
    QUICK_CHIPS,
    VideoMetadata,
    _format_bytes,
    _format_duration,
    probe_video,
)


# ── Duration formatter ─────────────────────────────────────────────


def test_format_duration_under_a_minute():
    assert _format_duration(45.2) == "0:45"


def test_format_duration_minutes_and_seconds():
    # 2:34 — common youtube-clip range, no hour component
    assert _format_duration(154) == "2:34"


def test_format_duration_hours_format():
    # Hour component appears with no leading zero (per docstring)
    assert _format_duration(3661) == "1:01:01"


def test_format_duration_rounds_fractional_seconds():
    # 30.6 → 31; ensures we don't truncate
    assert _format_duration(30.6) == "0:31"


def test_format_duration_zero():
    assert _format_duration(0) == "0:00"


# ── Size formatter ─────────────────────────────────────────────────


def test_format_bytes_below_one_kb():
    assert _format_bytes(512) == "512 B"


def test_format_bytes_kilobytes():
    assert _format_bytes(2048) == "2.0 KB"


def test_format_bytes_megabytes():
    # 41.2 MB target — the example in the docstring
    assert _format_bytes(41_200_000) == "39.3 MB"


def test_format_bytes_gigabytes():
    assert _format_bytes(5_000_000_000) == "4.7 GB"


# ── VideoMetadata.short_summary ────────────────────────────────────


def test_short_summary_full_metadata():
    md = VideoMetadata(
        duration_s=154.0,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=41_200_000,
    )
    summary = md.short_summary()
    assert "1920×1080" in summary
    assert "30 fps" in summary
    assert "2:34" in summary
    assert "h264 / aac" in summary
    assert "39.3 MB" in summary


def test_short_summary_missing_audio():
    """A silent video (no audio stream) shouldn't render a trailing
    ' / ' in the codec block."""
    md = VideoMetadata(
        duration_s=10.0,
        width=640,
        height=480,
        fps=24.0,
        video_codec="h264",
        audio_codec=None,
        size_bytes=1_000_000,
    )
    summary = md.short_summary()
    assert "h264" in summary
    # No bare " / " stranded by the missing audio
    assert "h264 / " not in summary


def test_short_summary_fractional_fps_uses_two_decimals():
    """Variable-frame-rate cameras report fps like 23.976 — show two
    decimals when sub-1 fps, single decimal otherwise. Boundary at 1.0."""
    md = VideoMetadata(
        duration_s=10.0,
        width=1280,
        height=720,
        fps=0.5,
        video_codec="h264",
        audio_codec="aac",
        size_bytes=1_000_000,
    )
    assert "0.50 fps" in md.short_summary()


def test_short_summary_empty_metadata_returns_placeholder():
    md = VideoMetadata(
        duration_s=None,
        width=None,
        height=None,
        fps=None,
        video_codec=None,
        audio_codec=None,
        size_bytes=0,
    )
    assert md.short_summary() == "(metadata unavailable)"


# ── probe_video ────────────────────────────────────────────────────


def _ffprobe_stub_output(**overrides) -> bytes:
    """Build a stub ffprobe JSON blob with sensible defaults.

    Tests override fields via kwargs instead of authoring a full
    JSON document each time — keeps each test focused on the one
    field it cares about.
    """
    data = {
        "format": {
            "duration": "154.0",
            "size": "41200000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
            },
        ],
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


def test_probe_video_returns_none_when_ffprobe_missing(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")  # Content doesn't matter — ffprobe stub is mocked.
    with patch("polyglot_ai.ui.panels.video_panel.find_executable", return_value=None):
        assert probe_video(f) is None


def test_probe_video_parses_typical_h264_clip(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    fake_result = MagicMock(returncode=0, stdout=_ffprobe_stub_output())
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        md = probe_video(f)
    assert md is not None
    assert md.width == 1920
    assert md.height == 1080
    assert md.fps == 30.0
    assert md.video_codec == "h264"
    assert md.audio_codec == "aac"
    assert md.duration_s == 154.0
    assert md.size_bytes == 41_200_000


def test_probe_video_parses_ntsc_fractional_fps(tmp_path):
    """29.97 NTSC arrives as ``30000/1001`` — parse the fraction
    correctly rather than treating the slash as a string."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    stub = _ffprobe_stub_output(
        streams=[
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "r_frame_rate": "30000/1001",
            }
        ]
    )
    fake_result = MagicMock(returncode=0, stdout=stub)
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        md = probe_video(f)
    assert md is not None
    assert md.fps is not None
    assert abs(md.fps - 29.97) < 0.01


def test_probe_video_handles_zero_denominator_fps(tmp_path):
    """Stills (single-frame images that ffprobe also handles) sometimes
    report ``r_frame_rate="0/0"``. Parse without ZeroDivisionError —
    fps stays ``None`` rather than crashing the panel load."""
    f = tmp_path / "still.png"
    f.write_bytes(b"\x00")
    stub = _ffprobe_stub_output(
        streams=[
            {
                "codec_type": "video",
                "codec_name": "png",
                "width": 800,
                "height": 600,
                "r_frame_rate": "0/0",
                "avg_frame_rate": "0/0",
            }
        ]
    )
    fake_result = MagicMock(returncode=0, stdout=stub)
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        md = probe_video(f)
    assert md is not None
    assert md.fps is None  # No fps because denom is 0
    assert md.width == 800


def test_probe_video_falls_back_to_avg_frame_rate(tmp_path):
    """If ``r_frame_rate`` is missing/invalid, ``avg_frame_rate`` is
    the next fallback per the implementation."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    stub = _ffprobe_stub_output(
        streams=[
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                # r_frame_rate intentionally absent
                "avg_frame_rate": "24/1",
            }
        ]
    )
    fake_result = MagicMock(returncode=0, stdout=stub)
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        md = probe_video(f)
    assert md is not None
    assert md.fps == 24.0


def test_probe_video_returns_none_on_nonzero_returncode(tmp_path):
    """ffprobe exits non-zero for unreadable / corrupt media. The
    panel falls back to a path-only label in that case."""
    f = tmp_path / "junk.mp4"
    f.write_bytes(b"not a video")
    fake_result = MagicMock(returncode=1, stdout=b"")
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        assert probe_video(f) is None


def test_probe_video_returns_none_on_timeout(tmp_path):
    """ffprobe shouldn't hang the picker. The implementation passes a
    5 s timeout to ``subprocess.run`` — when it expires we get
    ``TimeoutExpired`` and surface ``None``."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=5),
        ),
    ):
        assert probe_video(f) is None


def test_probe_video_returns_none_on_invalid_json(tmp_path):
    """ffprobe sometimes returns broken JSON (corrupt media, weird
    encodings) — fail gracefully rather than crash the picker."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00")
    fake_result = MagicMock(returncode=0, stdout=b"{not json{")
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        assert probe_video(f) is None


def test_probe_video_uses_stat_size_when_format_size_missing(tmp_path):
    """Some ffprobe builds don't surface format.size on certain
    containers. Fall back to the OS stat() size rather than 0."""
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 1234)
    stub = json.dumps(
        {
            "format": {"duration": "10.0"},  # No 'size'
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
    ).encode("utf-8")
    fake_result = MagicMock(returncode=0, stdout=stub)
    with (
        patch(
            "polyglot_ai.ui.panels.video_panel.find_executable",
            return_value="/usr/bin/ffprobe",
        ),
        patch(
            "polyglot_ai.ui.panels.video_panel.subprocess.run",
            return_value=fake_result,
        ),
    ):
        md = probe_video(f)
    assert md is not None
    assert md.size_bytes == 1234  # From file stat, not ffprobe


# ── QUICK_CHIPS catalog ────────────────────────────────────────────


def test_quick_chips_are_well_formed():
    """Each chip must have icon, label, and template strings."""
    assert len(QUICK_CHIPS) >= 5, "Expected at least 5 quick-action chips"
    seen_labels: set[str] = set()
    for icon, label, template in QUICK_CHIPS:
        assert icon, "chip icon is empty"
        assert label, "chip label is empty"
        assert template, "chip template is empty"
        assert label not in seen_labels, f"duplicate chip label: {label}"
        seen_labels.add(label)
        # Templates should be plain English, not contain placeholder
        # syntax that the AI can't interpret (no ``{var}`` style).
        assert "{{" not in template, (
            f"chip {label!r}: template contains Jinja-style placeholder — "
            f"the planner has no substitution context to resolve it"
        )


def test_quick_chips_include_common_operations():
    """A regression test for the catalog itself — every common
    ffmpeg operation should have a chip. Adding a new one is fine
    (test only checks the minimum); removing one breaks the test
    so you remember to document it."""
    labels = {label for _, label, _ in QUICK_CHIPS}
    for required in ("Trim", "Resize", "Audio", "Compress", "GIF"):
        assert required in labels, f"missing quick-action chip: {required!r}"
