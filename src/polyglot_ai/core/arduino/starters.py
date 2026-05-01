"""Starter project catalog.

A starter is a folder under ``starters/<slug>/`` containing:

- ``meta.yml`` — name, blurb, emoji, language, supported boards, the
  filename of the entry point, suggested project name.
- The entry-point file itself (``<slug>.ino`` for C++, ``main.py`` for
  MicroPython, ``code.py`` for CircuitPython).
- Any extra files the sketch needs (sub-modules, config, etc.).

The panel calls :func:`list_starters` to populate its tile grid and
:func:`copy_starter` to drop a chosen starter into the user's project
directory. The loader is bundled-only — there's no project-local
override path because starters are meant to be the same everywhere
the app runs.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from polyglot_ai.core.arduino.boards import Board, Language

logger = logging.getLogger(__name__)

_STARTERS_DIR = Path(__file__).parent / "starters"


@dataclass(frozen=True)
class Starter:
    slug: str
    name: str
    blurb: str
    emoji: str
    language: Language
    # Slugs of boards this starter is known to work on. Empty tuple
    # means "any board that supports the language".
    boards: tuple[str, ...]
    entry_file: str
    suggested_project_name: str
    source_dir: Path

    def supports_board(self, board: Board) -> bool:
        if not board.supports(self.language):
            return False
        if not self.boards:
            return True
        return board.slug in self.boards


def list_starters() -> list[Starter]:
    """Discover every starter shipped in ``starters/``.

    Skips folders that fail to parse so a busted ``meta.yml`` can't
    take down the whole picker. The bad entry is logged.
    """
    if not _STARTERS_DIR.is_dir():
        return []

    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — starters disabled")
        return []

    out: list[Starter] = []
    for folder in sorted(_STARTERS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.yml"
        if not meta_path.is_file():
            continue
        try:
            data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            logger.warning("Starter %s: bad meta.yml — %s", folder.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("Starter %s: meta.yml must be a mapping", folder.name)
            continue

        try:
            language = Language(data["language"])
        except (KeyError, ValueError):
            logger.warning("Starter %s: missing or bad ``language``", folder.name)
            continue

        entry_file = str(data.get("entry_file") or _default_entry_for(language, folder.name))
        if not (folder / entry_file).is_file():
            logger.warning("Starter %s: entry file %s not found", folder.name, entry_file)
            continue

        out.append(
            Starter(
                slug=folder.name,
                name=str(data.get("name") or folder.name),
                blurb=str(data.get("blurb") or ""),
                emoji=str(data.get("emoji") or "✨"),
                language=language,
                boards=tuple(str(b) for b in (data.get("boards") or [])),
                entry_file=entry_file,
                suggested_project_name=str(data.get("suggested_project_name") or folder.name),
                source_dir=folder,
            )
        )
    return out


def starters_for(board: Board, language: Language) -> list[Starter]:
    """Subset of :func:`list_starters` valid for a board+language pick."""
    return [s for s in list_starters() if s.language is language and s.supports_board(board)]


def copy_starter(starter: Starter, target_dir: Path) -> Path:
    """Copy a starter's files into ``target_dir/<suggested_project_name>/``.

    Returns the path to the entry file in the target directory so the
    panel can immediately offer "Compile + Upload" against it.

    Every starter — C++, MicroPython, or CircuitPython — gets its
    own named sub-folder. The earlier flat-copy path for Python
    starters surprised users who picked a folder of projects to
    save into and then had ``main.py`` dropped directly alongside
    other projects. C++ starters additionally rename the entry file
    to match the folder name so ``arduino-cli`` accepts the
    directory as a sketch.

    Use :func:`starter_destination` to preview where the entry file
    will land *before* calling this — that's what the dialog's
    "Will be saved as:" line uses.
    """
    if not starter.source_dir.is_dir():
        raise FileNotFoundError(f"Starter source missing: {starter.source_dir}")

    project_dir = target_dir / starter.suggested_project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    for src in starter.source_dir.iterdir():
        if src.name == "meta.yml":
            continue
        if starter.language is Language.CPP and src.name == starter.entry_file:
            dst = project_dir / f"{starter.suggested_project_name}.ino"
        else:
            dst = project_dir / src.name
        shutil.copy2(src, dst)

    if starter.language is Language.CPP:
        return project_dir / f"{starter.suggested_project_name}.ino"
    return project_dir / starter.entry_file


def starter_destination(starter: Starter, target_dir: Path) -> Path:
    """Predict the entry-file path that :func:`copy_starter` will produce.

    Pure path arithmetic — no filesystem access. The Change-project
    dialog uses this to show the user where the project will land
    before they commit. Keeping the rule in one place avoids the
    preview and the actual copy drifting apart.
    """
    project_dir = target_dir / starter.suggested_project_name
    if starter.language is Language.CPP:
        return project_dir / f"{starter.suggested_project_name}.ino"
    return project_dir / starter.entry_file


def _default_entry_for(language: Language, slug: str) -> str:
    if language is Language.CPP:
        return f"{slug}.ino"
    if language is Language.CIRCUITPYTHON:
        return "code.py"
    return "main.py"
