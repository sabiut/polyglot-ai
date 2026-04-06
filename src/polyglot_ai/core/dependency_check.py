"""Detect optional system dependencies (Node.js, uv, docker, kubectl, gh, git).

Used on first launch to warn users about missing runtimes and offer
guided install commands for their distro. Nothing here runs arbitrary
commands except the opt-in uv installer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
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
    candidates: list[tuple[str, list[str]]] = [
        ("gnome-terminal", ["--", "bash", "-c"]),
        ("konsole", ["-e", "bash", "-c"]),
        ("xfce4-terminal", ["-e", "bash -c"]),
        ("mate-terminal", ["-e", "bash -c"]),
        ("tilix", ["-e", "bash", "-c"]),
        ("kitty", ["bash", "-c"]),
        ("alacritty", ["-e", "bash", "-c"]),
        ("xterm", ["-e", "bash", "-c"]),
        ("x-terminal-emulator", ["-e", "bash", "-c"]),
    ]
    for exe, argv in candidates:
        if shutil.which(exe):
            return exe, argv
    return None


def install_system_deps(deps: list[Dependency]) -> tuple[bool, str]:
    """Install a set of system dependencies via ``pkexec`` or a terminal.

    Chains every non-URL install command from ``deps`` with ``&&`` and
    runs it with elevated privileges. Prefers ``pkexec`` (native GUI sudo
    prompt, no terminal) and falls back to opening the user's terminal
    emulator with the command pre-populated.

    Returns ``(success, message)``. ``success=True`` means the child
    process launched cleanly — the caller should always tell the user
    to restart Polyglot AI afterwards so new binaries appear on PATH.
    """
    distro = detect_distro()
    commands: list[str] = []
    skipped: list[str] = []
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
        commands.append(f"echo '==> Installing {dep.name}'; {hint}")

    if not commands:
        if skipped:
            return False, (
                "None of the missing dependencies can be installed automatically "
                f"on this distribution. Please install manually: {', '.join(skipped)}"
            )
        return False, "Nothing to install."

    chained = " && ".join(commands)

    # Prefer pkexec — native GUI password prompt, no terminal pop-up.
    if has_pkexec():
        try:
            subprocess.Popen(
                ["pkexec", "sh", "-c", chained],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            return False, f"Could not launch pkexec: {e}"
        return True, (
            "Installer launched via pkexec. Enter your password in the prompt. "
            "When it finishes, restart Polyglot AI."
        )

    # Fallback — open a terminal with the chained command.
    term = _find_terminal_emulator()
    if term is None:
        return False, (
            "No terminal emulator found. Please run this command manually:\n\n"
            f'sudo sh -c "{chained}"'
        )
    exe, argv_prefix = term
    # Wrap the chained command so the terminal stays open after install
    # completes, letting the user read any output.
    wrapped = f'sudo sh -c "{chained}" ; echo ""; echo "Press Enter to close…"; read'
    try:
        subprocess.Popen(
            [exe, *argv_prefix, wrapped],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        return False, f"Could not launch {exe}: {e}"
    return True, (
        f"Opened {exe} with the install commands. "
        "Enter your sudo password, then restart Polyglot AI when it finishes."
    )


def install_uv() -> tuple[bool, str]:
    """Run the official uv installer script (userland, no sudo).

    Returns ``(success, message)``. The installer writes to ``~/.local/bin``
    by default, so after a successful install the user may need to add
    that to their PATH or restart the app.
    """
    try:
        # Two-stage: curl the script, pipe to sh. shell=True is required
        # for the pipe but the command is fixed and not user-supplied.
        result = subprocess.run(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "Installer timed out after 2 minutes. Try running it manually."
    except OSError as e:
        return False, f"Could not launch installer: {e}"

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        return False, f"Installer exited with code {result.returncode}: {stderr[:300]}"

    # Success — uv is typically in ~/.local/bin. Caller may need to
    # inform the user to add that to PATH or restart.
    return True, (
        "uv installed successfully. You may need to restart the app "
        "(or open a new shell) for it to appear on PATH."
    )
