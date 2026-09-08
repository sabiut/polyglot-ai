"""Editor integration for diagnostics: squiggles, counts, navigation, tooltips."""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.diagnostics import Diagnostic  # noqa: E402
from polyglot_ai.ui.panels.editor_panel import EditorPanel  # noqa: E402
from polyglot_ai.ui.panels.editor_tab import EditorTab  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _pump_until(pred, timeout_s=4.0):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _has_indicator(tab: EditorTab, line: int, col: int, indic: int) -> bool:
    editor = tab.editor
    pos = editor.positionFromLineIndex(line, col)
    return bool(editor.SendScintilla(editor.SCI_INDICATORVALUEAT, indic, pos))


class TestApply:
    def test_squiggles_and_counts(self, qapp, tmp_path):
        tab = EditorTab()
        tab.editor.setText("import os\nx = (\n")
        tab._file_path = tmp_path / "a.py"
        seen = []
        tab.diagnostics_changed.connect(lambda e, w: seen.append((e, w)))
        tab._apply_diagnostics(
            [
                Diagnostic(1, 8, 1, 10, "F401", "`os` imported but unused", "warning", True),
                Diagnostic(2, 5, 2, 5, "invalid-syntax", "unexpected EOF", "error"),
            ]
        )
        assert seen == [(1, 1)]
        assert _has_indicator(tab, 0, 7, EditorTab._INDIC_WARNING)
        assert not _has_indicator(tab, 0, 1, EditorTab._INDIC_WARNING)
        # zero-width syntax error is stretched so it's visible
        assert _has_indicator(tab, 1, 4, EditorTab._INDIC_ERROR)

    def test_clearing_removes_squiggles(self, qapp, tmp_path):
        tab = EditorTab()
        tab.editor.setText("import os\n")
        tab._apply_diagnostics([Diagnostic(1, 8, 1, 10, "F401", "unused", "warning", True)])
        assert _has_indicator(tab, 0, 7, EditorTab._INDIC_WARNING)
        tab._apply_diagnostics([])
        assert not _has_indicator(tab, 0, 7, EditorTab._INDIC_WARNING)
        assert tab.diagnostics == []


class TestNavigation:
    def test_goto_next_problem_wraps(self, qapp):
        tab = EditorTab()
        tab.editor.setText("a\nb\nc\nd\n")
        tab._apply_diagnostics(
            [
                Diagnostic(2, 1, 2, 2, "X", "second", "warning"),
                Diagnostic(4, 1, 4, 2, "X", "fourth", "error"),
            ]
        )
        tab.editor.setCursorPosition(0, 0)
        assert tab.goto_next_problem()
        assert tab.editor.getCursorPosition() == (1, 0)
        assert tab.goto_next_problem()
        assert tab.editor.getCursorPosition() == (3, 0)
        assert tab.goto_next_problem()  # wraps
        assert tab.editor.getCursorPosition() == (1, 0)

    def test_no_problems_returns_false(self, qapp):
        tab = EditorTab()
        assert tab.goto_next_problem() is False


class TestLiveRuff:
    @pytest.mark.skipif(
        __import__("polyglot_ai.core.diagnostics", fromlist=["find_ruff"]).find_ruff() is None,
        reason="ruff not installed",
    )
    def test_loading_a_broken_python_file_produces_an_error(self, qapp, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text("def f(:\n    pass\n")
        tab = EditorTab()
        tab.load(path)
        assert _pump_until(lambda: any(d.severity == "error" for d in tab.diagnostics))
        assert _has_indicator(tab, 0, 6, EditorTab._INDIC_ERROR)


class TestPanelForwarding:
    def test_panel_emits_counts_for_current_tab(self, qapp, tmp_path):
        panel = EditorPanel()
        path = tmp_path / "x.txt"
        path.write_text("hello\n")
        panel.open_file(path)
        seen = []
        panel.problems_changed.connect(lambda e, w: seen.append((e, w)))
        tab = panel.get_current_tab()
        assert isinstance(tab, EditorTab)
        tab._apply_diagnostics([Diagnostic(1, 1, 1, 2, "x", "bad", "error")])
        assert seen == [(1, 0)]
        assert panel.current_problem_counts() == (1, 0)
        assert panel.goto_next_problem() is True
