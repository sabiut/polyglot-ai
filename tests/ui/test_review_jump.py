"""Tests for click-to-jump on review-finding locations.

The review panel renders each finding's ``file:line`` as a flat
link-styled button. Clicking it must resolve the (repo-relative)
path against the project root and hand it to the wired editor panel
via ``open_file_at``. These tests pin:

- a click on the location button reaches the editor double with the
  absolute path and the finding's line;
- ``line=0`` findings open the file with no line (no scrolling);
- a finding whose file has since been deleted is a no-op, not a crash;
- no wired editor panel is also a no-op (panel-internal safety).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pytestqt")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

from polyglot_ai.core.review.models import (  # noqa: E402
    Category,
    ReviewFinding,
    ReviewResult,
    Severity,
)
from polyglot_ai.ui.panels.review_panel import ReviewPanel  # noqa: E402


class EditorDouble:
    """Records ``open_file_at`` calls in place of a real EditorPanel."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, int | None]] = []

    def open_file_at(self, path: Path, line: int | None = None) -> None:
        self.calls.append((path, line))


def _finding(file: str, line: int) -> ReviewFinding:
    return ReviewFinding(
        file=file,
        line=line,
        severity=Severity.HIGH,
        category=Category.BUG,
        title="Possible off-by-one",
        body="The loop bound skips the final element.",
    )


def _result(*findings: ReviewFinding) -> ReviewResult:
    return ReviewResult(summary="1 issue found", findings=list(findings), files_reviewed=1)


def _location_button(panel: ReviewPanel, text: str) -> QPushButton:
    matches = [b for b in panel.findChildren(QPushButton) if b.text() == text]
    assert matches, f"no location button with text {text!r}"
    return matches[0]


@pytest.fixture
def panel(qtbot, tmp_path):
    p = ReviewPanel()
    qtbot.addWidget(p)
    p.set_project_root(str(tmp_path))
    return p


def test_click_opens_file_at_line(panel, qtbot, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n" * 30)
    editor = EditorDouble()
    panel.set_editor_panel(editor)

    panel._display_results(_result(_finding("app.py", 12)))
    btn = _location_button(panel, "app.py:12")
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)

    assert editor.calls == [(tmp_path / "app.py", 12)]


def test_line_zero_opens_without_line(panel, qtbot, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    editor = EditorDouble()
    panel.set_editor_panel(editor)

    panel._display_results(_result(_finding("app.py", 0)))
    # A zero line renders as just the file name (no ":0" noise).
    btn = _location_button(panel, "app.py")
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)

    assert editor.calls == [(tmp_path / "app.py", None)]


def test_missing_file_does_not_crash_or_open(panel, qtbot):
    editor = EditorDouble()
    panel.set_editor_panel(editor)

    panel._display_results(_result(_finding("gone/forever.py", 3)))
    btn = _location_button(panel, "gone/forever.py:3")
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)

    assert editor.calls == []


def test_click_without_editor_panel_is_noop(panel, qtbot, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")

    panel._display_results(_result(_finding("app.py", 1)))
    btn = _location_button(panel, "app.py:1")
    # No set_editor_panel() — the click must simply do nothing.
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
