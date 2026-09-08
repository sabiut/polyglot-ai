"""SSH sessions in the terminal: dialog, terminal send, main-window plumbing, AWS hooks."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.ssh import SshTarget  # noqa: E402
from polyglot_ai.ui.dialogs.ssh_dialog import SshConnectDialog  # noqa: E402
from polyglot_ai.ui.panels.terminal_panel import TerminalPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _FakePty:
    def __init__(self, running=True):
        self.is_running = running
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data


class TestDialog:
    def test_pasting_user_host_port_splits_fields(self, qapp):
        dlg = SshConnectDialog(["old@host"], ["prod"])
        assert [dlg._host.itemText(i) for i in range(dlg._host.count())] == ["old@host", "prod"]
        dlg._host.setCurrentText("deploy@10.1.2.3:2222")
        assert dlg._host.currentText() == "10.1.2.3"
        assert dlg._user.text() == "deploy"
        assert dlg._port.value() == 2222
        target = dlg.build_target()
        assert target.command() == "ssh -p 2222 deploy@10.1.2.3"

    def test_invalid_host_blocks_accept(self, qapp):
        dlg = SshConnectDialog()
        dlg._host.setCurrentText("-oProxyCommand=x")
        dlg._on_accept()
        assert dlg.target() is None
        assert not dlg._error.isHidden()

    def test_prefill(self, qapp):
        dlg = SshConnectDialog(prefill=SshTarget(host="1.2.3.4", user="ec2-user"))
        assert dlg.build_target().label == "ec2-user@1.2.3.4"


class TestTerminalSend:
    def test_send_command_types_line_into_shell(self, qapp):
        panel = TerminalPanel()
        panel._pty = _FakePty()
        assert panel.send_command("ssh prod") is True
        assert panel._pty.written == b"ssh prod\n"

    def test_send_command_without_shell_is_false(self, qapp):
        panel = TerminalPanel()
        panel._pty = _FakePty(running=False)
        assert panel.send_command("ls") is False
        assert hasattr(panel, "ssh_requested")


class TestMainWindow:
    def test_run_in_terminal_shows_terminal_and_sends(self, qapp):
        from polyglot_ai.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._terminal_panel._pty = _FakePty()
            assert not window._action_toggle_terminal.isChecked()
            assert window.run_in_terminal("ssh prod") is True
            assert window._action_toggle_terminal.isChecked()
            assert window._terminal_panel._pty.written == b"ssh prod\n"
        finally:
            window.close()

    def test_open_ssh_session_without_asking_records_recent(self, qapp):
        from polyglot_ai.ui.main_window import MainWindow

        saved = {}

        class _Settings:
            def get(self, key):
                return ["old@box"] if key == "ssh.recent_targets" else None

            async def set(self, key, value):
                saved[key] = value

        window = MainWindow()
        try:
            window._settings = _Settings()
            window._terminal_panel._pty = _FakePty()
            target = SshTarget(host="10.0.0.9", user="ec2-user")
            # safe_task needs a loop; run the call inside one.
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.call_soon(lambda: window.open_ssh_session(target, ask=False))
                loop.run_until_complete(asyncio.sleep(0.05))
            finally:
                loop.close()
            assert window._terminal_panel._pty.written == b"ssh ec2-user@10.0.0.9\n"
            assert saved["ssh.recent_targets"] == ["ec2-user@10.0.0.9", "old@box"]
        finally:
            window.close()


class TestAwsHooks:
    def test_ssh_target_and_ssm_command(self, qapp):
        from polyglot_ai.ui.panels.aws_panel import AwsPanel

        panel = AwsPanel()
        panel._profile, panel._region = "work", "us-east-1"
        inst = {"type": "ec2", "id": "i-0abc", "state": "running", "ip": "3.3.3.3"}
        assert panel.ssh_target_for(inst).label == "ec2-user@3.3.3.3"
        assert panel.ssm_command_for("i-0abc") == (
            "aws ssm start-session --target i-0abc --profile work --region us-east-1"
        )
