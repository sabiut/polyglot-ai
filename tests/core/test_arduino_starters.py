"""Tests for the starter project catalog and copier.

Pin:

- The bundled starters all parse and resolve their entry files.
- ``starters_for(board, language)`` filters correctly so the panel's
  picker never offers a starter the chosen board can't run.
- ``copy_starter`` honours the C++ "folder must match .ino name"
  convention and the Python flat-file convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from polyglot_ai.core.arduino import board_for_fqbn  # noqa: E402
from polyglot_ai.core.arduino.boards import Language  # noqa: E402
from polyglot_ai.core.arduino.starters import (  # noqa: E402
    Starter,
    copy_starter,
    list_starters,
    starter_destination,
    starters_for,
)


class TestBundledStarters:
    def test_at_least_one_per_language(self):
        starters = list_starters()
        langs = {s.language for s in starters}
        assert Language.CPP in langs
        assert Language.MICROPYTHON in langs
        assert Language.CIRCUITPYTHON in langs

    def test_every_starter_resolves_entry_file(self):
        # The loader already drops malformed starters; this test
        # just confirms that none of the bundled ones are silently
        # skipped (which would mean the picker shows fewer tiles
        # than expected).
        slugs_on_disk = {
            p.name
            for p in (
                Path(__file__).resolve().parent.parent.parent
                / "src/polyglot_ai/core/arduino/starters"
            ).iterdir()
            if p.is_dir()
        }
        loaded_slugs = {s.slug for s in list_starters()}
        assert slugs_on_disk == loaded_slugs, (
            f"Starters dropped by loader: {slugs_on_disk - loaded_slugs}"
        )

    def test_blink_present_in_every_language(self):
        slugs = {s.slug for s in list_starters()}
        assert "blink-cpp" in slugs
        assert "blink-micropython" in slugs
        assert "blink-circuitpython" in slugs


class TestStartersFor:
    def test_uno_with_cpp_includes_blink(self):
        uno = board_for_fqbn("arduino:avr:uno")
        assert uno is not None
        slugs = [s.slug for s in starters_for(uno, Language.CPP)]
        assert "blink-cpp" in slugs

    def test_uno_with_micropython_yields_nothing(self):
        # Uno is C++ only — every micropython starter must be filtered out.
        uno = board_for_fqbn("arduino:avr:uno")
        assert uno is not None
        assert starters_for(uno, Language.MICROPYTHON) == []

    def test_pico_with_circuitpython_picks_starters(self):
        pico = board_for_fqbn("rp2040:rp2040:rpipico")
        assert pico is not None
        slugs = [s.slug for s in starters_for(pico, Language.CIRCUITPYTHON)]
        assert "blink-circuitpython" in slugs

    def test_starter_with_explicit_board_list_filters(self):
        # The rainbow starter is restricted to CPX / Feather M4.
        # On a Pico (CircuitPython too) the rainbow starter must be
        # excluded — its NeoPixel hardware isn't built in.
        pico = board_for_fqbn("rp2040:rp2040:rpipico")
        assert pico is not None
        slugs = [s.slug for s in starters_for(pico, Language.CIRCUITPYTHON)]
        assert "rgb-rainbow-circuitpython" not in slugs


class TestCopyStarter:
    def _starter(self, slug: str) -> Starter:
        for s in list_starters():
            if s.slug == slug:
                return s
        pytest.fail(f"starter {slug!r} not bundled")

    def test_cpp_starter_creates_named_folder(self, tmp_path: Path):
        s = self._starter("blink-cpp")
        entry = copy_starter(s, tmp_path)
        # Sketch folder must be named after the project, with the
        # .ino sharing that name — arduino-cli is strict about this.
        assert entry.parent.name == "blink"
        assert entry.name == "blink.ino"
        assert entry.is_file()

    def test_python_starter_lands_in_named_subfolder(self, tmp_path: Path):
        # MicroPython starters now create a sub-folder just like
        # C++ starters — picking ``~/Desktop`` to save into shouldn't
        # dump ``main.py`` directly onto the desktop alongside other
        # projects.
        s = self._starter("blink-micropython")
        entry = copy_starter(s, tmp_path)
        assert entry == tmp_path / "blink" / "main.py"
        assert entry.is_file()
        assert not (tmp_path / "blink" / "meta.yml").exists()

    def test_circuitpython_starter_lands_in_named_subfolder(self, tmp_path: Path):
        s = self._starter("blink-circuitpython")
        entry = copy_starter(s, tmp_path)
        assert entry == tmp_path / "blink" / "code.py"
        assert "while True" in entry.read_text()


class TestStarterDestination:
    """``starter_destination`` must predict ``copy_starter``'s output.

    The Change-project dialog shows the destination preview BEFORE
    calling copy_starter. If the two functions ever drift, the
    preview lies — these tests pin them together.
    """

    def _starter(self, slug: str) -> Starter:
        for s in list_starters():
            if s.slug == slug:
                return s
        pytest.fail(f"starter {slug!r} not bundled")

    def test_predicts_cpp_path(self, tmp_path: Path):
        s = self._starter("blink-cpp")
        predicted = starter_destination(s, tmp_path)
        actual = copy_starter(s, tmp_path)
        assert predicted == actual

    def test_predicts_micropython_path(self, tmp_path: Path):
        s = self._starter("blink-micropython")
        predicted = starter_destination(s, tmp_path)
        actual = copy_starter(s, tmp_path)
        assert predicted == actual

    def test_predicts_circuitpython_path(self, tmp_path: Path):
        s = self._starter("blink-circuitpython")
        predicted = starter_destination(s, tmp_path)
        actual = copy_starter(s, tmp_path)
        assert predicted == actual

    def test_does_not_touch_filesystem(self, tmp_path: Path):
        # Pure path arithmetic — calling it must not create any files.
        s = self._starter("blink-cpp")
        starter_destination(s, tmp_path / "imaginary")
        assert not (tmp_path / "imaginary").exists()
