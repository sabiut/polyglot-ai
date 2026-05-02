"""Detect optional system dependencies (Node.js, uv, docker, kubectl, gh, git).

Used on first launch to warn users about missing runtimes and offer
guided install commands for their distro. Nothing here runs arbitrary
commands except the opt-in uv installer.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Distro = Literal["debian", "fedora", "arch", "opensuse", "alpine", "unknown"]


@dataclass
class Dependency:
    """A single optional dependency that may or may not be installed."""

    key: str  # stable id, e.g. "node"
    name: str  # user-facing label, e.g. "Node.js"
    command: str  # executable to look for on PATH
    purpose: str  # one-line description of what it unlocks
    install_urls: dict[Distro, str]  # distro → install command or URL

    def is_installed(self) -> bool:
        return shutil.which(self.command) is not None

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


def install_system_deps(deps: list[Dependency], *, log_path: Path | None = None) -> InstallResult:
    """Install a set of system dependencies via ``pkexec`` or a terminal.

    Chains every non-URL install command from ``deps`` with ``;`` (so one
    failed package doesn't prevent later ones from being attempted) and
    runs them with elevated privileges. Captures all stdout/stderr to a
    temp log file and waits for the process to finish (pkexec path).
    The terminal-fallback path cannot be waited on, but still directs
    output through ``tee`` to the same log file for post-mortem.

    BLOCKING — this function waits up to _INSTALLER_TIMEOUT seconds for
    the installer to return. Callers must run it off the UI thread
    (e.g. via ``asyncio.to_thread`` or a worker).

    The returned ``InstallResult`` carries a real success flag based on
    the installer's exit code (pkexec path) or on whether the terminal
    spawn succeeded (fallback path), plus a log path the caller can
    surface to the user.
    """
    distro = detect_distro()
    commands: list[str] = []
    skipped: list[str] = []
    # Filter once so the progress markers below use the same total
    # the GUI's progress bar parses out.
    installable = [
        d
        for d in deps
        if d.key != "uv"
        and (h := d.install_hint(distro))
        and not h.startswith(("http://", "https://"))
    ]
    for dep in deps:
        hint = dep.install_hint(distro)
        if not hint:
            skipped.append(dep.name)
            continue
        if hint.startswith(("http://", "https://")):
            # Documentation URL — we can't script it.
            skipped.append(dep.name)
            continue
        if dep.key == "uv":
            # Userland — handled separately via install_uv().
            continue
        # Emit a structured progress marker the GUI can parse to
        # drive a real progress bar. Format: ``@@PROGRESS@@ N/M slug``.
        # Using a sentinel prefix instead of the human-readable
        # ``==> Installing`` line makes the regex tight enough to
        # ignore anything inside the upstream installer's own output.
        idx = installable.index(dep) + 1
        total = len(installable)
        marker = f"@@PROGRESS@@ {idx}/{total} {dep.key}"
        commands.append(f"echo '{marker}'; echo '==> Installing {dep.name}'; {hint}")

    if not commands:
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

    # ';' between deps so one failure doesn't skip the rest.
    # Final ``@@PROGRESS@@ done`` lets the GUI fill the bar to 100 %
    # and switch the label from "Installing X of Y" to "Wrapping up…"
    # while the shell flushes its tail end.
    chained = " ; ".join(commands) + " ; echo '@@PROGRESS@@ done'"

    if log_path is None:
        log_path = _new_installer_log_path()
    logger.info("install_system_deps: using log file %s", log_path)

    # Prefer pkexec — native GUI password prompt, no terminal pop-up.
    if has_pkexec():
        # pkexec cannot write to a file owned by the invoking user by
        # default when running as root, so we redirect via `tee` which
        # the invoking user controls.
        tee_cmd = f"({chained}) 2>&1 | tee {shlex_quote(str(log_path))}"
        logger.info("install_system_deps: running via pkexec (timeout=%ds)", _INSTALLER_TIMEOUT)
        try:
            result = subprocess.run(
                ["pkexec", "sh", "-c", tee_cmd],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_INSTALLER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
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
        except OSError as e:
            logger.exception("install_system_deps: could not launch pkexec")
            return InstallResult(ok=False, message=f"Could not launch pkexec: {e}")

        if result.returncode != 0:
            # pkexec returns 126 if the user cancelled or auth failed,
            # 127 if the command wasn't found. Anything else is the
            # installer's own exit code (apt/dnf rc).
            rc = result.returncode
            if rc in (126, 127):
                hint_msg = (
                    "Authentication was cancelled or failed."
                    if rc == 126
                    else "pkexec could not find the command."
                )
            else:
                hint_msg = "The installer reported errors."
            logger.error("install_system_deps: pkexec returned rc=%d; log=%s", rc, log_path)
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
