"""Tests for the install-progress marker parser.

The dialog and the installer share one regex (``parse_progress_marker``).
Pinning the format here means a future "let's tweak the marker"
PR will fail in CI before producing a dialog that silently never
shows progress.
"""

from __future__ import annotations

from polyglot_ai.core.dependency_check import (
    InstallProgress,
    parse_progress_marker,
)


class TestParseProgressMarker:
    def test_normal_tick(self):
        result = parse_progress_marker("@@PROGRESS@@ 2/3 arduino-cli")
        assert result is not None
        assert result.current == 2
        assert result.total == 3
        assert result.slug == "arduino-cli"
        assert result.done is False

    def test_done_marker(self):
        result = parse_progress_marker("@@PROGRESS@@ done")
        assert result is not None
        assert result.done is True

    def test_done_with_trailing_whitespace(self):
        # Shells often trail a newline or two — the regex must not
        # be picky about it.
        for line in ("@@PROGRESS@@ done", "@@PROGRESS@@ done\n", "@@PROGRESS@@ done   "):
            result = parse_progress_marker(line)
            assert result is not None and result.done, line

    def test_slug_with_hyphens(self):
        # Real dependency keys include hyphens (``arduino-cli``,
        # ``github-cli``); make sure the parser accepts them.
        result = parse_progress_marker("@@PROGRESS@@ 1/5 github-cli")
        assert result is not None
        assert result.slug == "github-cli"

    def test_unrelated_line_returns_none(self):
        # An apt status line should never be mistaken for a marker.
        for line in (
            "Reading package lists... Done",
            "==> Installing arduino-cli",
            "@@progress@@ 1/2 foo",  # case-sensitive sentinel
            "",
            "@@PROGRESS@@",  # no payload
            "@@PROGRESS@@ 1/",  # malformed
            "@@PROGRESS@@ /3 foo",  # malformed
        ):
            assert parse_progress_marker(line) is None, line

    def test_returns_install_progress_dataclass(self):
        # ``InstallProgress`` is the public type the dialog imports.
        # Pin its identity here so a refactor that swaps it out for
        # a tuple breaks loudly.
        result = parse_progress_marker("@@PROGRESS@@ 1/1 foo")
        assert isinstance(result, InstallProgress)
