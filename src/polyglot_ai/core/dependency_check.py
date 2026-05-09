"""Detect optional system dependencies (Node.js, uv, docker, kubectl, gh, git).

Used on first launch to warn users about missing runtimes and offer
guided install commands for their distro. Nothing here runs arbitrary
commands except the opt-in uv installer.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Distro = Literal["debian", "fedora", "arch", "opensuse", "alpine", "unknown"]


# ── Executable discovery ────────────────────────────────────────────
#
# ``shutil.which`` only searches ``$PATH``. That misses two very common
# install locations on Linux:
#
#   • ``~/.local/bin`` — pip's ``--user`` install destination and the
#     default ``BINDIR`` for the upstream ``arduino-cli`` installer
#     when ``BINDIR=/usr/local/bin`` isn't set explicitly. Debian /
#     Ubuntu only add this to ``$PATH`` from ``~/.profile`` *if it
#     exists at login*; users who install a tool today and don't log
#     out / back in won't have it on PATH for the running session.
#
#   • ``/snap/bin`` — Ubuntu / Mint default for snap-installed
#     binaries (``kubectl``, ``gh``). Present on PATH for most users
#     but not all (Fedora Silverblue, NixOS, custom shells).
#
# The first-run dependency dialog used to pop up for every user with
# arduino-cli or mpremote in ``~/.local/bin`` because of this. We now
# search a small list of well-known userland locations *in addition*
# to PATH, and any tool that resolves anywhere on that list is
# considered installed.
_EXTRA_SEARCH_PATHS_CACHE: list[Path] | None = None


def _extra_search_paths() -> list[Path]:
    """Return common executable directories that may not be on PATH.

    Cached on first call — these directories don't appear or
    disappear during a session, and ``Path.is_dir()`` is cheap but
    not free.
    """
    global _EXTRA_SEARCH_PATHS_CACHE
    if _EXTRA_SEARCH_PATHS_CACHE is not None:
        return _EXTRA_SEARCH_PATHS_CACHE

    home = Path.home()
    candidates = [
        home / ".local" / "bin",  # pip --user, arduino-cli install.sh default
        home / "bin",  # legacy ~/bin
        home / ".cargo" / "bin",  # Rust / cargo-installed CLIs
        Path("/usr/local/bin"),  # arduino-cli with BINDIR=/usr/local/bin
        Path("/snap/bin"),  # snap (kubectl, gh on Ubuntu/Mint)
        Path("/var/lib/snapd/snap/bin"),  # snap (alternate mount)
        Path("/opt/homebrew/bin"),  # Homebrew on Apple Silicon (rare on Linux)
    ]
    # Deduplicate while preserving order, and drop ones that don't
    # exist so per-tool lookups can short-circuit cleanly.
    seen: set[str] = set()
    result: list[Path] = []
    for p in candidates:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        try:
            if p.is_dir():
                result.append(p)
        except OSError:
            # Permission errors on weird mounts shouldn't break
            # detection — just skip the path.
            continue
    _EXTRA_SEARCH_PATHS_CACHE = result
    return result


def find_executable(command: str) -> str | None:
    """Like ``shutil.which`` but also searches common userland bin dirs.

    Returns the absolute path to ``command`` if found anywhere on
    PATH or in :func:`_extra_search_paths`, else ``None``.

    Public so other modules (e.g. ``arduino/service.py``) can use the
    same resolver and avoid the "dialog says installed but the panel
    can't find it" inconsistency.
    """
    if not command:
        return None
    found = shutil.which(command)
    if found:
        return found
    for d in _extra_search_paths():
        candidate = d / command
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _module_importable(module: str) -> bool:
    """Return True if ``module`` is importable in the *current* interpreter.

    Used for pure-Python dependencies (``pyserial`` and friends)
    that ship as importable packages rather than CLIs. Checking the
    spec here — instead of subprocess-importing under ``python3`` —
    means we report what the running app can actually use, which
    avoids "system Python has it but our venv doesn't" false
    positives.
    """
    if not module:
        return False
    try:
        import importlib.util

        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _python_module_runs(module: str) -> bool:
    """Return True if ``python3 -m <module>`` exits cleanly.

    Used as a last-resort fallback for pip-installed tools whose
    console-script wrapper isn't reachable (e.g. ``pip install
    --user mpremote`` on a system whose ``$PATH`` lacks
    ``~/.local/bin``). The module is in ``sys.path`` either way, so
    ``python3 -m mpremote --help`` still works.

    Each call shells out and is bounded at 5 s — fine on the first-
    run dialog which only invokes this once per startup. ``--help``
    rather than ``--version`` because not every CLI implements
    ``--version`` but ``-m`` always responds to ``--help``.
    """
    for python in ("python3", "python"):
        py = shutil.which(python)
        if not py:
            continue
        try:
            result = subprocess.run(
                [py, "-m", module, "--help"],
                capture_output=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


@dataclass
class Dependency:
    """A single optional dependency that may or may not be installed.

    Two flavours are supported:

    * **Executables** — set ``command`` (and optionally ``aliases``).
      Detection runs through :func:`find_executable`, which checks
      both ``$PATH`` and a small list of well-known userland bin
      dirs.
    * **Python packages** — set ``importable_module`` (e.g.
      ``"serial"`` for pyserial). Detection runs through
      :func:`_module_importable` against the *current* interpreter,
      which is what the app actually uses at runtime.

    Both flavours can coexist on the same entry, and an additional
    ``python_module`` fallback is available for pip-installed CLIs
    whose console-script wrapper isn't reachable on ``$PATH``.
    """

    key: str  # stable id, e.g. "node"
    name: str  # user-facing label, e.g. "Node.js"
    purpose: str  # one-line description of what it unlocks
    install_urls: dict[Distro, str]  # distro → install command or URL
    # Empty when the dependency is a pure-Python package with no
    # CLI wrapper (e.g. pyserial). Use ``importable_module`` instead.
    command: str = ""
    # Optional: extra executable names to accept as "installed". Some
    # tools ship under more than one binary name across distros
    # (e.g. ``esptool`` vs ``esptool.py``); listing all of them here
    # avoids false-negative dialogs.
    aliases: tuple[str, ...] = field(default_factory=tuple)
    # Optional: a Python module name that, if importable, satisfies
    # this dependency. Used for pure-Python packages — pyserial sets
    # this to ``"serial"``.
    importable_module: str | None = None
    # Optional: if the tool can be invoked as ``python -m <name>``
    # (typical of pip-installed CLIs whose console script may not be
    # on PATH), set this and detection will try that as a fallback.
    python_module: str | None = None
    # Whether installing this dependency needs ``sudo`` / ``pkexec``.
    # System packages (apt/dnf/etc.) need root; pip-installed Python
    # packages (``pyserial``, ``mpremote``) and userland installers
    # (``uv``) do not. The installer batches root deps under a
    # single pkexec'd shell and runs userland deps as the calling
    # user, so a missing pip package won't drag a sudo prompt.
    requires_root: bool = True

    def is_installed(self) -> bool:
        # 1) Executable on PATH or in a known userland bin dir.
        if self.command:
            for name in (self.command, *self.aliases):
                if find_executable(name) is not None:
                    return True
        # 2) Pure-Python package importable in the running app.
        if self.importable_module and _module_importable(self.importable_module):
            return True
        # 3) ``python -m <name>`` fallback for pip-installed CLIs
        #    whose console-script wrapper isn't reachable. Slower
        #    (subprocess) so it runs last.
        if self.python_module and _python_module_runs(self.python_module):
            return True
        return False

    def install_hint(self, distro: Distro) -> str:
        return self.install_urls.get(distro) or self.install_urls.get("unknown", "")


def detect_distro() -> Distro:
    """Parse /etc/os-release to identify the running Linux distribution.

    Returns "unknown" if the file is missing or the ID is unrecognised.
    """
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return "unknown"
    try:
        content = os_release.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("Could not read /etc/os-release: %s", e)
        return "unknown"

    info: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        info[key.strip()] = value.strip().strip('"')

    id_ = info.get("ID", "").lower()
    id_like = info.get("ID_LIKE", "").lower()
    combined = f"{id_} {id_like}"

    if any(x in combined for x in ("debian", "ubuntu", "mint", "pop", "elementary")):
        return "debian"
    if any(x in combined for x in ("fedora", "rhel", "centos", "rocky", "alma")):
        return "fedora"
    if any(x in combined for x in ("arch", "manjaro", "endeavouros")):
        return "arch"
    if any(x in combined for x in ("opensuse", "suse")):
        return "opensuse"
    if "alpine" in combined:
        return "alpine"
    return "unknown"


#: The full set of optional dependencies that unlock features in the app.
#: Order matters — displayed top-to-bottom in the first-run dialog.
DEPENDENCIES: list[Dependency] = [
    Dependency(
        key="node",
        name="Node.js (npx)",
        command="npx",
        purpose="MCP servers: sequential-thinking, memory, filesystem, github, playwright, …",
        install_urls={
            "debian": "sudo apt install nodejs npm",
            "fedora": "sudo dnf install nodejs npm",
            "arch": "sudo pacman -S nodejs npm",
            "opensuse": "sudo zypper install nodejs npm",
            "alpine": "sudo apk add nodejs npm",
            "unknown": "https://nodejs.org/",
        },
    ),
    Dependency(
        key="uv",
        name="uv (uvx)",
        command="uvx",
        purpose="MCP servers: fetch, git",
        install_urls={
            "debian": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "fedora": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "arch": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "opensuse": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "alpine": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "unknown": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        },
    ),
    Dependency(
        key="docker",
        name="Docker",
        command="docker",
        purpose="Docker panel (containers, images, logs)",
        install_urls={
            "debian": "https://docs.docker.com/engine/install/ubuntu/",
            "fedora": "https://docs.docker.com/engine/install/fedora/",
            "arch": "sudo pacman -S docker",
            "opensuse": "https://docs.docker.com/engine/install/",
            "alpine": "sudo apk add docker",
            "unknown": "https://docs.docker.com/engine/install/",
        },
    ),
    Dependency(
        key="kubectl",
        name="kubectl",
        command="kubectl",
        purpose="Kubernetes panel (pods, deployments, services)",
        install_urls={
            "debian": "sudo snap install kubectl --classic",
            "fedora": "sudo dnf install kubernetes-client",
            "arch": "sudo pacman -S kubectl",
            "opensuse": "sudo zypper install kubernetes-client",
            "alpine": "sudo apk add kubectl",
            "unknown": "https://kubernetes.io/docs/tasks/tools/",
        },
    ),
    Dependency(
        key="gh",
        name="GitHub CLI (gh)",
        command="gh",
        purpose="CI/CD panel (GitHub Actions workflow runs and logs)",
        install_urls={
            "debian": "sudo apt install gh",
            "fedora": "sudo dnf install gh",
            "arch": "sudo pacman -S github-cli",
            "opensuse": "sudo zypper install gh",
            "alpine": "sudo apk add github-cli",
            "unknown": "https://cli.github.com/",
        },
    ),
    Dependency(
        key="arduino-cli",
        name="Arduino CLI",
        command="arduino-cli",
        purpose="Arduino panel — compile and upload C++ sketches",
        install_urls={
            # The upstream installer drops a single static binary
            # under ``$HOME/.local/bin`` (or ``BINDIR=…`` if set) and
            # works on every distro. Using it everywhere keeps the
            # install path consistent — earlier versions suggested
            # ``yay -S arduino-cli`` on Arch which silently failed
            # for users without an AUR helper installed.
            "debian": (
                "curl -fsSL https://raw.githubusercontent.com/arduino/"
                "arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh"
            ),
            "fedora": (
                "curl -fsSL https://raw.githubusercontent.com/arduino/"
                "arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh"
            ),
            "arch": (
                "curl -fsSL https://raw.githubusercontent.com/arduino/"
                "arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh"
            ),
            "opensuse": (
                "curl -fsSL https://raw.githubusercontent.com/arduino/"
                "arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh"
            ),
            "alpine": (
                "curl -fsSL https://raw.githubusercontent.com/arduino/"
                "arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh"
            ),
            "unknown": "https://arduino.github.io/arduino-cli/latest/installation/",
        },
    ),
    Dependency(
        key="mpremote",
        name="mpremote (MicroPython)",
        command="mpremote",
        purpose="Arduino panel — upload Python code to MicroPython boards",
        install_urls={
            "debian": "pip install --user mpremote",
            "fedora": "pip install --user mpremote",
            "arch": "pip install --user mpremote",
            "opensuse": "pip install --user mpremote",
            "alpine": "pip install --user mpremote",
            "unknown": "pip install --user mpremote",
        },
        # mpremote is now a hard dep in pyproject.toml, so for any
        # standard install it's importable from the running app's
        # interpreter even when its console-script wrapper isn't on
        # PATH. The ``importable_module`` check is the canonical
        # one. ``python_module`` stays as a third-tier fallback for
        # the legacy case where someone has mpremote under a
        # different Python that happens to be on PATH.
        importable_module="mpremote",
        python_module="mpremote",
        requires_root=False,
    ),
    Dependency(
        key="pyserial",
        name="pyserial",
        # No executable — pyserial is a pure-Python library that the
        # Arduino panel imports directly. Detection is via the
        # ``importable_module`` field below.
        command="",
        purpose=(
            "Arduino panel — board detection (USB serial port scan). "
            "Required when arduino-cli isn't installed and as a fallback "
            "for cheap clones (CH340, ESP, Pico) it doesn't recognise."
        ),
        install_urls={
            "debian": "pip install --user pyserial",
            "fedora": "pip install --user pyserial",
            "arch": "pip install --user pyserial",
            "opensuse": "pip install --user pyserial",
            "alpine": "pip install --user pyserial",
            "unknown": "pip install --user pyserial",
        },
        importable_module="serial",
        requires_root=False,
    ),
    Dependency(
        key="git",
        name="Git",
        command="git",
        # Used by the Git panel, the CI/CD panel, the AI's git_*
        # tools, ``Sandbox`` change tracking, and the diff review
        # engine. Silent failure here breaks half the app, so it
        # earns its place in the first-run dialog.
        purpose="Git panel, CI/CD panel, diff review, AI git_* tools",
        install_urls={
            "debian": "sudo apt install git",
            "fedora": "sudo dnf install git",
            "arch": "sudo pacman -S git",
            "opensuse": "sudo zypper install git",
            "alpine": "sudo apk add git",
            "unknown": "https://git-scm.com/downloads",
        },
    ),
    Dependency(
        key="ffmpeg",
        name="ffmpeg",
        command="ffmpeg",
        # Drives the Video editor panel — every operation the AI
        # plans (trim, scale, format convert, audio extract,
        # watermark, slideshow, …) shells out to ffmpeg via
        # ``shell_exec``. The same package ships ``ffprobe``,
        # which the panel uses on input load to display codec /
        # duration / resolution / fps inline (no separate
        # detection entry — they're inseparable in practice).
        purpose=(
            "Video editor panel — clip metadata (ffprobe) plus trim, "
            "scale, convert, audio-extract via AI"
        ),
        install_urls={
            "debian": "sudo apt install ffmpeg",
            "fedora": "sudo dnf install ffmpeg",
            "arch": "sudo pacman -S ffmpeg",
            "opensuse": "sudo zypper install ffmpeg",
            "alpine": "sudo apk add ffmpeg",
            "unknown": "https://ffmpeg.org/download.html",
        },
    ),
]


def missing_dependencies() -> list[Dependency]:
    """Return the subset of DEPENDENCIES that are not on PATH."""
    return [d for d in DEPENDENCIES if not d.is_installed()]


def has_pkexec() -> bool:
    """True if ``pkexec`` (polkit) is available for GUI sudo prompts."""
    return shutil.which("pkexec") is not None


def _find_terminal_emulator() -> tuple[str, list[str]] | None:
    """Return ``(executable, argv_prefix)`` for the first available terminal.

    The prefix is the arguments that precede the command to run. Falls back
    through a handful of common Linux terminals.
    """
    # xfce4-terminal and mate-terminal use -x (execute with argv list)
    # rather than -e (single command string) so bash + -c + script are
    # passed as separate argv elements. gnome-terminal takes the command
    # after "--". kitty takes the argv directly with no flag.
    candidates: list[tuple[str, list[str]]] = [
        ("gnome-terminal", ["--", "bash", "-c"]),
        ("konsole", ["-e", "bash", "-c"]),
        ("xfce4-terminal", ["-x", "bash", "-c"]),
        ("mate-terminal", ["-x", "bash", "-c"]),
        ("tilix", ["-e", "bash", "-c"]),
        ("kitty", ["bash", "-c"]),
        ("alacritty", ["-e", "bash", "-c"]),
        ("xterm", ["-e", "bash", "-c"]),
        ("x-terminal-emulator", ["-e", "bash", "-c"]),
    ]
    for exe, argv in candidates:
        if shutil.which(exe):
            logger.debug("Terminal emulator found: %s", exe)
            return exe, argv
        logger.debug("Terminal emulator not found, skipping: %s", exe)
    return None


def _new_installer_log_path() -> Path:
    """Return a fresh unique log file path under the system temp dir."""
    fd, path = tempfile.mkstemp(prefix="polyglot-ai-installer-", suffix=".log")
    import os

    os.close(fd)
    return Path(path)


def new_installer_log_path() -> Path:
    """Public alias of :func:`_new_installer_log_path`.

    Exposed so the GUI can pre-allocate a log path, start tailing
    it, and then hand the same path into :func:`install_system_deps`
    via the ``log_path`` argument. Without this, the dialog had no
    way to know where the installer was writing until *after* the
    install finished — too late to show live progress.
    """
    return _new_installer_log_path()


@dataclass
class InstallResult:
    """Result of running an installer."""

    ok: bool
    message: str
    log_path: Path | None = None  # Path to captured installer output, if any


@dataclass(frozen=True)
class InstallProgress:
    """One progress tick emitted by the installer.

    ``current`` and ``total`` are 1-indexed; ``slug`` is the
    dependency key (``arduino-cli``, ``mpremote``, etc.) so the
    dialog can map back to the friendly display name. ``done`` is
    True for the final marker that fires after every command has
    run — at that point ``current`` and ``total`` will both be
    ``total`` so a progress bar can fill cleanly.
    """

    current: int
    total: int
    slug: str
    done: bool = False


# Regex applied per log line. Using a sentinel prefix (rather than
# the human-readable ``==> Installing X``) keeps the parser
# tight enough that the upstream installer's own output (e.g.
# ``apt`` printing package names) can't fake a marker.
_PROGRESS_MARKER_RE = re.compile(
    r"^@@PROGRESS@@\s+(?:(?P<done>done)|(?P<current>\d+)/(?P<total>\d+)\s+(?P<slug>\S+))\s*$"
)


def parse_progress_marker(line: str) -> InstallProgress | None:
    """Parse one log line; return ``None`` when the line isn't a marker.

    Public so the dependency dialog and unit tests can use the same
    regex — keeps "what does a marker look like?" in one place.
    """
    m = _PROGRESS_MARKER_RE.match(line.rstrip("\r\n"))
    if m is None:
        return None
    if m.group("done"):
        return InstallProgress(current=0, total=0, slug="", done=True)
    return InstallProgress(
        current=int(m.group("current")),
        total=int(m.group("total")),
        slug=m.group("slug"),
    )


#: How long to wait for pkexec / apt / dnf / etc. to finish.
_INSTALLER_TIMEOUT = 600  # 10 minutes


def _build_chained_command(
    deps: list[Dependency],
    distro: Distro,
    *,
    start_idx: int,
    total: int,
    emit_done: bool,
) -> str:
    """Compose a single shell pipeline that installs ``deps`` in order.

    Each dependency emits a ``@@PROGRESS@@ N/M slug`` marker before
    its install command runs so the GUI's log tailer can advance
    its progress bar. ``start_idx`` lets us number across two
    batches — the userland batch goes first (1..U), the root batch
    follows (U+1..U+R) — without the bar resetting in the middle.
    ``;`` between commands so one failure doesn't skip the rest.
    """
    pieces: list[str] = []
    for i, dep in enumerate(deps, start=start_idx):
        hint = dep.install_hint(distro)
        marker = f"@@PROGRESS@@ {i}/{total} {dep.key}"
        pieces.append(f"echo '{marker}'; echo '==> Installing {dep.name}'; {hint}")
    chained = " ; ".join(pieces)
    if emit_done:
        # Final marker tells the GUI to fill the bar to 100 % and
        # switch the label to "Wrapping up…" while the shell flushes.
        chained = f"{chained} ; echo '@@PROGRESS@@ done'"
    return chained


def _run_userland_install(chained: str, log_path: Path) -> InstallResult:
    """Run a userland install pipeline as the calling user (no pkexec).

    Used for pip-installed packages (``pyserial``, ``mpremote``)
    where ``pip install --user`` writes into ``~/.local/`` and
    sudo'ing it would either install into root's home or trip
    PEP 668's externally-managed marker.

    Truncates the log file (``"wb"``) — the userland pass is
    always the *first* pass, so any prior content is from a
    previous run and isn't relevant.
    """
    logger.info(
        "_run_userland_install: running as user (timeout=%ds, log=%s)",
        _INSTALLER_TIMEOUT,
        log_path,
    )
    try:
        proc = subprocess.Popen(
            ["sh", "-c", chained],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as e:
        logger.exception("_run_userland_install: could not launch sh")
        return InstallResult(ok=False, message=f"Could not launch installer: {e}")

    try:
        with log_path.open("wb") as log:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
        proc.wait(timeout=_INSTALLER_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return InstallResult(
            ok=False,
            message=(
                f"User-level installer timed out after {_INSTALLER_TIMEOUT // 60} "
                f"minutes. See log: {log_path}"
            ),
            log_path=log_path,
        )

    if proc.returncode != 0:
        return InstallResult(
            ok=False,
            message=(
                f"User-level install reported errors (exit {proc.returncode}). See log: {log_path}"
            ),
            log_path=log_path,
        )

    logger.info("_run_userland_install: success")
    return InstallResult(
        ok=True,
        message="User-level packages installed.",
        log_path=log_path,
    )


def install_system_deps(deps: list[Dependency], *, log_path: Path | None = None) -> InstallResult:
    """Install a set of dependencies, splitting userland from root.

    The deps are bucketed by ``Dependency.requires_root``:

    * **Userland** (e.g. ``pyserial``, ``mpremote``) run first as
      the calling user — no auth prompt, no pkexec.
    * **Root** (e.g. ``apt install nodejs``, ``dnf install gh``)
      then run via a single ``pkexec`` so the user types their
      password exactly once.

    Both batches share a single log file so the dialog's tail
    timer renders one continuous stream, and the progress markers
    are numbered across both batches so the bar advances smoothly.

    Documentation URLs and the special ``uv`` key are skipped
    (the GUI surfaces those separately) — same as before.

    BLOCKING — waits up to _INSTALLER_TIMEOUT seconds *per batch*
    for the installer to return. Callers must run it off the UI
    thread.
    """
    distro = detect_distro()
    skipped: list[str] = []

    def _is_runnable(dep: Dependency) -> bool:
        if dep.key == "uv":
            return False  # userland, handled separately via install_uv()
        hint = dep.install_hint(distro)
        if not hint:
            return False
        if hint.startswith(("http://", "https://")):
            return False
        return True

    runnable = [d for d in deps if _is_runnable(d)]
    skipped = [d.name for d in deps if d.key != "uv" and not _is_runnable(d)]

    if not runnable:
        if skipped:
            logger.warning(
                "install_system_deps: no auto-installable deps on distro=%s (skipped=%s)",
                distro,
                skipped,
            )
            return InstallResult(
                ok=False,
                message=(
                    "None of the missing dependencies can be installed automatically "
                    f"on this distribution. Please install manually: {', '.join(skipped)}"
                ),
            )
        logger.warning(
            "install_system_deps: called with no installable deps — possible state drift"
        )
        return InstallResult(ok=False, message="Nothing to install.")

    # Stable order: userland first (no auth), then root via pkexec.
    user_deps = [d for d in runnable if not d.requires_root]
    root_deps = [d for d in runnable if d.requires_root]
    total = len(runnable)

    if log_path is None:
        log_path = _new_installer_log_path()
    logger.info("install_system_deps: using log file %s", log_path)

    # Pass 1 — userland (pip install --user, etc). Run only if we
    # have userland deps; if we don't, skip straight to pkexec.
    if user_deps:
        user_chained = _build_chained_command(
            user_deps,
            distro,
            start_idx=1,
            total=total,
            # Only emit done if there's no root pass after this.
            emit_done=not root_deps,
        )
        user_result = _run_userland_install(user_chained, log_path)
        if not user_result.ok:
            # Userland failed — don't bother prompting for sudo to
            # finish the root half. The dialog can re-launch later.
            return user_result
        if not root_deps:
            return InstallResult(
                ok=True,
                message=(
                    "Installer finished successfully. Restart Polyglot AI so the new "
                    f"packages are picked up. Full log: {log_path}"
                ),
                log_path=log_path,
            )

    # Pass 2 — root (apt/dnf/pacman/zypper/apk via pkexec).
    chained = _build_chained_command(
        root_deps,
        distro,
        start_idx=len(user_deps) + 1,
        total=total,
        emit_done=True,
    )

    # Append (not truncate) when a userland pass already wrote to
    # this log — otherwise we'd lose the pip output the dialog has
    # already shown the user.
    log_open_mode = "ab" if user_deps else "wb"

    # Prefer pkexec — native GUI password prompt, no terminal pop-up.
    if has_pkexec():
        # Earlier versions piped the install commands through ``tee``
        # *inside the pkexec'd shell* so the log file ended up owned
        # by the calling user. That broke on systems where pkexec
        # runs the shell under a sandboxed namespace (AppArmor
        # profile / systemd PrivateTmp) — tee inside the sandbox
        # couldn't see the user's /tmp file and bailed with
        # "Permission denied", leaving a 0-byte log and rc=1 with
        # the user no clue what went wrong.
        #
        # Now we stream pkexec's stdout from *our* side and write
        # the log file from the user process. No sandbox is involved
        # in the write, so no permission-denied class of failure.
        logger.info("install_system_deps: running via pkexec (timeout=%ds)", _INSTALLER_TIMEOUT)
        try:
            proc = subprocess.Popen(
                ["pkexec", "sh", "-c", chained],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                # Merge stderr into stdout so the log captures both
                # the install's diagnostics *and* pkexec's own
                # error messages in one stream — no separate
                # "stderr was empty / log was empty" guess work.
                stderr=subprocess.STDOUT,
            )
        except OSError as e:
            logger.exception("install_system_deps: could not launch pkexec")
            return InstallResult(ok=False, message=f"Could not launch pkexec: {e}")

        # Stream chunks to the log so the dialog's poll timer can
        # tail it for progress markers as the install runs. 4 KiB
        # chunks balance I/O syscalls against latency — a typical
        # ``apt`` line is well under 1 KiB so the user sees output
        # appear within a fraction of a second of when it was
        # produced.
        try:
            with log_path.open(log_open_mode) as log:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    log.write(chunk)
                    log.flush()
            proc.wait(timeout=_INSTALLER_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            logger.error(
                "install_system_deps: pkexec timed out after %ds; log=%s",
                _INSTALLER_TIMEOUT,
                log_path,
            )
            return InstallResult(
                ok=False,
                message=(
                    f"Installer timed out after {_INSTALLER_TIMEOUT // 60} minutes. "
                    f"See log: {log_path}"
                ),
                log_path=log_path,
            )

        if proc.returncode != 0:
            rc = proc.returncode
            # The log file now has both stdout and stderr merged,
            # so we don't need a separate stderr capture — read the
            # log itself for whatever pkexec / the install wrote.
            try:
                stderr_text = log_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                stderr_text = ""

            # If the install never produced any output, the failure
            # was at the pkexec / polkit layer — the chained command
            # never ran. Embed pkexec's own stderr in the log file
            # so the user has *something* to read when they click
            # the path in the dialog.
            # The merged stdout+stderr is in the log file; size > 0
            # means at least *something* ran (even if it errored).
            try:
                log_size = log_path.stat().st_size
            except OSError:
                log_size = 0

            # pkexec returns 126 if the user cancelled or auth failed,
            # 127 if the command wasn't found. Anything else is the
            # install command's own exit code.
            if rc in (126, 127):
                hint_msg = (
                    "Authentication was cancelled or failed (no polkit "
                    "agent? on Wayland, install gnome-keyring or "
                    "polkit-kde-agent and log out / back in)."
                    if rc == 126
                    else "pkexec could not find the command."
                )
            elif log_size == 0:
                # The install commands didn't print anything — pkexec
                # auth probably succeeded but the elevated shell
                # bailed before the first echo (e.g. the chained
                # command's first token wasn't on PATH).
                hint_msg = (
                    "pkexec ran but the install never produced any "
                    "output. Try running the commands manually in a "
                    "terminal — click 'Copy all commands' then paste "
                    "in your shell."
                )
            else:
                hint_msg = "The installer reported errors. See the log file for details."
            logger.error(
                "install_system_deps: pkexec returned rc=%d; log=%s; tail=%r",
                rc,
                log_path,
                stderr_text[-500:],
            )
            return InstallResult(
                ok=False,
                message=f"{hint_msg} Exit code {rc}. See log for details: {log_path}",
                log_path=log_path,
            )

        logger.info("install_system_deps: pkexec succeeded; log=%s", log_path)
        return InstallResult(
            ok=True,
            message=(
                "Installer finished successfully. Restart Polyglot AI so the new "
                f"binaries show up on PATH. Full log: {log_path}"
            ),
            log_path=log_path,
        )

    # Fallback — open a terminal with the chained command. We can't
    # wait for the result here because the terminal spawns detached,
    # but we still pipe output through tee so the user has a log to
    # read afterwards.
    term = _find_terminal_emulator()
    if term is None:
        logger.error("install_system_deps: no terminal emulator found")
        return InstallResult(
            ok=False,
            message=(
                "No terminal emulator found and pkexec is unavailable. "
                "Please run this command manually:\n\n"
                f'sudo sh -c "{chained}"'
            ),
        )
    exe, argv_prefix = term
    wrapped = (
        f"({chained}) 2>&1 | tee {shlex_quote(str(log_path))} | sudo -S sh -c '"
        f"cat > /dev/null' ; "
        'echo ""; echo "Press Enter to close…"; read'
    )
    # The above is intentionally simple — most users will just run
    # the script interactively. Wrap with a plain sudo prompt inline.
    wrapped = (
        f'sudo sh -c "({chained}) 2>&1 | tee {shlex_quote(str(log_path))}" ; '
        'echo ""; echo "Press Enter to close…"; read'
    )
    logger.info("install_system_deps: spawning terminal %s", exe)
    try:
        subprocess.Popen(
            [exe, *argv_prefix, wrapped],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.exception("install_system_deps: could not launch %s", exe)
        return InstallResult(ok=False, message=f"Could not launch {exe}: {e}")

    return InstallResult(
        ok=True,  # spawn succeeded; we cannot verify install itself
        message=(
            f"Opened {exe}. Enter your sudo password in that window. When it "
            f"finishes, restart Polyglot AI. Install log: {log_path}"
        ),
        log_path=log_path,
    )


def shlex_quote(s: str) -> str:
    """Shell-quote a string (thin wrapper to avoid polluting top-level imports)."""
    import shlex

    return shlex.quote(s)


_UV_INSTALLER_URL = "https://astral.sh/uv/install.sh"


def install_uv() -> InstallResult:
    """Run the official uv installer script (userland, no sudo).

    BLOCKING — runs the installer synchronously. Callers must run this
    off the UI thread.

    Captures full stdout+stderr to a temp log file, logs the command
    being run, and truncates only the *UI* message (the log file keeps
    the full output for post-mortem). Returns an ``InstallResult`` with
    a real success flag driven by the installer's exit code.
    """
    log_path = _new_installer_log_path()
    cmd = f"curl -LsSf {_UV_INSTALLER_URL} | sh"
    logger.info("install_uv: running %s (log=%s)", cmd, log_path)

    try:
        # shell=True is required for the pipe; the URL is a hardcoded
        # constant (_UV_INSTALLER_URL), no user input reaches the shell.
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        logger.error(
            "install_uv: timed out after 120s; partial stdout=%r stderr=%r",
            (e.stdout or "")[:500],
            (e.stderr or "")[:500],
        )
        return InstallResult(
            ok=False,
            message="Installer timed out after 2 minutes. Try running it manually.",
        )
    except OSError as e:
        logger.exception("install_uv: could not launch installer")
        return InstallResult(ok=False, message=f"Could not launch installer: {e}")

    try:
        log_path.write_text(
            f"$ {cmd}\n\n--- stdout ---\n{result.stdout}\n\n--- stderr ---\n{result.stderr}\n"
            f"\n--- exit code ---\n{result.returncode}\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("install_uv: failed to write log file %s: %s", log_path, e)
        log_path = None  # type: ignore[assignment]

    if result.returncode != 0:
        logger.error(
            "install_uv: rc=%d\nstdout=%s\nstderr=%s",
            result.returncode,
            result.stdout,
            result.stderr,
        )
        stderr = (result.stderr or result.stdout or "").strip()
        log_hint = f" Full log: {log_path}" if log_path else ""
        return InstallResult(
            ok=False,
            message=f"Installer exited with code {result.returncode}: {stderr[:300]}{log_hint}",
            log_path=log_path,
        )

    logger.info("install_uv: success (log=%s)", log_path)
    return InstallResult(
        ok=True,
        message=(
            "uv installed successfully. You may need to restart Polyglot AI "
            "(or open a new shell) for it to appear on PATH."
        ),
        log_path=log_path,
    )
