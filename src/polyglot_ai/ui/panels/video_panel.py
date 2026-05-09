"""Video panel — pick a clip, describe an edit, hand off to the AI.

Mirrors the Arduino panel pattern: the activity-bar entry pops a
standalone window (the editor would be cramped in the 200 px
sidebar), and inside that window a three-step wizard walks the
user from "no video loaded" to "AI is processing your edit."

Architectural choice: instead of building a full timeline editor
or implementing every operation natively, the panel hands the
user's natural-language request off to the chat panel, where the
AI plans an ``ffmpeg`` command and the existing ``shell_exec``
approval flow runs it. Net effect — anything an LLM can describe
via ffmpeg works on day one (trim, scale, format conversion,
audio extract, watermark, slideshow, frame extraction, …) without
us having to invent a parallel UI for each operation.

The hand-off is the same one the Arduino panel uses for "Ask AI
for help": ``prefill_input`` populates the chat textarea and
``send_button.click()`` submits it. That gives us free reuse of
the existing approval dialog, tool-call streaming, and progress
feed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.dependency_check import find_executable
from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)


# File-picker filter — both the input picker and the output picker
# share this list. Order matters: the most common first means the
# default-selected filter is the right one for almost everyone.
_VIDEO_FILTER = "Video files (*.mp4 *.mov *.mkv *.webm *.avi *.flv *.wmv *.m4v);;All files (*)"

# Recognised video extensions for drag-and-drop filtering. We could
# accept anything and let ffprobe sort it out, but a quick suffix
# check rejects obvious wrong drops (a .pdf, a folder, etc.) before
# we hit the picker styling and avoids a confusing "not a video"
# error after the fact.
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".m4v"}


@dataclass(frozen=True)
class VideoMetadata:
    """Subset of ffprobe output we surface in the panel.

    Keep this minimal — every field we add is one more line in the
    UI and one more thing that might be missing for a weird input.
    Duration / resolution / fps / video codec / audio codec / file
    size cover the questions a user actually asks ("is this 4K?
    how long is it? what's the format?") without crowding the
    layout.
    """

    duration_s: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    size_bytes: int

    def short_summary(self) -> str:
        """One-line, theme-agnostic markup for the input label."""
        parts: list[str] = []
        if self.width and self.height:
            parts.append(f"{self.width}×{self.height}")
        if self.fps:
            parts.append(f"{self.fps:.0f} fps" if self.fps >= 1 else f"{self.fps:.2f} fps")
        if self.duration_s:
            parts.append(_format_duration(self.duration_s))
        codec_bits = [c for c in (self.video_codec, self.audio_codec) if c]
        if codec_bits:
            parts.append(" / ".join(codec_bits))
        if self.size_bytes:
            parts.append(_format_bytes(self.size_bytes))
        return "  ·  ".join(parts) if parts else "(metadata unavailable)"


def _format_duration(seconds: float) -> str:
    """Render seconds as ``H:MM:SS`` or ``M:SS``, no leading zeros for hours."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_bytes(n: int) -> str:
    """Compact size — ``41.2 MB`` rather than long byte counts."""
    if n < 1024:
        return f"{n} B"
    units = ("KB", "MB", "GB", "TB")
    val = float(n)
    for unit in units:
        val /= 1024
        if val < 1024 or unit == "TB":
            return f"{val:.1f} {unit}"
    return f"{val:.1f} TB"  # pragma: no cover — defensive


def probe_video(path: Path, *, timeout: float = 5.0) -> VideoMetadata | None:
    """Run ``ffprobe`` against ``path`` and parse the relevant fields.

    Returns ``None`` when ffprobe isn't installed or the file
    can't be read — the panel falls back to the bare filename in
    that case so the picker still feels responsive. The
    sub-second timeout (5 s by default) keeps the GUI responsive
    even on a hostile input; ffprobe usually finishes in <100 ms
    for normal media.

    Synchronous on purpose. ffprobe is fast and the picker dialog
    is modal — there's no UI thread to block past dialog-dismiss
    time, and going async here would add complexity for no win.
    """
    ffprobe = find_executable("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("ffprobe failed for %s: %s", path, exc)
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = video_stream.get("width") if video_stream else None
    height = video_stream.get("height") if video_stream else None
    video_codec = video_stream.get("codec_name") if video_stream else None
    audio_codec = audio_stream.get("codec_name") if audio_stream else None

    fps: float | None = None
    if video_stream:
        # ffprobe reports fps as a fraction string like "30000/1001"
        # for NTSC variants. Try ``r_frame_rate`` first (real frame
        # rate, accounts for VFR), then fall back to
        # ``avg_frame_rate``.
        for key in ("r_frame_rate", "avg_frame_rate"):
            raw = video_stream.get(key)
            if not raw or "/" not in raw:
                continue
            try:
                num, denom = raw.split("/", 1)
                num_f = float(num)
                denom_f = float(denom)
                if denom_f == 0:
                    continue
                fps = num_f / denom_f
                break
            except ValueError:
                continue

    duration_raw = fmt.get("duration") or (video_stream or {}).get("duration")
    duration_s: float | None = None
    if duration_raw:
        try:
            duration_s = float(duration_raw)
        except (ValueError, TypeError):
            pass

    try:
        size_bytes = int(fmt.get("size") or path.stat().st_size)
    except (ValueError, TypeError, OSError):
        size_bytes = 0

    return VideoMetadata(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        size_bytes=size_bytes,
    )


class _StepCard(QFrame):
    """A numbered card section, matching the Arduino panel's style.

    The visual hierarchy (number badge, title, body) makes the
    wizard feel the same across both panels — kid-friendly enough
    for someone who's never edited video, structured enough for an
    experienced user to skim.
    """

    def __init__(self, number: int, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoStepCard")
        self.setStyleSheet(
            f"#videoStepCard {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_subtle')}; "
            f"border-radius: 8px; padding: 14px 18px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # Header row — number badge + title.
        header = QHBoxLayout()
        header.setSpacing(10)
        badge = QLabel(str(number))
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; "
            f"border-radius: 14px; font-size: {tc.FONT_MD}px; "
            "font-weight: 700;"
        )
        header.addWidget(badge)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_LG}px; font-weight: 600; "
            "background: transparent;"
        )
        header.addWidget(title_lbl, stretch=1)
        outer.addLayout(header)

        # Body container — caller adds widgets here.
        self._body = QVBoxLayout()
        self._body.setSpacing(8)
        outer.addLayout(self._body)

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._body.addLayout(layout)


class VideoPanel(QWidget):
    """Pick a clip, describe what to do, hand off to the AI.

    Three-step layout:

    1. **Your clip** — file picker for the input video. Shows the
       resolved path; clears on Reset.
    2. **What to do** — a multi-line text area where the user
       describes the edit in plain language. Examples on the
       placeholder text.
    3. **Process** — one big button that opens an output-file
       picker (the user chose this in the design discussion —
       per-edit destination, no surprise overwrites) then composes
       a structured prompt and pushes it into the chat panel,
       which submits it for the AI to plan an ``ffmpeg`` command.

    Output policy is "user picks each time" — see
    ``_pick_output_path``. The picker pre-suggests
    ``<input>-edited<ext>`` next to the source so the common case
    (just save it next to the original) is one Enter press, but
    nothing is created until the user confirms.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._input_path: Path | None = None
        # Cached metadata for the active clip — populated by
        # ``_apply_input_path`` when the user picks or drops a file.
        # Surfaced both in the input label (visible at-a-glance)
        # and in the AI prompt sent on Process (so the model can
        # quote duration / resolution back without having to ask).
        self._input_metadata: VideoMetadata | None = None
        # Accept drag-and-drop of video files anywhere on the
        # panel. Quicker than the picker for the common case
        # (drag from a file manager). The actual filtering happens
        # in ``dragEnterEvent`` / ``dropEvent``.
        self.setAcceptDrops(True)
        self._setup_ui()
        self._refresh_process_button()

    # ── UI ─────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)
        self.setStyleSheet(f"background: {tc.get('bg_base')};")

        # Title row.
        title = QLabel("Edit a video with AI")
        title.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_XL}px; font-weight: 700; "
            "background: transparent;"
        )
        outer.addWidget(title)

        subtitle = QLabel(
            "Pick a clip, say what you want done, and the AI will plan an "
            "ffmpeg command — you approve before anything runs."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_MD}px; background: transparent;"
        )
        outer.addWidget(subtitle)

        # Step 1 — pick a clip.
        outer.addWidget(self._build_step1_input())

        # Step 2 — describe the edit.
        outer.addWidget(self._build_step2_prompt())

        # Step 3 — process button + status feed.
        outer.addWidget(self._build_step3_process())

        outer.addStretch(1)

    def _build_step1_input(self) -> _StepCard:
        card = _StepCard(1, "Your clip")

        self._input_label = QLabel(
            "No video loaded yet. <span style='color:" + tc.get("text_muted") + ";"
            f"font-size:{tc.FONT_SM}px;'>"
            "Click <b>Pick a video</b> below, or drop a file anywhere on this window."
            "</span>"
        )
        self._input_label.setWordWrap(True)
        self._input_label.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_MD}px; background: transparent;"
        )
        card.add_widget(self._input_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        pick_btn = QPushButton("📂  Pick a video")
        pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pick_btn.setStyleSheet(self._primary_button_qss())
        pick_btn.clicked.connect(self._on_pick_video)
        button_row.addWidget(pick_btn)

        clear_btn = QPushButton("Reset")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(self._secondary_button_qss())
        clear_btn.clicked.connect(self._on_clear_input)
        button_row.addWidget(clear_btn)

        button_row.addStretch(1)
        card.add_layout(button_row)
        return card

    def _build_step2_prompt(self) -> _StepCard:
        card = _StepCard(2, "What to do")

        self._prompt_input = QTextEdit()
        self._prompt_input.setPlaceholderText(
            "Describe the edit in plain language. Examples:\n"
            "  • Trim from 0:30 to 1:15 and add a half-second fade-out\n"
            "  • Scale to 1080p and convert to MP4 with H.264\n"
            "  • Extract just the audio as a 192 kbps MP3\n"
            "  • Burn the subtitles in subs.srt into the video\n"
            "  • Crop to a 1:1 square centered on the frame"
        )
        self._prompt_input.setMinimumHeight(120)
        mono = QFont("Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.SansSerif)
        self._prompt_input.setFont(mono)
        self._prompt_input.setStyleSheet(
            f"QTextEdit {{ background: {tc.get('bg_input_deep')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_subtle')}; border-radius: 6px; "
            f"padding: 10px; font-size: {tc.FONT_MD}px; }}"
            f"QTextEdit:focus {{ border-color: {tc.get('accent_primary')}; }}"
        )
        self._prompt_input.textChanged.connect(self._refresh_process_button)
        card.add_widget(self._prompt_input)
        return card

    def _build_step3_process(self) -> _StepCard:
        card = _StepCard(3, "Process")

        self._process_btn = QPushButton("Process with AI")
        self._process_btn.setMinimumHeight(48)
        self._process_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._process_btn.clicked.connect(self._on_process)
        card.add_widget(self._process_btn)

        self._status = QPlainTextEdit()
        self._status.setReadOnly(True)
        self._status.setMaximumHeight(180)
        self._status.setPlaceholderText("The AI's plan and ffmpeg progress will appear here.")
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._status.setFont(mono)
        self._status.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_input_deep')}; "
            f"color: {tc.get('text_primary')}; border: 1px solid "
            f"{tc.get('border_subtle')}; border-radius: 6px; padding: 8px; }}"
        )
        card.add_widget(self._status)
        return card

    def _primary_button_qss(self) -> str:
        return (
            f"QPushButton {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; "
            f"border-radius: 6px; padding: 8px 14px; "
            f"font-size: {tc.FONT_MD}px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tc.get('accent_primary_hover')}; }}"
            f"QPushButton:disabled {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_muted')}; }}"
        )

    def _secondary_button_qss(self) -> str:
        return (
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            f"border-radius: 6px; padding: 8px 14px; "
            f"font-size: {tc.FONT_MD}px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )

    # ── Handlers ───────────────────────────────────────────────────

    def _on_pick_video(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Pick a video to edit",
            str(Path.home()),
            _VIDEO_FILTER,
        )
        if not path_str:
            return
        self._apply_input_path(Path(path_str))

    def _apply_input_path(self, path: Path) -> None:
        """Set the active clip and refresh the UI.

        Shared by the picker dialog and the drag-and-drop drop
        handler so both entry points produce identical state —
        same metadata probe, same label rendering, same readiness
        recompute. Resolves the path to absolute first so a
        clipboard/drop relative path doesn't surprise the AI prompt
        composer later.
        """
        self._input_path = path.expanduser().resolve()
        # ffprobe is cheap (under ~100 ms for normal media); doing
        # it inline keeps the picker→info-display step one mental
        # beat. Falls back to ``None`` cleanly when ffprobe isn't
        # installed.
        self._input_metadata = probe_video(self._input_path)

        meta_html = ""
        if self._input_metadata is not None:
            meta_html = (
                f"<br><span style='color:{tc.get('accent_primary')}; "
                f"font-size:{tc.FONT_SM}px;'>"
                f"{self._input_metadata.short_summary()}</span>"
            )
        self._input_label.setText(
            f"<b>{self._input_path.name}</b>"
            f"<br><span style='color:{tc.get('text_muted')}; "
            f"font-size:{tc.FONT_SM}px;'>{self._input_path}</span>"
            f"{meta_html}"
        )
        self._input_label.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_MD}px; background: transparent;"
        )
        self._refresh_process_button()

    def _on_clear_input(self) -> None:
        self._input_path = None
        self._input_metadata = None
        self._input_label.setText(
            "No video loaded yet. <span style='color:" + tc.get("text_muted") + ";"
            f"font-size:{tc.FONT_SM}px;'>"
            "Click <b>Pick a video</b> below, or drop a file anywhere on this window."
            "</span>"
        )
        self._input_label.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_MD}px; background: transparent;"
        )
        self._refresh_process_button()

    # ── Drag-and-drop ──────────────────────────────────────────────
    #
    # Accept video files dropped anywhere on the panel. Delegates
    # to the same ``_apply_input_path`` the picker uses, so the
    # post-load state is identical regardless of how the file
    # arrived. Filtering is suffix-based — quick reject for
    # obvious non-videos, and ffprobe will sort out the truly
    # weird cases on load.

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and Path(local).suffix.lower() in _VIDEO_EXTS:
                event.acceptProposedAction()
                return
        # No video found in the drop — let the event fall through
        # in case a child widget wants it.
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.suffix.lower() in _VIDEO_EXTS and path.is_file():
                self._apply_input_path(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _refresh_process_button(self) -> None:
        ready, why = self._readiness()
        self._process_btn.setEnabled(ready)
        self._process_btn.setStyleSheet(self._process_button_qss(ready))
        self._process_btn.setToolTip("" if ready else why)

    def _process_button_qss(self, enabled: bool) -> str:
        if enabled:
            return (
                f"QPushButton {{ background: {tc.get('accent_success')}; "
                f"color: #fff; border: none; border-radius: 8px; "
                f"font-size: {tc.FONT_LG}px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: #3a9970; }}"
            )
        return (
            f"QPushButton {{ background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_muted')}; border: 1px solid "
            f"{tc.get('border_secondary')}; border-radius: 8px; "
            f"font-size: {tc.FONT_LG}px; }}"
        )

    def _readiness(self) -> tuple[bool, str]:
        """Return ``(ready, reason_if_not)`` for the Process button."""
        if self._input_path is None:
            return False, "Pick a video to edit first."
        if not self._input_path.is_file():
            return False, f"Can't find {self._input_path} — pick again?"
        if not self._prompt_input.toPlainText().strip():
            return False, "Describe what you want done in step 2."
        if find_executable("ffmpeg") is None:
            return (
                False,
                "ffmpeg isn't installed yet. The first-launch dialog "
                "will help you install it (re-open the app to see it), "
                "or run: sudo apt install ffmpeg",
            )
        return True, ""

    def _on_process(self) -> None:
        ready, why = self._readiness()
        if not ready:
            self._append_status(why, kind="warn")
            return

        # Output destination — user picks each time, per the design
        # discussion. Pre-suggest ``<input>-edited<ext>`` next to
        # the source so the common case is one Enter press.
        assert self._input_path is not None
        suggested = self._input_path.parent / (
            self._input_path.stem + "-edited" + self._input_path.suffix
        )
        out_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save edited video as…",
            str(suggested),
            _VIDEO_FILTER,
        )
        if not out_str:
            return
        out_path = Path(out_str)

        prompt = self._prompt_input.toPlainText().strip()
        chat = self._find_chat_panel()
        if chat is None or not hasattr(chat, "prefill_input"):
            self._append_status(
                "Couldn't reach the chat panel. Open the chat tab once and try again.",
                kind="fail",
            )
            return

        # Compose a structured request. The AI sees:
        #   • the absolute input path (so it doesn't have to ask)
        #   • the absolute output path (so its ffmpeg command writes
        #     where the user wanted)
        #   • the user's natural-language description
        #   • an explicit nudge to use shell_exec + ffmpeg
        # The existing approval flow gates the command before
        # anything runs.
        # Build an optional "Source info:" block from ffprobe data
        # so the AI can reason about format-aware operations
        # without re-probing ("the clip is already 1080p — no
        # scale needed", "duration is 2:34 so a trim to 0:30-1:15
        # makes sense"). When ffprobe wasn't available or failed,
        # the line is omitted entirely rather than left blank.
        source_info_block = ""
        if self._input_metadata is not None:
            md = self._input_metadata
            details: list[str] = []
            if md.duration_s is not None:
                details.append(f"- Duration: {_format_duration(md.duration_s)}")
            if md.width and md.height:
                details.append(f"- Resolution: {md.width}×{md.height}")
            if md.fps:
                fps_label = f"{md.fps:.0f}" if md.fps >= 1 else f"{md.fps:.2f}"
                details.append(f"- Frame rate: {fps_label} fps")
            if md.video_codec:
                details.append(f"- Video codec: {md.video_codec}")
            if md.audio_codec:
                details.append(f"- Audio codec: {md.audio_codec}")
            if md.size_bytes:
                details.append(f"- File size: {_format_bytes(md.size_bytes)}")
            if details:
                source_info_block = "**Source info:**\n" + "\n".join(details) + "\n\n"

        message = (
            "Edit this video using `ffmpeg` via the `shell_exec` tool.\n"
            "\n"
            f"**Input:** `{self._input_path}`\n"
            f"**Output:** `{out_path}`\n"
            f"**Edit:** {prompt}\n"
            "\n"
            f"{source_info_block}"
            "Plan a single ffmpeg command (or a short pipeline) that "
            "reads the input and writes the output. Pick sensible "
            "encoder defaults (libx264 / aac for MP4 unless the user "
            "specified otherwise). Always wait for my approval on the "
            "shell_exec tool call before running."
        )
        chat.prefill_input(message)

        # Same pattern Arduino's "Ask AI for help" uses — programmatic
        # send so the user doesn't have to click twice. Defer one
        # event-loop turn so the prefill settles before the send slot
        # reads from the input.
        send_btn = getattr(chat, "send_button", None)
        if send_btn is not None:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(0, send_btn.click)

        self._raise_chat_window()
        self._append_status(
            f"Sent to AI: {prompt}\n→ Output will be: {out_path}",
            kind="ok",
        )

    def _raise_chat_window(self) -> None:
        host = self.window()
        parent = host.parent() if host is not None else None
        main_window = parent if parent is not None else None
        if main_window is None:
            return
        # Activate the chat tab if MainWindow has the helper.
        for attr in ("_show_chat_tab", "show_chat_tab"):
            fn = getattr(main_window, attr, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break
        try:
            main_window.raise_()
            main_window.activateWindow()
        except Exception:
            pass

    def _find_chat_panel(self):
        """Walk the parent chain to the chat panel.

        Same pattern the Arduino panel uses: this widget is hosted
        inside a ``VideoWindow`` whose parent is the ``MainWindow``;
        the chat panel lives on that parent under either
        ``_chat_panel`` or ``chat_panel`` depending on era.
        """
        host = self.window()
        parent = host.parent() if host is not None else None
        if parent is None:
            return None
        return getattr(parent, "_chat_panel", None) or getattr(parent, "chat_panel", None)

    def _append_status(self, message: str, kind: str = "info") -> None:
        prefix = {"ok": "✓ ", "fail": "✗ ", "warn": "⚠ ", "info": "› "}.get(kind, "› ")
        self._status.appendPlainText(f"{prefix}{message}")

    @property
    def has_ffmpeg(self) -> bool:
        """Surface ffmpeg availability to the host window's title."""
        return find_executable("ffmpeg") is not None


# ── Top-level window wrapper ───────────────────────────────────────


class VideoWindow(QWidget):
    """Standalone window hosting :class:`VideoPanel`.

    Mirrors :class:`ArduinoWindow`: the activity-bar entry pops
    this instead of toggling a sidebar — the editor wizard is too
    tall and tile-heavy for a 200 px sidebar pane to be useful.
    Created once per :class:`MainWindow` and re-shown on subsequent
    clicks so the loaded clip and prompt survive a close → reopen
    cycle.

    The owning ``MainWindow`` keeps a strong reference to avoid
    relying on Qt's parent ownership alone — without that, closing
    the window would let the panel be garbage-collected and the
    next open would create a fresh, empty one.
    """

    def __init__(self, panel: VideoPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Video Editor — Polyglot AI")
        self.resize(960, 760)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if parent is not None:
            icon = parent.windowIcon()
            if not icon.isNull():
                self.setWindowIcon(icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel)
        self._panel = panel

        # Friendly hint if ffmpeg is missing — first-time visitors
        # get told once instead of being mystified by a disabled
        # button later.
        if not shutil.which("ffmpeg") and find_executable("ffmpeg") is None:
            self._panel._append_status(
                "ffmpeg isn't on PATH. Install it with: sudo apt install ffmpeg "
                "(or your distro's equivalent). The first-launch dependency "
                "dialog covers this too.",
                kind="warn",
            )

    @property
    def panel(self) -> VideoPanel:
        return self._panel

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
