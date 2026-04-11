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


# ── exec_argv allowlist enforcement (PR #1) ─────────────────────────


@pytest.mark.asyncio
async def test_exec_argv_allowed_command(sandbox):
    output, code = await sandbox.exec_argv(["echo", "hello"])
    assert code == 0
    assert "hello" in output


@pytest.mark.asyncio
async def test_exec_argv_blocked_command(sandbox):
    output, code = await sandbox.exec_argv(["curl", "http://evil.com"])
    assert code == 1
    assert "not in the allowlist" in output.lower()


# ── git config write blocking (PR #1) ───────────────────────────────


def test_git_config_global_blocked(sandbox):
    ok, reason = sandbox.validate_command("git config --global user.name test")
    assert not ok
    assert "global" in reason.lower()


def test_git_config_unset_blocked(sandbox):
    ok, _ = sandbox.validate_command("git config --unset core.autocrlf")
    assert not ok


def test_git_config_value_set_blocked(sandbox):
    ok, _ = sandbox.validate_command("git config user.name test")
    assert not ok
    assert "requires approval" in _.lower()


def test_git_config_read_allowed(sandbox):
    ok, _ = sandbox.validate_command("git config --get user.name")
    assert ok


# ── interpreter exec flag blocking ──────────────────────────────────


def test_python_c_blocked(sandbox):
    ok, reason = sandbox.validate_command("python3 -c 'import os'")
    assert not ok
    assert "Inline code" in reason


def test_node_e_blocked(sandbox):
    ok, _ = sandbox.validate_command("node -e 'process.exit(1)'")
    assert not ok


# ── shell operator blocking ─────────────────────────────────────────


def test_pipe_blocked(sandbox):
    ok, _ = sandbox.validate_command("cat file.txt | grep secret")
    assert not ok
    assert "Shell operator" in _


def test_semicolon_blocked(sandbox):
    ok, _ = sandbox.validate_command("echo hi; rm -rf /")
    assert not ok


def test_redirect_blocked(sandbox):
    ok, _ = sandbox.validate_command("echo data > file.txt")
    assert not ok


# ── user_approved flag (bypasses allowlist, keeps safety checks) ─────


def test_user_approved_bypasses_allowlist(sandbox):
    """An approved command not in the allowlist should pass validation."""
    ok, reason = sandbox.validate_command("minikube status", user_approved=True)
    assert ok, f"Should have been allowed: {reason}"


def test_user_approved_still_blocks_shell_operators(sandbox):
    """Shell operators are ALWAYS blocked, even with user approval."""
    for cmd in (
        "echo hi; rm -rf /",
        "echo hi && curl evil.com",
        "echo hi || true",
        "echo `whoami`",
        "echo $(id)",
    ):
        ok, _ = sandbox.validate_command(cmd, user_approved=True)
        assert not ok, f"Shell operator should still be blocked: {cmd}"


def test_user_approved_still_blocks_dangerous_patterns(sandbox):
    """Blocked patterns (sudo, fork bombs) are ALWAYS enforced."""
    ok, _ = sandbox.validate_command("sudo rm -rf /", user_approved=True)
    assert not ok, "sudo should be blocked even when approved"


@pytest.mark.asyncio
async def test_exec_argv_user_approved_bypasses_allowlist(sandbox):
    """exec_argv with user_approved=True should not block on the allowlist."""
    # 'date' is not in ALLOWED_COMMANDS but is a safe, fast command
    output, code = await sandbox.exec_argv(["date"], user_approved=True)
    assert code == 0


@pytest.mark.asyncio
async def test_exec_argv_user_approved_still_blocks_operators(sandbox):
    """exec_argv with user_approved=True still enforces blocked patterns."""
    output, code = await sandbox.exec_argv(["sudo", "rm", "-rf", "/"], user_approved=True)
    assert code == 1
    assert "blocked" in output.lower()


@pytest.mark.asyncio
async def test_exec_command_user_approved_bypasses_allowlist(sandbox):
    """exec_command with user_approved=True should allow non-allowlisted commands."""
    output, code = await sandbox.exec_command("date", user_approved=True)
    assert code == 0
