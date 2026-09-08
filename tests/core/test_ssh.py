"""SSH target parsing and command building."""

from __future__ import annotations

from polyglot_ai.core import ssh


class TestTarget:
    def test_command_quotes_and_orders_options(self):
        t = ssh.SshTarget(
            host="10.0.0.5", user="ec2-user", port=2222, identity_file="/k/my key.pem"
        )
        assert t.argv() == ["ssh", "-p", "2222", "-i", "/k/my key.pem", "ec2-user@10.0.0.5"]
        assert t.command() == "ssh -p 2222 -i '/k/my key.pem' ec2-user@10.0.0.5"
        assert t.to_string() == "ec2-user@10.0.0.5:2222"

    def test_defaults_drop_port_and_user(self):
        t = ssh.SshTarget(host="build-box")
        assert t.command() == "ssh build-box"
        assert t.to_string() == "build-box"


class TestParse:
    def test_user_host_port(self):
        t = ssh.parse_target("deploy@example.com:2200")
        assert (t.user, t.host, t.port) == ("deploy", "example.com", 2200)

    def test_bare_host_and_ssh_prefix(self):
        assert ssh.parse_target("ssh myalias").host == "myalias"
        assert ssh.parse_target("  ").__class__ is type(None)

    def test_rejects_option_injection(self):
        assert ssh.parse_target("-oProxyCommand=evil") is None
        assert ssh.valid_host("host name") is False
        assert ssh.valid_host("") is False
        assert ssh.parse_target("host:99999") is None


class TestConfigHosts:
    def test_reads_concrete_aliases_only(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "Host *\n  ServerAliveInterval 30\n"
            "Host prod staging\n  User deploy\n"
            "host dev-?\n"
            "Host prod\n"
        )
        assert ssh.config_hosts(cfg) == ["prod", "staging"]

    def test_missing_config_is_empty(self, tmp_path):
        assert ssh.config_hosts(tmp_path / "nope") == []


def test_ssm_command():
    assert (
        ssh.ssm_command("i-0abc", profile="work", region="eu-west-1")
        == "aws ssm start-session --target i-0abc --profile work --region eu-west-1"
    )
    assert ssh.ssm_command("i-0abc") == "aws ssm start-session --target i-0abc"
