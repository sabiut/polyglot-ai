"""Reaching a database that isn't reachable directly.

Most RDS instances are private ("Publicly accessible: no"): only machines
inside the VPC can open a connection. Two standard ways in, both of which
end with the database appearing on a local port:

* an **SSH tunnel** through a bastion host (``ssh -L``), using your SSH
  agent or a key file — never a typed password, since nothing here can
  answer a prompt;
* **AWS Session Manager** port forwarding through any EC2 instance that
  runs the SSM agent (``aws ssm start-session`` with the
  ``AWS-StartPortForwardingSessionToRemoteHost`` document), which needs
  no open SSH port and no key.

Also here: IAM database authentication, which swaps the stored password
for a 15-minute token made from the user's AWS credentials.

Blocking by design — callers run these off the GUI thread.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass

from polyglot_ai.core.ssh import valid_host

logger = logging.getLogger(__name__)

_INSTANCE_ID_RE = re.compile(r"^m?i-[0-9a-f]{8,}$")


class ConnectionSetupError(Exception):
    """Couldn't prepare the route or credentials; the message is user-facing."""


@dataclass
class TunnelSpec:
    kind: str  # "ssh" | "ssm"
    # ssh
    host: str = ""
    user: str = ""
    port: int = 22
    identity_file: str = ""
    # ssm
    target: str = ""  # EC2 instance id
    profile: str = ""
    region: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> TunnelSpec | None:
        if not data or data.get("kind") not in ("ssh", "ssm"):
            return None
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)

    def to_dict(self) -> dict:
        """Only the fields this kind of tunnel uses, and only those that are set."""
        mine = (
            ("kind", "host", "user", "port", "identity_file")
            if self.kind == "ssh"
            else ("kind", "target", "profile", "region")
        )
        return {k: v for k, v in asdict(self).items() if k in mine and v not in ("", None)}

    @property
    def label(self) -> str:
        if self.kind == "ssm":
            return f"Session Manager via {self.target}"
        who = f"{self.user}@{self.host}" if self.user else self.host
        return f"SSH via {who}"

    def argv(self, remote_host: str, remote_port: int, local_port: int) -> list[str]:
        """The command that forwards ``127.0.0.1:local_port`` to the database."""
        if not valid_host(remote_host):
            raise ConnectionSetupError(f"“{remote_host}” isn't a valid database host name.")
        if self.kind == "ssh":
            if not valid_host(self.host):
                raise ConnectionSetupError(f"“{self.host}” isn't a valid bastion host name.")
            if self.user and not re.fullmatch(r"[A-Za-z0-9._-]+", self.user):
                raise ConnectionSetupError(f"“{self.user}” isn't a valid SSH user name.")
            args = [
                "ssh",
                "-N",  # no remote command, just the forward
                "-L",
                f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
                # Fail instead of prompting: there is no terminal to answer.
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "ServerAliveInterval=30",
            ]
            if self.port and self.port != 22:
                args += ["-p", str(self.port)]
            if self.identity_file:
                args += ["-i", os.path.expanduser(self.identity_file)]
            args.append(f"{self.user}@{self.host}" if self.user else self.host)
            return args
        if not _INSTANCE_ID_RE.match(self.target):
            raise ConnectionSetupError(
                f"“{self.target}” isn't an EC2 instance ID (expected something like i-0abc123…)."
            )
        parameters = {
            "host": [remote_host],
            "portNumber": [str(remote_port)],
            "localPortNumber": [str(local_port)],
        }
        args = [
            "aws",
            "ssm",
            "start-session",
            "--target",
            self.target,
            "--document-name",
            "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters",
            json.dumps(parameters),
        ]
        if self.profile:
            args += ["--profile", self.profile]
        if self.region:
            args += ["--region", self.region]
        return args


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _explain(kind: str, output: str) -> str:
    """Turn ssh / aws stderr into something the user can act on."""
    text = output.strip()
    low = text.lower()
    if "host key verification failed" in low:
        return (
            "The bastion's host key isn't trusted yet. Connect to it once from the "
            "terminal (ssh user@host) to accept the key, then try again."
        )
    if "permission denied" in low:
        return (
            "The bastion refused the login. Only key-based logins work here: load your "
            "key into the SSH agent (ssh-add) or choose a key file."
        )
    if "could not resolve hostname" in low or "name or service not known" in low:
        return "The bastion's host name couldn't be resolved."
    if "sessionmanagerplugin is not found" in low or "session-manager-plugin" in low:
        return (
            "The AWS Session Manager plugin isn't installed. Install "
            "session-manager-plugin, then try again."
        )
    if "targetnotconnected" in low:
        return (
            "That EC2 instance isn't connected to Session Manager — it needs the SSM agent "
            "running and an instance role that allows it."
        )
    if "accessdenied" in low or "not authorized" in low:
        return "AWS refused to start the session — this profile lacks permission for it."
    if "expiredtoken" in low or "token has expired" in low:
        return "Your AWS session has expired — run `aws sso login` (or refresh your credentials)."
    last = text.splitlines()[-1] if text else ""
    what = "SSH tunnel" if kind == "ssh" else "Session Manager tunnel"
    return f"The {what} closed before it was ready. {last[:200]}".strip()


#: Tunnels whose process may still be running. A tunnel is its own
#: session (see ``Tunnel.start``), so it does not die with the app: left
#: alone it keeps a login on the bastion and a port on this machine open
#: until the far end times it out.
_live: set[Tunnel] = set()
_live_lock = threading.Lock()


def stop_all() -> int:
    """Stop every tunnel that is still running. Returns how many there were.

    The app's shutdown disconnects its databases, which stops their
    tunnels; this is for whatever that didn't reach — a disconnect that
    timed out or raised, a connection attempt still in flight, or an
    exit that skipped the shutdown sequence (it is also an ``atexit``
    hook). Blocking, like ``Tunnel.stop``.
    """
    with _live_lock:
        tunnels = list(_live)
    for tunnel in tunnels:
        try:
            tunnel.stop()
        except Exception:
            logger.debug("Couldn't stop tunnel %s", tunnel.spec.label, exc_info=True)
    return len(tunnels)


atexit.register(stop_all)


class Tunnel:
    """A running port forward. ``start()`` returns the local port to dial."""

    def __init__(self, spec: TunnelSpec, remote_host: str, remote_port: int) -> None:
        self.spec = spec
        self._remote = (remote_host, remote_port)
        self._proc: subprocess.Popen | None = None
        self._log = None
        self.local_port = 0

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 25.0) -> int:
        if self.spec.kind == "ssm":
            for tool, hint in (
                ("aws", "The AWS CLI (`aws`) isn't installed."),
                (
                    "session-manager-plugin",
                    "The AWS Session Manager plugin isn't installed. Install "
                    "session-manager-plugin, then try again.",
                ),
            ):
                if shutil.which(tool) is None:
                    raise ConnectionSetupError(hint)
        elif shutil.which("ssh") is None:
            raise ConnectionSetupError("The `ssh` command isn't installed.")

        self.local_port = _free_port()
        argv = self.spec.argv(*self._remote, self.local_port)
        # Output goes to a file, not a pipe: nobody reads it while the
        # tunnel runs, and a full pipe would stall the process.
        self._log = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                env=dict(os.environ, AWS_PAGER=""),
                # Its own process group: `aws ssm` runs the plugin as a
                # child, and both must go when the tunnel is stopped.
                start_new_session=True,
            )
        except OSError as exc:
            self._close_log()
            raise ConnectionSetupError(f"Couldn't start the tunnel: {exc}") from exc
        with _live_lock:
            _live.add(self)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                message = _explain(self.spec.kind, self._output())
                self.stop()
                raise ConnectionSetupError(message)
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.5):
                    logger.info("Tunnel ready on port %d (%s)", self.local_port, self.spec.label)
                    return self.local_port
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise ConnectionSetupError(
            f"The tunnel ({self.spec.label}) didn't come up within {int(timeout)} seconds."
        )

    def _output(self) -> str:
        if self._log is None:
            return ""
        try:
            self._log.seek(0)
            return self._log.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def stop(self) -> None:
        with _live_lock:
            _live.discard(self)
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    proc.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
        self._close_log()


def iam_auth_token(host: str, port: int, user: str, profile: str = "", region: str = "") -> str:
    """A 15-minute IAM authentication token to use as the database password.

    ``host`` must be the instance's real endpoint, even when the connection
    itself goes through a tunnel: the token is signed for that name.
    Signing happens locally in the CLI; nothing is sent to AWS here.
    """
    from polyglot_ai.core import aws_cli

    output, code = aws_cli.run(
        [
            "rds",
            "generate-db-auth-token",
            "--hostname",
            host,
            "--port",
            str(port),
            "--username",
            user,
        ],
        profile=profile,
        region=region,
        json_output=False,
    )
    if code != 0 or not output.strip():
        raise ConnectionSetupError(
            "Couldn't create an IAM authentication token: "
            + (output.removeprefix("Error: ") or "no token returned")
        )
    return output.strip()
