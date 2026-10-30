"""AI file tools must operate on exactly the path named — never a symlink target.

The old check ran ``check_no_symlinks_in_path`` on the already-resolved
path, which by construction contains no symlinks, so it never fired. A
cloned repo could ship ``docs/setup.txt -> ../.env`` (read returned the
secret) or ``docs/notes.md -> ../.git/config`` (an approved write of
``docs/notes.md`` rewrote git config).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from polyglot_ai.core.ai.tools import file_tools
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.file_ops import FileOperations
from polyglot_ai.core.sandbox import Sandbox


@pytest.fixture
def project(tmp_path):
    root = tmp_path.resolve()
    (root / ".env").write_text("STRIPE_KEY=sk_live_abc123SECRET\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "docs").mkdir()
    (root / "docs" / "real.md").write_text("hello\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n")
    os.symlink("../.env", root / "docs" / "setup.txt")
    os.symlink("../.git/config", root / "docs" / "notes.md")
    os.symlink(root / ".git", root / "gitdir")  # symlinked directory
    return root


@pytest.fixture
def ops(project):
    f = FileOperations(EventBus())
    f.set_project_root(project)
    return f


def test_read_through_symlinked_file_is_refused(ops):
    with pytest.raises(PermissionError, match="symlink"):
        ops.read("docs/setup.txt")


def test_write_through_symlinked_file_is_refused(ops, project):
    with pytest.raises(PermissionError, match="symlink"):
        ops.write("docs/notes.md", "[core]\n\tfsmonitor = touch /tmp/pwned\n")
    assert (project / ".git" / "config").read_text() == "[core]\n"


def test_symlinked_directory_component_is_refused(ops, project):
    with pytest.raises(PermissionError, match="symlink"):
        ops.write("gitdir/config", "evil")
    with pytest.raises(PermissionError, match="symlink"):
        ops.read("gitdir/config")
    assert (project / ".git" / "config").read_text() == "[core]\n"


def test_dotdot_is_refused(ops):
    with pytest.raises(PermissionError, match=r"'\.\.'"):
        ops.read("src/../docs/real.md")


def test_ordinary_paths_still_work(ops, project):
    assert ops.read("docs/real.md") == "hello\n"
    ops.write("src/new.py", "y = 2\n")
    assert (project / "src" / "new.py").read_text() == "y = 2\n"
    ops.write(str(project / "src" / "abs.py"), "z = 3\n")  # absolute, inside root
    assert (project / "src" / "abs.py").read_text() == "z = 3\n"


def test_absolute_path_outside_root_is_refused(ops, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "x.txt"
    with pytest.raises(PermissionError):
        ops.write(str(outside), "nope")


def test_delete_through_symlinked_directory_is_refused(ops, project):
    with pytest.raises(PermissionError, match="symlink"):
        ops.delete("gitdir/config")
    assert (project / ".git" / "config").exists()


def test_file_read_tool_returns_error_not_secret(ops):
    from polyglot_ai.core.ai.tools import ToolRegistry

    registry = ToolRegistry(Sandbox(ops.project_root), ops)
    result = asyncio.run(registry.execute("file_read", '{"path": "docs/setup.txt"}'))
    assert "sk_live_abc123SECRET" not in result
    assert result.startswith("Error")


def test_file_patch_through_symlink_is_refused(ops, project):
    result = asyncio.run(
        file_tools.file_patch(
            Sandbox(project),
            ops,
            {"path": "docs/notes.md", "old_text": "[core]", "new_text": "[core]\n\tpager = evil"},
        )
    )
    assert result.startswith("Error") and "symlink" in result
    assert (project / ".git" / "config").read_text() == "[core]\n"
