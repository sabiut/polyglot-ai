"""Editor diagnostics engine: ruff, JSON and YAML."""

from __future__ import annotations

import pytest

from polyglot_ai.core import diagnostics as diag

ruff_present = diag.find_ruff() is not None


class TestRuff:
    @pytest.mark.skipif(not ruff_present, reason="ruff not installed")
    def test_syntax_error_is_an_error_with_position(self, tmp_path):
        text = "def f(:\n    pass\n"
        found = diag.ruff_diagnostics(text, tmp_path / "bad.py")
        assert found, "expected at least one diagnostic"
        first = found[0]
        assert first.severity == "error"
        assert first.line == 1
        assert first.col >= 6

    @pytest.mark.skipif(not ruff_present, reason="ruff not installed")
    def test_unused_import_is_a_fixable_warning(self, tmp_path):
        found = diag.ruff_diagnostics("import os\n", tmp_path / "ok.py")
        codes = {d.code for d in found}
        assert "F401" in codes
        f401 = next(d for d in found if d.code == "F401")
        assert f401.severity == "warning"
        assert f401.fixable is True

    @pytest.mark.skipif(not ruff_present, reason="ruff not installed")
    def test_clean_file_has_no_diagnostics(self, tmp_path):
        assert diag.ruff_diagnostics("x = 1\n", tmp_path / "clean.py") == []

    @pytest.mark.skipif(not ruff_present, reason="ruff not installed")
    def test_autofix_removes_unused_import(self, tmp_path):
        fixed = diag.ruff_autofix("import os\nx = 1\n", tmp_path / "a.py")
        assert fixed == "x = 1\n"

    @pytest.mark.skipif(not ruff_present, reason="ruff not installed")
    def test_autofix_returns_none_when_nothing_to_fix(self, tmp_path):
        assert diag.ruff_autofix("x = 1\n", tmp_path / "a.py") is None

    def test_missing_ruff_degrades_to_no_diagnostics(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diag, "find_ruff", lambda: None)
        assert diag.ruff_diagnostics("def f(:\n", tmp_path / "x.py") == []
        assert diag.supported(tmp_path / "x.py") is False


class TestSeverity:
    def test_ruffs_blanket_error_label_is_ignored(self):
        assert diag._severity_for("F401", "error") == "warning"
        assert diag._severity_for("invalid-syntax", "error") == "error"

    def test_fallback_by_code(self):
        assert diag._severity_for("invalid-syntax", None) == "error"
        assert diag._severity_for("F821", None) == "error"
        assert diag._severity_for("F401", None) == "warning"
        assert diag._severity_for("E501", None) == "warning"


class TestJsonYaml:
    def test_json_error_has_line_and_column(self):
        found = diag.json_diagnostics('{\n  "a": 1,\n  "b": \n}')
        assert len(found) == 1 and found[0].severity == "error"
        assert found[0].line == 4
        assert found[0].code == "json"

    def test_valid_or_empty_json_is_clean(self):
        assert diag.json_diagnostics('{"a": 1}') == []
        assert diag.json_diagnostics("   ") == []

    def test_yaml_error_has_line(self):
        found = diag.yaml_diagnostics("a: 1\nb: [1, 2\nc: 3\n")
        assert len(found) == 1 and found[0].severity == "error"
        assert found[0].line >= 2

    def test_valid_yaml_is_clean(self):
        assert diag.yaml_diagnostics("a: 1\nb:\n  - x\n") == []


class TestCollect:
    def test_dispatches_by_suffix(self, tmp_path):
        assert diag.collect(tmp_path / "x.json", "{bad") and diag.collect(
            tmp_path / "x.yaml", "a: [1"
        )
        assert diag.collect(tmp_path / "notes.txt", "anything") == []
        assert diag.collect(None, "x") == []

    def test_supported(self, tmp_path):
        assert diag.supported(tmp_path / "a.json") is True
        assert diag.supported(tmp_path / "a.yml") is True
        assert diag.supported(tmp_path / "a.md") is False
        assert diag.supported(None) is False
