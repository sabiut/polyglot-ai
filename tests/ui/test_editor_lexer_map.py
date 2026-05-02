"""Tests for the editor's file-extension → lexer mapping.

The map drives syntax highlighting in :class:`EditorTab`. Missing
an extension means files of that type render as undifferentiated
white text — easy to miss until a user reports it for a specific
language. These tests pin the slugs we care about so a regression
catches in CI before users do.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from PyQt6.Qsci import QsciLexerCPP  # noqa: E402

from polyglot_ai.ui.panels.editor_tab import LEXER_MAP  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class TestArduinoLexerMapping:
    """Arduino sketches are C++ — without this the editor showed
    ``.ino`` files as plain white text and users couldn't tell
    keywords from identifiers.
    """

    def test_ino_maps_to_cpp(self, qapp):
        assert LEXER_MAP.get(".ino") is QsciLexerCPP, (
            "Arduino .ino files must use the C++ lexer for syntax highlighting"
        )

    def test_legacy_pde_maps_to_cpp(self, qapp):
        assert LEXER_MAP.get(".pde") is QsciLexerCPP, (
            "Legacy Arduino / Processing .pde files should also use the C++ lexer"
        )


class TestCommonExtensionsAreMapped:
    """A coverage spot-check: every language we advertise as
    'supported' should have at least one extension wired up."""

    @pytest.mark.parametrize(
        "ext",
        [
            ".py",
            ".js",
            ".ts",
            ".json",
            ".html",
            ".css",
            ".md",
            ".sh",
            ".sql",
            ".yaml",
            ".c",
            ".cpp",
            ".h",
            ".java",
            ".go",
            ".rs",
            ".ino",
        ],
    )
    def test_extension_has_a_lexer(self, qapp, ext: str) -> None:
        assert ext in LEXER_MAP, f"{ext} files won't get syntax highlighting"
        # The values should be classes, not instances.
        assert isinstance(LEXER_MAP[ext], type), (
            f"LEXER_MAP[{ext!r}] should be a class, got {LEXER_MAP[ext]!r}"
        )
