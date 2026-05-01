"""Tests for Arduino project detection + blank-project scaffolding.

Pin the contracts the Arduino panel depends on:

- A folder containing a ``.ino`` is recognised as a C++ sketch, even
  when nested one level deep (``parent/blink/blink.ino``).
- ``code.py`` → CircuitPython, ``main.py`` → MicroPython.
- ``.ino`` wins over Python files in the same folder so an Arduino
  sketch with a build helper script doesn't get classified Python.
- :func:`create_blank` produces an arduino-cli-compatible folder
  layout (sketch directory shares the .ino name) and refuses to
  silently overwrite an existing entry file.
- Folder-name sanitisation strips characters arduino-cli rejects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polyglot_ai.core.arduino.boards import Language
from polyglot_ai.core.arduino.project import (
    DetectedProject,
    create_blank,
    detect_in,
    language_for_file,
)


class TestLanguageForFile:
    def test_ino_is_cpp(self):
        assert language_for_file(Path("sketch.ino")) is Language.CPP

    def test_code_py_is_circuitpython(self):
        assert language_for_file(Path("code.py")) is Language.CIRCUITPYTHON

    def test_main_py_is_micropython(self):
        assert language_for_file(Path("main.py")) is Language.MICROPYTHON

    def test_other_python_file_is_unknown(self):
        # ``utility.py`` shouldn't be mistaken for an entry point —
        # only the canonical names map to a language.
        assert language_for_file(Path("utility.py")) is None

    def test_extension_is_case_insensitive(self):
        assert language_for_file(Path("BLINK.INO")) is Language.CPP


class TestDetectIn:
    def test_direct_ino(self, tmp_path: Path):
        (tmp_path / "blink.ino").write_text("void setup(){} void loop(){}")
        det = detect_in(tmp_path)
        assert isinstance(det, DetectedProject)
        assert det.entry_file.name == "blink.ino"
        assert det.language is Language.CPP
        assert det.project_dir == tmp_path

    def test_nested_ino_one_level_deep(self, tmp_path: Path):
        sub = tmp_path / "blink"
        sub.mkdir()
        (sub / "blink.ino").write_text("void setup(){} void loop(){}")
        det = detect_in(tmp_path)
        assert det is not None
        assert det.project_dir == sub
        assert det.entry_file == sub / "blink.ino"

    def test_circuitpython_code_py(self, tmp_path: Path):
        (tmp_path / "code.py").write_text("print('hi')\n")
        det = detect_in(tmp_path)
        assert det is not None
        assert det.language is Language.CIRCUITPYTHON

    def test_micropython_main_py(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("print('hi')\n")
        det = detect_in(tmp_path)
        assert det is not None
        assert det.language is Language.MICROPYTHON

    def test_ino_wins_over_python_in_same_folder(self, tmp_path: Path):
        (tmp_path / "blink.ino").write_text("void setup(){}")
        (tmp_path / "main.py").write_text("print('helper')\n")
        det = detect_in(tmp_path)
        assert det is not None
        assert det.language is Language.CPP

    def test_empty_folder_returns_none(self, tmp_path: Path):
        assert detect_in(tmp_path) is None

    def test_folder_with_unrelated_files_returns_none(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# hello\n")
        (tmp_path / "util.py").write_text("# helper\n")
        assert detect_in(tmp_path) is None

    def test_missing_path_returns_none(self, tmp_path: Path):
        assert detect_in(tmp_path / "nope") is None

    def test_hidden_subfolders_are_skipped(self, tmp_path: Path):
        # ``.git`` etc. shouldn't be searched — otherwise a stray
        # file in a hidden cache could pollute detection.
        hidden = tmp_path / ".cache"
        hidden.mkdir()
        (hidden / "code.py").write_text("# stray")
        assert detect_in(tmp_path) is None

    def test_alphabetical_pick_when_two_subfolders(self, tmp_path: Path):
        a = tmp_path / "alpha"
        b = tmp_path / "beta"
        a.mkdir()
        b.mkdir()
        (a / "alpha.ino").write_text("void setup(){}")
        (b / "beta.ino").write_text("void setup(){}")
        det = detect_in(tmp_path)
        assert det is not None
        assert det.project_dir.name == "alpha"


class TestCreateBlank:
    def test_cpp_creates_named_folder(self, tmp_path: Path):
        det = create_blank(tmp_path, "myblink", Language.CPP)
        assert det.project_dir == tmp_path / "myblink"
        assert det.entry_file == tmp_path / "myblink" / "myblink.ino"
        assert det.language is Language.CPP
        assert "void setup()" in det.entry_file.read_text()

    def test_micropython_creates_main_py(self, tmp_path: Path):
        det = create_blank(tmp_path, "demo", Language.MICROPYTHON)
        assert det.entry_file == tmp_path / "demo" / "main.py"
        assert "print(" in det.entry_file.read_text()

    def test_circuitpython_creates_code_py(self, tmp_path: Path):
        det = create_blank(tmp_path, "demo", Language.CIRCUITPYTHON)
        assert det.entry_file == tmp_path / "demo" / "code.py"
        assert "print(" in det.entry_file.read_text()

    def test_refuses_to_overwrite_existing_entry(self, tmp_path: Path):
        # First creation succeeds; second raises so the panel can
        # warn the user instead of clobbering work.
        create_blank(tmp_path, "demo", Language.CPP)
        with pytest.raises(FileExistsError):
            create_blank(tmp_path, "demo", Language.CPP)

    def test_empty_name_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            create_blank(tmp_path, "   ", Language.CPP)

    def test_sanitises_unsafe_characters(self, tmp_path: Path):
        # Spaces become underscores, punctuation is dropped, and
        # arduino-cli-incompatible names are normalised.
        det = create_blank(tmp_path, "My Cool Sketch!", Language.CPP)
        assert det.project_dir.name == "My_Cool_Sketch"
        assert det.entry_file.name == "My_Cool_Sketch.ino"

    def test_leading_digit_gets_prefixed(self, tmp_path: Path):
        # arduino-cli rejects sketch names starting with a digit.
        det = create_blank(tmp_path, "1blink", Language.CPP)
        assert det.project_dir.name.startswith("sketch_")
