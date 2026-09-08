"""SSH session helpers for the built-in terminal.

First cut of "remote projects": an SSH session opened *in the terminal
panel* (the shell runs ``ssh …`` like the user would type it), with
targets remembered and ``~/.ssh/config`` aliases offered as
suggestions. Editing remote files in the editor is not part of this —
the terminal is the remote surface for now.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]%]+$")


def valid_host(host: str) -> bool:
    """Hostname / IP / config alias that's safe to hand to ``ssh``.

    Rejects whitespace and a leading ``-`` so a pasted value can't turn
    into an ssh option (``-oProxyCommand=…``).
    """
    return bool(host) and not host.startswith("-") and _HOST_RE.match(host) is not None


@dataclass
class SshTarget:
    host: str
    user: str = ""
    port: int = 22
    identity_file: str = ""

    @property
    def label(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def to_string(self) -> str:
        """``user@host:port`` form used for the recent-targets list."""
        text = self.label
        if self.port and self.port != 22:
            text += f":{self.port}"
        return text

    def argv(self) -> list[str]:
        args = ["ssh"]
        if self.port and self.port != 22:
            args += ["-p", str(self.port)]
        if self.identity_file:
            args += ["-i", self.identity_file]
        args.append(self.label)
        return args

    def command(self) -> str:
        """Shell-quoted command line for the terminal."""
        return shlex.join(self.argv())


def parse_target(text: str) -> SshTarget | None:
    """Parse ``[user@]host[:port]`` (also tolerates a leading ``ssh``)."""
    text = text.strip()
    if text.startswith("ssh "):
        text = text[4:].strip()
    if not text:
        return None
    user = ""
    if "@" in text:
        user, text = text.rsplit("@", 1)
    port = 22
    # IPv6 literals contain colons; only treat a trailing :digits as a port.
    m = re.match(r"^(.*):(\d{1,5})$", text)
    if m and "]" not in text[m.end(1) :]:
        text, port = m.group(1), int(m.group(2))
    if not valid_host(text) or not (0 < port < 65536):
        return None
    return SshTarget(host=text, user=user.strip(), port=port)


def config_hosts(config_path: Path | None = None) -> list[str]:
    """Concrete ``Host`` aliases from ``~/.ssh/config`` (no wildcards)."""
    path = config_path or Path.home() / ".ssh" / "config"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hosts: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.lower().startswith("host ") and not stripped.lower().startswith("host\t"):
            continue
        for name in stripped.split(None, 1)[1].split():
            if any(ch in name for ch in "*?!") or name in hosts:
                continue
            hosts.append(name)
    return hosts


def ssm_command(instance_id: str, profile: str = "", region: str = "") -> str:
    """``aws ssm start-session`` line for an EC2 instance (no SSH port needed)."""
    args = ["aws", "ssm", "start-session", "--target", instance_id]
    if profile:
        args += ["--profile", profile]
    if region:
        args += ["--region", region]
    return shlex.join(args)
