"""Arduino-project shape detection and blank-project creation.

The panel needs to answer two questions cheaply:

1. *Given a folder, does it look like an Arduino / MCU project, and
   if so what's the entry file and language?* — so opening a project
   in the IDE auto-loads it into the Arduino panel.
2. *Where do new "Start blank" projects live, and what's in them?* —
   so a kid (or anyone) can bootstrap a blank sketch from the panel
   without copy-pasting boilerplate.

The detection rules are deliberately conservative. We'd rather fail
to recognise an unusual project layout (and let the user point at
it manually) than mis-identify some random folder of Python files
as a CircuitPython project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polyglot_ai.core.arduino.boards import Language


@dataclass(frozen=True)
class DetectedProject:
    """A loaded Arduino-shaped project on disk.

    ``entry_file`` is the path the panel uses for compile/upload.
    ``project_dir`` is the folder it lives in — for C++ the folder
    that contains the .ino, for Python the folder containing
    main.py / code.py.
    """

    entry_file: Path
    project_dir: Path
    language: Language


def language_for_file(path: Path) -> Language | None:
    """Map an entry filename to its language.

    Only checks the *name* (not the contents) so it stays cheap.
    """
    name = path.name.lower()
    if name.endswith(".ino"):
        return Language.CPP
    if name == "code.py":
        return Language.CIRCUITPYTHON
    if name == "main.py":
        return Language.MICROPYTHON
    return None


def detect_in(folder: Path) -> DetectedProject | None:
    """Walk ``folder`` looking for an entry file we recognise.

    Search order:

    1. Direct children — handles the common "user opened the sketch
       folder itself" case.
    2. One level deep — handles "user opened the parent folder, and
       the sketch is in <parent>/blink/blink.ino".

    Stops at the first match in the first applicable bucket; deeper
    nesting is intentionally ignored to avoid sucking in random
    Python files from a sub-project.
    """
    if not folder.is_dir():
        return None

    # Direct-children pass. ``.ino`` wins over python files because
    # an Arduino sketch folder usually has both Arduino and helper
    # ``.py`` files (e.g. build scripts) and the .ino is the actual
    # project entry.
    direct_ino = next(folder.glob("*.ino"), None)
    if direct_ino is not None:
        return DetectedProject(direct_ino, folder, Language.CPP)
    if (folder / "code.py").is_file():
        return DetectedProject(folder / "code.py", folder, Language.CIRCUITPYTHON)
    if (folder / "main.py").is_file():
        return DetectedProject(folder / "main.py", folder, Language.MICROPYTHON)

    # One level deep. We sort the children for deterministic
    # behaviour when several sketch folders coexist — picking the
    # first alphabetical match is more predictable than relying on
    # filesystem order.
    for child in sorted(folder.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        ino = next(child.glob("*.ino"), None)
        if ino is not None:
            return DetectedProject(ino, child, Language.CPP)
        if (child / "code.py").is_file():
            return DetectedProject(child / "code.py", child, Language.CIRCUITPYTHON)
        if (child / "main.py").is_file():
            return DetectedProject(child / "main.py", child, Language.MICROPYTHON)

    return None


def create_blank(parent: Path, name: str, language: Language) -> DetectedProject:
    """Create a minimal blank project under ``parent/name``.

    Returns the resulting :class:`DetectedProject` so the caller can
    drop it straight into the panel's loaded state.

    Raises
    ------
    FileExistsError
        If the target entry file already exists. The caller should
        prompt the user before overwriting.
    """
    if not name.strip():
        raise ValueError("Project name cannot be empty.")
    safe = safe_folder_name(name)

    project_dir = parent / safe
    project_dir.mkdir(parents=True, exist_ok=True)

    if language is Language.CPP:
        entry = project_dir / f"{safe}.ino"
        scaffold = _CPP_BLANK
    elif language is Language.CIRCUITPYTHON:
        entry = project_dir / "code.py"
        scaffold = _CIRCUITPYTHON_BLANK
    else:  # MicroPython
        entry = project_dir / "main.py"
        scaffold = _MICROPYTHON_BLANK

    if entry.exists():
        raise FileExistsError(entry)

    entry.write_text(scaffold, encoding="utf-8")
    return DetectedProject(entry, project_dir, language)


def safe_folder_name(name: str) -> str:
    """Sanitise a free-text project name into a folder-safe slug.

    Arduino-cli is strict about sketch directory names: they must
    match the .ino filename, and certain characters (spaces, dots,
    most punctuation) cause it to refuse compilation. Be tighter
    than strictly necessary for cross-platform safety.

    Public so the Change-project dialog can preview the resulting
    path before calling :func:`create_blank` — keeping the rule in
    one place avoids the preview and the actual create call drifting
    apart.
    """
    cleaned: list[str] = []
    for ch in name.strip():
        if ch.isalnum() or ch in ("_", "-"):
            cleaned.append(ch)
        elif ch in (" ", "\t"):
            cleaned.append("_")
        # everything else dropped
    out = "".join(cleaned).strip("_-") or "sketch"
    # Arduino-cli also rejects leading digits in sketch names.
    if out[0].isdigit():
        out = "sketch_" + out
    return out


# ── Scaffolds ──────────────────────────────────────────────────────


_CPP_BLANK = """// New Arduino sketch.
//
// ``setup()`` runs once when the board powers on. Use it to set
// pin modes, start serial communication, etc.
//
// ``loop()`` runs over and over forever. Put the work here.

void setup() {
  // Runs once.
}

void loop() {
  // Runs over and over.
}
"""


_MICROPYTHON_BLANK = """# New MicroPython project.
#
# This file (``main.py``) runs automatically when the board boots.
# Anything you ``print()`` shows up in the REPL.

print("Hello from MicroPython!")
"""


_CIRCUITPYTHON_BLANK = """# New CircuitPython project.
#
# This file (``code.py``) runs automatically every time the board
# reboots — and CircuitPython reboots whenever you save changes,
# so editing the file is enough to restart your program.

print("Hello from CircuitPython!")
"""
