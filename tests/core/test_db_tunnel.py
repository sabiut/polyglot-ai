"""SSH / Session Manager tunnels and IAM tokens for private databases.

No real ssh or aws: the "tunnel" is a small local process that listens on
the port it is given.
"""

from __future__ import annotations

import json
import socket
import sys

import pytest

from polyglot_ai.core import aws_cli, db_tunnel
from polyglot_ai.core.db_tunnel import ConnectionSetupError, Tunnel, TunnelSpec, iam_auth_token

RDS = "cm-ecs-au.cbeaskeokhql.ap-southeast-2.rds.amazonaws.com"


class TestCommands:
    def test_ssh_forward(self):
        spec = TunnelSpec(kind="ssh", host="bastion.example.com", user="ec2-user")
        argv = spec.argv(RDS, 1433, 15000)
        assert argv[:4] == ["ssh", "-N", "-L", f"127.0.0.1:15000:{RDS}:1433"]
        assert argv[-1] == "ec2-user@bastion.example.com"
        # It must fail rather than wait for a password nobody can type.
        assert "BatchMode=yes" in argv and "ExitOnForwardFailure=yes" in argv
        assert "-p" not in argv and "-i" not in argv

    def test_ssh_port_and_key(self):
        spec = TunnelSpec(kind="ssh", host="b", port=2222, identity_file="~/.ssh/id_ed25519")
        argv = spec.argv(RDS, 5432, 15000)
        assert argv[argv.index("-p") + 1] == "2222"
        assert not argv[argv.index("-i") + 1].startswith("~")  # expanded
        assert argv[-1] == "b"

    def test_session_manager_forward(self):
        spec = TunnelSpec(kind="ssm", target="i-0abc123456789", profile="work", region="eu-west-1")
        argv = spec.argv(RDS, 1433, 15000)
        assert argv[:5] == ["aws", "ssm", "start-session", "--target", "i-0abc123456789"]
        assert argv[argv.index("--document-name") + 1] == (
            "AWS-StartPortForwardingSessionToRemoteHost"
        )
        assert json.loads(argv[argv.index("--parameters") + 1]) == {
            "host": [RDS],
            "portNumber": ["1433"],
            "localPortNumber": ["15000"],
        }
        assert argv[-4:] == ["--profile", "work", "--region", "eu-west-1"]

    @pytest.mark.parametrize(
        "spec, remote",
        [
            (TunnelSpec(kind="ssh", host="-oProxyCommand=evil"), RDS),  # an option, not a host
            (TunnelSpec(kind="ssh", host="b", user="x -oProxyCommand=evil"), RDS),
            (TunnelSpec(kind="ssh", host="b"), "-oProxyCommand=evil"),
            (TunnelSpec(kind="ssh", host="b"), "db host"),
            (TunnelSpec(kind="ssm", target="i-0abc; rm -rf ~"), RDS),
            (TunnelSpec(kind="ssm", target=""), RDS),
        ],
    )
    def test_values_that_could_become_options_are_refused(self, spec, remote):
        with pytest.raises(ConnectionSetupError):
            spec.argv(remote, 1433, 15000)

    def test_saved_form_keeps_only_what_the_kind_uses(self):
        ssm = TunnelSpec(kind="ssm", target="i-0abc123456789", region="eu-west-1")
        assert ssm.to_dict() == {"kind": "ssm", "target": "i-0abc123456789", "region": "eu-west-1"}
        ssh = TunnelSpec(kind="ssh", host="b", user="u", port=2222)
        assert ssh.to_dict() == {"kind": "ssh", "host": "b", "user": "u", "port": 2222}
        assert TunnelSpec.from_dict(ssh.to_dict()) == ssh
        assert TunnelSpec.from_dict(None) is None
        assert TunnelSpec.from_dict({"kind": "carrier-pigeon"}) is None
        assert ssm.label == "Session Manager via i-0abc123456789"
        assert ssh.label == "SSH via u@b"


_LISTENER = (
    "import socket, sys, time\n"
    "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen()\n"
    "time.sleep(60)\n"
)


@pytest.mark.skipif(sys.platform == "win32", reason="tunnels are stopped by process group (POSIX)")
class TestTunnelProcess:
    @pytest.fixture(autouse=True)
    def _tools_present(self, monkeypatch):
        monkeypatch.setattr(db_tunnel.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def _spec(self, monkeypatch, script: str) -> TunnelSpec:
        monkeypatch.setattr(
            TunnelSpec,
            "argv",
            lambda self, host, port, local: [sys.executable, "-c", script, str(local)],
        )
        return TunnelSpec(kind="ssh", host="bastion")

    def test_start_waits_for_the_port_and_stop_ends_the_process(self, monkeypatch):
        tunnel = Tunnel(self._spec(monkeypatch, _LISTENER), RDS, 1433)
        port = tunnel.start(timeout=10)
        assert tunnel.alive and port == tunnel.local_port
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        proc = tunnel._proc
        tunnel.stop()
        assert not tunnel.alive and proc.poll() is not None
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.5)
        tunnel.stop()  # harmless twice

    @pytest.mark.parametrize(
        "stderr, expected",
        [
            ("Host key verification failed.", "host key isn't trusted yet"),
            ("ec2-user@b: Permission denied (publickey).", "Only key-based logins work"),
            (
                "ssh: Could not resolve hostname b: Name or service not known",
                "couldn't be resolved",
            ),
            ("An error occurred (TargetNotConnected) when calling the StartSession", "SSM agent"),
            ("SessionManagerPlugin is not found. Please refer to", "plugin isn't installed"),
            ("something unexpected", "closed before it was ready. something unexpected"),
        ],
    )
    def test_a_tunnel_that_dies_explains_why(self, monkeypatch, stderr, expected):
        script = f"import sys; sys.stderr.write({stderr!r}); sys.exit(255)"
        tunnel = Tunnel(self._spec(monkeypatch, script), RDS, 1433)
        with pytest.raises(ConnectionSetupError, match=expected):
            tunnel.start(timeout=10)
        assert not tunnel.alive

    def test_a_tunnel_that_never_listens_times_out_and_is_stopped(self, monkeypatch):
        tunnel = Tunnel(self._spec(monkeypatch, "import time; time.sleep(60)"), RDS, 1433)
        with pytest.raises(ConnectionSetupError, match="didn't come up"):
            tunnel.start(timeout=1.0)
        assert not tunnel.alive

    def test_missing_session_manager_plugin_is_reported_before_starting(self, monkeypatch):
        monkeypatch.setattr(
            db_tunnel.shutil, "which", lambda tool: None if "plugin" in tool else "/usr/bin/aws"
        )
        tunnel = Tunnel(TunnelSpec(kind="ssm", target="i-0abc123456789"), RDS, 1433)
        with pytest.raises(ConnectionSetupError, match="plugin isn't installed"):
            tunnel.start()


@pytest.mark.skipif(sys.platform == "win32", reason="tunnels are stopped by process group (POSIX)")
class TestNoTunnelOutlivesTheApp:
    """A tunnel runs in its own session, so nothing stops it when the app
    goes away unless the app does."""

    @pytest.fixture(autouse=True)
    def _tools_present(self, monkeypatch):
        monkeypatch.setattr(db_tunnel.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(
            TunnelSpec,
            "argv",
            lambda self, host, port, local: [sys.executable, "-c", _LISTENER, str(local)],
        )

    def test_stop_all_ends_every_running_tunnel(self):
        tunnels = [Tunnel(TunnelSpec(kind="ssh", host="bastion"), RDS, 1433) for _ in range(2)]
        ports = [t.start(timeout=10) for t in tunnels]
        procs = [t._proc for t in tunnels]

        assert db_tunnel.stop_all() == 2
        assert all(proc.poll() is not None for proc in procs)
        for port in ports:
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.5)
        assert db_tunnel.stop_all() == 0  # nothing left to stop

    def test_a_stopped_or_failed_tunnel_is_forgotten(self, monkeypatch):
        tunnel = Tunnel(TunnelSpec(kind="ssh", host="bastion"), RDS, 1433)
        tunnel.start(timeout=10)
        assert tunnel in db_tunnel._live
        tunnel.stop()
        assert tunnel not in db_tunnel._live

        monkeypatch.setattr(
            TunnelSpec, "argv", lambda self, host, port, local: [sys.executable, "-c", "pass"]
        )
        dead = Tunnel(TunnelSpec(kind="ssh", host="bastion"), RDS, 1433)
        with pytest.raises(ConnectionSetupError):
            dead.start(timeout=10)
        assert dead not in db_tunnel._live

    def test_a_process_that_exits_without_stopping_its_tunnel_takes_it_down(self):
        """The whole thing, for real: a Python process opens a tunnel and
        simply ends. Before the exit hook the listener stayed up."""
        import subprocess

        program = (
            "import sys\n"
            "from polyglot_ai.core import db_tunnel\n"
            "from polyglot_ai.core.db_tunnel import Tunnel, TunnelSpec\n"
            f"LISTENER = {_LISTENER!r}\n"
            "TunnelSpec.argv = lambda self, host, port, local: "
            "[sys.executable, '-c', LISTENER, str(local)]\n"
            "db_tunnel.shutil.which = lambda tool: '/usr/bin/' + tool\n"
            "tunnel = Tunnel(TunnelSpec(kind='ssh', host='bastion'), 'db.example.com', 1433)\n"
            "print(tunnel.start(timeout=10), flush=True)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        port = int(result.stdout.strip())
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.5)


class TestIamToken:
    def test_token_is_made_for_the_real_endpoint(self, monkeypatch):
        seen = {}

        def run(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return f"{RDS}:5432/?Action=connect&X-Amz-Signature=abc\n", 0

        monkeypatch.setattr(aws_cli, "run", run)
        token = iam_auth_token(RDS, 5432, "app_user", "work", "ap-southeast-2")
        assert token.endswith("X-Amz-Signature=abc")
        assert seen["argv"] == [
            "rds", "generate-db-auth-token", "--hostname", RDS, "--port", "5432",
            "--username", "app_user",
        ]  # fmt: skip
        assert seen["kw"] == {"profile": "work", "region": "ap-southeast-2", "json_output": False}

    def test_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            aws_cli, "run", lambda argv, **kw: ("Error: No AWS credentials found", 255)
        )
        with pytest.raises(ConnectionSetupError, match="No AWS credentials found"):
            iam_auth_token(RDS, 5432, "u")
