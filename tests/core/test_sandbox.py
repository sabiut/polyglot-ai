"""Tests for Sandbox."""

import pytest

from polyglot_ai.core.sandbox import Sandbox


@pytest.fixture
def sandbox(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test.txt").write_text("hello")
    (project / "subdir").mkdir()
    (project / "subdir" / "nested.txt").write_text("nested")
    return Sandbox(project)


def test_validate_path_normal(sandbox):
    path = sandbox.validate_path("test.txt")
    assert path.name == "test.txt"


def test_validate_path_nested(sandbox):
    path = sandbox.validate_path("subdir/nested.txt")
    assert path.name == "nested.txt"


def test_validate_path_traversal_rejected(sandbox):
    with pytest.raises(PermissionError):
        sandbox.validate_path("../../etc/passwd")


def test_validate_path_absolute_outside_rejected(sandbox):
    with pytest.raises(PermissionError):
        sandbox.validate_path("/etc/passwd")


def test_validate_command_allowed(sandbox):
    ok, _ = sandbox.validate_command("ls -la")
    assert ok


def test_validate_command_python(sandbox):
    ok, _ = sandbox.validate_command("python3 script.py")
    assert ok


def test_validate_command_git(sandbox):
    ok, _ = sandbox.validate_command("git status")
    assert ok


def test_validate_command_blocked_sudo(sandbox):
    ok, reason = sandbox.validate_command("sudo rm -rf /")
    assert not ok
    assert "sudo" in reason.lower() or "Blocked" in reason


def test_validate_command_blocked_fork_bomb(sandbox):
    ok, reason = sandbox.validate_command(":(){:|:&};:")
    assert not ok


def test_validate_command_not_allowed(sandbox):
    ok, reason = sandbox.validate_command("curl http://evil.com")
    assert not ok
    assert "allowlist" in reason.lower()


@pytest.mark.asyncio
async def test_exec_command(sandbox):
    output, code = await sandbox.exec_command("echo hello")
    assert code == 0
    assert "hello" in output


@pytest.mark.asyncio
async def test_exec_command_timeout(sandbox):
    # Use tail -f which blocks indefinitely on an allowed command
    output, code = await sandbox.exec_command("tail -f /dev/null", timeout=1)
    assert code == 1
    assert "timed out" in output.lower()
