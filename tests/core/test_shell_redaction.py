"""shell_exec must redact secrets from command output before it reaches
the LLM provider — mirroring the MCP tool-result path. Regression for a
gap where `cat`/`printenv`/`git log -p` output flowed through verbatim.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.ai.tools.shell_tools import shell_exec


class _StubSandbox:
    """Minimal sandbox stub: returns canned (output, returncode)."""

    def __init__(self, output: str, returncode: int = 0) -> None:
        self._output = output
        self._returncode = returncode
        self.calls: list = []

    async def exec_command(self, command, workdir=None, **kwargs):
        self.calls.append((command, workdir, kwargs))
        return self._output, self._returncode


@pytest.mark.asyncio
async def test_shell_exec_redacts_secret_in_output():
    sandbox = _StubSandbox('API_KEY="sk-abc123DEF456ghi789JKL012mno345PQR"')
    result = await shell_exec(sandbox, {"command": "printenv"})
    assert "sk-abc123DEF456ghi789JKL012mno345PQR" not in result
    # Either redaction pass may claim it; both leave a marker.
    assert "REDACTED" in result


@pytest.mark.asyncio
async def test_shell_exec_redacts_bare_key_in_output():
    # A bare key with no credential-header prefix is caught by the
    # content pass (the header pass wouldn't match it).
    sandbox = _StubSandbox("token is sk-abc123DEF456ghi789JKL012mno345PQR here")
    result = await shell_exec(sandbox, {"command": "cat note.txt"})
    assert "sk-abc123DEF456ghi789JKL012mno345PQR" not in result


@pytest.mark.asyncio
async def test_shell_exec_redacts_bearer_token():
    sandbox = _StubSandbox("Authorization: Bearer ghp_1234567890abcdefABCDEF1234567890abcdef")
    result = await shell_exec(sandbox, {"command": "cat headers.txt"})
    assert "ghp_1234567890abcdefABCDEF1234567890abcdef" not in result


@pytest.mark.asyncio
async def test_shell_exec_plain_output_unchanged():
    sandbox = _StubSandbox("total 8\n-rw-r--r-- 1 user user 42 file.txt")
    result = await shell_exec(sandbox, {"command": "ls -la"})
    assert "file.txt" in result
    assert "[SECRET_REDACTED]" not in result


@pytest.mark.asyncio
async def test_shell_exec_preserves_exit_code_line():
    sandbox = _StubSandbox("boom", returncode=1)
    result = await shell_exec(sandbox, {"command": "false"})
    assert "[exit code: 1]" in result


@pytest.mark.asyncio
async def test_shell_exec_empty_output():
    sandbox = _StubSandbox("")
    result = await shell_exec(sandbox, {"command": "true"})
    assert result == "(no output)"
