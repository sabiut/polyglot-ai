"""AI tool implementations for the Arduino panel.

These let the chat model do more than *talk* about Arduino projects:
read state, list connected boards, compile and upload.

Design rules
------------

1. **Read tools auto-approve.** ``arduino_get_state`` and
   ``arduino_list_boards`` only read in-process snapshots / system
   state; no filesystem writes, no subprocess. Safe to chain.

2. **Mutation tools require approval.** ``arduino_compile`` and
   ``arduino_upload`` shell out to ``arduino-cli`` / ``mpremote``.
   Even compile is gated because a malicious or mistaken prompt
   could compile against an unexpected FQBN; the user should see
   what's being run.

3. **Source of truth is ``panel_state``.** The compile/upload tools
   pull the current project / board / port from the published
   panel snapshot rather than taking them as arguments. That way
   the AI can't drive the tools toward a stale or fabricated path.
   When state is absent, the tool returns a clear error instead of
   guessing.

4. **No Qt imports.** Lives in ``core/ai/tools/`` like the other
   tool modules — pure async functions over the existing service
   and panel-state machinery.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from polyglot_ai.core import panel_state
from polyglot_ai.core.arduino import board_for_fqbn
from polyglot_ai.core.arduino.boards import Language
from polyglot_ai.core.arduino.project import (
    DetectedProject,
    create_blank,
    language_for_file,
)
from polyglot_ai.core.arduino.service import ArduinoService
from polyglot_ai.core.arduino.starters import copy_starter, list_starters

logger = logging.getLogger(__name__)


# ── Read-only tools ────────────────────────────────────────────────


async def arduino_get_state(args: dict) -> str:
    """Return the current Arduino panel state as JSON.

    Identical data to what the system prompt's panel block carries,
    plus the full board list. The tool is useful when the AI wants
    a fresh re-read mid-conversation (e.g. after asking the user
    to plug in a board).
    """
    snapshot = panel_state.get_last_arduino_state()
    if snapshot is None:
        return json.dumps(
            {
                "loaded": False,
                "note": "Arduino panel hasn't published a state yet — "
                "open it from the activity bar to wake it up.",
            }
        )
    # Add the live connected-boards list — the snapshot only has the
    # *preferred* board. The AI may want to enumerate all candidates
    # ("you also have an ESP32 plugged in").
    try:
        boards = await ArduinoService().list_connected_boards()
        snapshot["connected_boards"] = [
            {
                "port": b.port,
                "display_name": (b.board.display_name if b.board else "Unknown"),
                "fqbn": b.board.fqbn if b.board else None,
                "vid": f"0x{b.vid:04x}" if b.vid else None,
                "pid": f"0x{b.pid:04x}" if b.pid else None,
            }
            for b in boards
        ]
    except Exception:  # pragma: no cover — defensive
        logger.exception("arduino_get_state: list_connected_boards failed")
        snapshot["connected_boards"] = []
    return json.dumps(snapshot, indent=2)


async def arduino_list_boards(args: dict) -> str:
    """Enumerate every board currently connected over USB."""
    try:
        boards = await ArduinoService().list_connected_boards()
    except Exception as exc:
        return f"Error scanning boards: {exc}"
    if not boards:
        return (
            "No boards are connected. Plug in your Arduino with the USB "
            "cable, then call this again."
        )
    lines = [f"Found {len(boards)} board(s):"]
    for b in boards:
        name = b.board.display_name if b.board else "Unknown board"
        fqbn = f" — {b.board.fqbn}" if b.board else ""
        lines.append(f"  • {name}{fqbn}  on {b.port}")
    return "\n".join(lines)


# ── Mutation tools (gated by approval) ─────────────────────────────


async def arduino_compile(args: dict) -> str:
    """Compile the project currently loaded in the Arduino panel.

    Reads the project / board from ``panel_state`` rather than
    taking them as arguments — see module docstring for why.
    """
    project, board = _resolve_project_and_board(args, require_board=False)
    if isinstance(project, str):  # error message
        return project
    if board is None:
        return (
            "No board is selected in the Arduino panel and no fqbn "
            'argument was provided. Plug in a board or pass {"fqbn": '
            '"arduino:avr:uno"} (or similar).'
        )
    if project.language is not Language.CPP:
        return (
            "arduino_compile is only meaningful for C++ projects. "
            f"The current project is {project.language.value}; "
            "use arduino_upload instead — Python files copy directly."
        )

    service = ArduinoService()
    messages = [u.message async for u in service.compile_cpp(project.project_dir, board)]
    if service.last_error_detail:
        # Trim noisy stderr: the model gets the *first* problem, not
        # the entire arduino-cli diagnostic dump. The user can re-run
        # and read the panel for the rest.
        detail = service.last_error_detail.strip()
        if len(detail) > 4000:
            detail = detail[:4000] + "\n... (truncated)"
        return (
            "Compilation failed.\n"
            + "\n".join(f"- {m}" for m in messages)
            + "\n\nDetails:\n"
            + detail
        )
    return "Compilation succeeded.\n" + "\n".join(f"- {m}" for m in messages)


async def arduino_upload(args: dict) -> str:
    """Upload the loaded project to the connected board."""
    project, board = _resolve_project_and_board(args, require_board=True)
    if isinstance(project, str):
        return project
    if isinstance(board, str):  # error
        return board
    assert board is not None  # for type-checkers; runtime narrowed above

    snapshot = panel_state.get_last_arduino_state() or {}
    snapshot_board = snapshot.get("board") or {}
    port = args.get("port") or snapshot_board.get("port")

    service = ArduinoService()

    if project.language is Language.CPP:
        if not port:
            return (
                "No serial port available. Plug in a board so the "
                'panel can detect it, or pass {"port": '
                '"/dev/ttyUSB0"} (or COM3 on Windows).'
            )
        # Compile first — uploading without compile leaves stale
        # binaries on the board after a code edit, which is the
        # most confusing failure mode for newcomers.
        async for _ in service.compile_cpp(project.project_dir, board):
            pass
        if service.last_error_detail:
            detail = service.last_error_detail.strip()
            return f"Compilation failed before upload:\n{detail[:4000]}"

        msgs = [u.message async for u in service.upload_cpp(project.project_dir, board, port)]
        if service.last_error_detail:
            return (
                "Upload failed.\n"
                + "\n".join(f"- {m}" for m in msgs)
                + "\n\nDetails:\n"
                + service.last_error_detail.strip()[:4000]
            )
        return "Upload succeeded.\n" + "\n".join(f"- {m}" for m in msgs)

    if project.language is Language.MICROPYTHON:
        if not port:
            return 'No serial port available. Plug in a board or pass {"port": "/dev/ttyACM0"}.'
        msgs = [u.message async for u in service.upload_micropython(project.entry_file, port)]
        if service.last_error_detail:
            return (
                "MicroPython upload failed.\n"
                + "\n".join(f"- {m}" for m in msgs)
                + "\n\nDetails:\n"
                + service.last_error_detail.strip()[:4000]
            )
        return "Upload succeeded.\n" + "\n".join(f"- {m}" for m in msgs)

    # CircuitPython
    drive_arg = args.get("drive")
    if not drive_arg:
        return (
            "CircuitPython needs the path to the CIRCUITPY USB drive. "
            "Pick it in the panel (Advanced → CIRCUITPY drive) or pass "
            '{"drive": "/run/media/USER/CIRCUITPY"}.'
        )
    drive = Path(drive_arg)
    msgs = [u.message async for u in service.upload_circuitpython(project.entry_file, drive)]
    if service.last_error_detail:
        return (
            "CircuitPython copy failed.\n"
            + "\n".join(f"- {m}" for m in msgs)
            + "\n\nDetails:\n"
            + service.last_error_detail.strip()[:4000]
        )
    return "Upload succeeded.\n" + "\n".join(f"- {m}" for m in msgs)


# ── Scaffold tools ─────────────────────────────────────────────────


async def arduino_list_starters(args: dict) -> str:
    """Return the bundled starter catalogue as JSON.

    Read-only. Lets the AI recommend a starter ("you'd want
    blink-cpp for a first project") before invoking
    ``arduino_load_starter`` for real.
    """
    starters = list_starters()
    out = [
        {
            "slug": s.slug,
            "name": s.name,
            "blurb": s.blurb,
            "language": s.language.value,
            "boards": list(s.boards),
        }
        for s in starters
    ]
    return json.dumps(out, indent=2)


async def arduino_load_starter(args: dict) -> str:
    """Copy a bundled starter into a target folder and load it.

    Args:
        slug: required — the starter slug, e.g. "blink-cpp"
        target_dir: optional — the parent folder. Defaults to the
                    project root from the current panel state, or
                    the user's home directory if neither is set.

    Side effects: writes files inside ``target_dir/<starter>/``.
    Updates the panel state so the next message's system prompt
    reflects the new project. Requires user approval.
    """
    slug = (args.get("slug") or "").strip()
    if not slug:
        return (
            "arduino_load_starter needs a ``slug`` argument. Call "
            "arduino_list_starters first to see the available slugs."
        )
    starter = next((s for s in list_starters() if s.slug == slug), None)
    if starter is None:
        valid = ", ".join(s.slug for s in list_starters())
        return f"Unknown starter '{slug}'. Available: {valid}"

    target_dir = _resolve_target_dir(args.get("target_dir"))

    try:
        entry = copy_starter(starter, target_dir)
    except OSError as exc:
        return f"Couldn't copy starter files: {exc}"

    language = language_for_file(entry) or starter.language
    project = DetectedProject(entry, entry.parent, language)
    _publish_project(project)
    return (
        f"Loaded starter '{starter.name}' ({starter.language.value}) "
        f"at {entry}. The Arduino panel now points at this project; "
        "ask the user to plug in their board, then call arduino_upload."
    )


async def arduino_create_blank(args: dict) -> str:
    """Scaffold a blank project and load it.

    Args:
        name: required — project name (used as folder + .ino filename)
        language: required — "cpp", "micropython", or "circuitpython"
        target_dir: optional — parent folder; defaults like
                    arduino_load_starter

    Side effects: creates ``target_dir/<safe-name>/<entry-file>`` with
    minimal boilerplate. Refuses to overwrite an existing entry file.
    Requires user approval.
    """
    name = (args.get("name") or "").strip()
    language_raw = (args.get("language") or "").strip().lower()
    if not name:
        return "arduino_create_blank needs a ``name`` argument."
    try:
        language = Language(language_raw)
    except ValueError:
        return "arduino_create_blank ``language`` must be one of: cpp, micropython, circuitpython."

    target_dir = _resolve_target_dir(args.get("target_dir"))

    try:
        project = create_blank(target_dir, name, language)
    except FileExistsError as exc:
        return f"A file already exists at {exc}. Pick a different name or target_dir."
    except (OSError, ValueError) as exc:
        return f"Couldn't create blank project: {exc}"

    _publish_project(project)
    return (
        f"Created blank {language.value} project at "
        f"{project.entry_file}. The Arduino panel now points at this "
        "project. The file just has empty boilerplate — tell the user "
        "to write their code (in the editor) before uploading."
    )


# ── Helpers ────────────────────────────────────────────────────────


def _resolve_target_dir(arg_value) -> Path:
    """Pick the parent folder for a new project.

    Order of preference: explicit argument → panel-state project_dir
    parent → user home. Stays inside :class:`Path` so callers can
    immediately ``mkdir`` against the result.
    """
    if arg_value:
        return Path(arg_value).expanduser().resolve()
    snapshot = panel_state.get_last_arduino_state() or {}
    if snapshot.get("project_dir"):
        # Drop the project's own subdirectory so a sibling project
        # lands next to it, not nested inside.
        return Path(snapshot["project_dir"]).parent
    return Path.home()


def _publish_project(project: DetectedProject) -> None:
    """Update panel_state in-place after a tool-driven project change.

    Mirrors what ``ArduinoPanel._publish_panel_state`` would do, but
    has to work without the panel itself — the AI may run these
    tools when the panel hasn't initialised yet (e.g. headless
    standalone mode). Keeps just enough fields populated that the
    next system-prompt rebuild has accurate context.
    """
    code = ""
    try:
        code = project.entry_file.read_text(encoding="utf-8", errors="replace")
        if len(code) > 3000:
            code = code[:3000] + "\n... (truncated)"
    except OSError:
        pass

    snapshot = panel_state.get_last_arduino_state() or {
        "loaded": False,
        "toolchains": {},
        "board": None,
    }
    snapshot.update(
        {
            "loaded": True,
            "entry_file": str(project.entry_file),
            "project_dir": str(project.project_dir),
            "language": project.language.value,
            "language_display": _LANG_DISPLAY[project.language],
            # The board / readiness fields will be updated next time
            # the panel polls; until then the AI only knows that a
            # project is loaded, which is the truth.
            "ready_to_upload": False,
            "blocker": "Plug in your board first.",
            "code": code,
        }
    )
    panel_state.set_last_arduino_state(snapshot)


_LANG_DISPLAY: dict[Language, str] = {
    Language.CPP: "C++",
    Language.MICROPYTHON: "Python (MicroPython)",
    Language.CIRCUITPYTHON: "Python (CircuitPython)",
}


def _resolve_project_and_board(args: dict, *, require_board: bool):
    """Read project + board from panel state with arg overrides.

    Returns either:
    - ``(DetectedProject, Board | None)`` on success, or
    - ``(error_message, None)`` if the project isn't loaded.

    When ``require_board`` is True and no board can be resolved
    from snapshot or args, the second element is the error string
    instead of a Board.
    """
    from polyglot_ai.core.arduino.project import (
        DetectedProject,
        language_for_file,
    )

    snapshot = panel_state.get_last_arduino_state()
    if not snapshot or not snapshot.get("loaded"):
        return (
            "No project is loaded in the Arduino panel. Ask the user to "
            "load one (open the panel and pick a starter, start blank, "
            "or open an existing folder).",
            None,
        )
    entry_file = Path(snapshot["entry_file"])
    project_dir = Path(snapshot["project_dir"])
    language = language_for_file(entry_file) or Language(snapshot["language"])
    project = DetectedProject(entry_file, project_dir, language)

    fqbn = args.get("fqbn") or (snapshot.get("board") or {}).get("fqbn")
    board = board_for_fqbn(fqbn) if fqbn else None
    if require_board and board is None:
        return (
            project,
            "No board is connected and no fqbn was provided. Either "
            'plug in a board or pass {"fqbn": "arduino:avr:uno"} '
            "(or similar) so the toolchain knows the target.",
        )
    return project, board
