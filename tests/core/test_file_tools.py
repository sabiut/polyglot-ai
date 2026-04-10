"""Tests for AI tool wrappers in ``core/ai/tools/file_tools.py``.

Focused on the new ``file_delete``, ``dir_create``, ``dir_delete``
tools and the safety policies they enforce. Pytest's ``asyncio_mode``
is set to ``auto``, so async test functions just work.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.ai.tools.file_tools import dir_create, dir_delete, file_delete
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.file_ops import FileOperations


@pytest.fixture
def file_ops(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ops = FileOperations(EventBus())
    ops.set_project_root(project)
    return ops


# ── file_delete ─────────────────────────────────────────────────────


async def test_file_delete_happy(file_ops):
    file_ops.write("a.txt", "hi")
    result = await file_delete(file_ops, {"path": "a.txt"})
    assert "Deleted file" in result
    assert not (file_ops.project_root / "a.txt").exists()


async def test_file_delete_missing_path_arg(file_ops):
    result = await file_delete(file_ops, {})
    assert result.startswith("Error")
    assert "No file path" in result


async def test_file_delete_missing_file(file_ops):
    result = await file_delete(file_ops, {"path": "ghost.txt"})
    assert result.startswith("Error")
    assert "does not exist" in result


async def test_file_delete_refuses_directory(file_ops):
    file_ops.write("subdir/x.txt", "x")
    result = await file_delete(file_ops, {"path": "subdir"})
    assert result.startswith("Error")
    assert "dir_delete" in result
    assert (file_ops.project_root / "subdir" / "x.txt").exists()


async def test_file_delete_path_traversal_blocked(file_ops):
    result = await file_delete(file_ops, {"path": "../../etc/passwd"})
    assert result.startswith("Error")


async def test_file_delete_sensitive_path_blocked(file_ops):
    workflow = file_ops.project_root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\n")
    result = await file_delete(file_ops, {"path": ".github/workflows/ci.yml"})
    assert result.startswith("Error")
    assert "CI" in result
    assert workflow.exists()


# ── dir_create ──────────────────────────────────────────────────────


async def test_dir_create_happy(file_ops):
    result = await dir_create(file_ops, {"path": "src/new"})
    assert "Created directory" in result
    assert (file_ops.project_root / "src" / "new").is_dir()


async def test_dir_create_idempotent(file_ops):
    await dir_create(file_ops, {"path": "src/new"})
    result = await dir_create(file_ops, {"path": "src/new"})
    assert "Created directory" in result  # second call still succeeds


async def test_dir_create_missing_path(file_ops):
    result = await dir_create(file_ops, {})
    assert result.startswith("Error")


async def test_dir_create_collides_with_file(file_ops):
    file_ops.write("conflict", "x")
    result = await dir_create(file_ops, {"path": "conflict"})
    assert result.startswith("Error")


async def test_dir_create_traversal_blocked(file_ops):
    result = await dir_create(file_ops, {"path": "../escape"})
    assert result.startswith("Error")


async def test_dir_create_sensitive_path_blocked(file_ops):
    result = await dir_create(file_ops, {"path": ".github/workflows/new"})
    assert result.startswith("Error")


# ── dir_delete ──────────────────────────────────────────────────────


async def test_dir_delete_happy(file_ops):
    file_ops.write("d/a.txt", "1")
    file_ops.write("d/sub/b.txt", "2")
    result = await dir_delete(file_ops, {"path": "d", "recursive": True})
    assert "Deleted directory" in result
    assert not (file_ops.project_root / "d").exists()


async def test_dir_delete_requires_recursive_flag(file_ops):
    file_ops.write("d/a.txt", "x")
    result = await dir_delete(file_ops, {"path": "d"})
    assert result.startswith("Error")
    assert "recursive" in result
    # Directory must still be there.
    assert (file_ops.project_root / "d" / "a.txt").exists()


async def test_dir_delete_recursive_false_also_blocked(file_ops):
    file_ops.write("d/a.txt", "x")
    result = await dir_delete(file_ops, {"path": "d", "recursive": False})
    assert result.startswith("Error")


async def test_dir_delete_refuses_file(file_ops):
    file_ops.write("not_a_dir.txt", "x")
    result = await dir_delete(file_ops, {"path": "not_a_dir.txt", "recursive": True})
    assert result.startswith("Error")
    assert "file_delete" in result
    assert (file_ops.project_root / "not_a_dir.txt").exists()


async def test_dir_delete_missing(file_ops):
    result = await dir_delete(file_ops, {"path": "ghost", "recursive": True})
    assert result.startswith("Error")
    assert "does not exist" in result


async def test_dir_delete_project_root_blocked(file_ops):
    result = await dir_delete(file_ops, {"path": ".", "recursive": True})
    assert result.startswith("Error")
    assert "project root" in result.lower()


async def test_dir_delete_traversal_blocked(file_ops):
    result = await dir_delete(file_ops, {"path": "../escape", "recursive": True})
    assert result.startswith("Error")


async def test_dir_delete_sensitive_path_blocked(file_ops):
    workflow_dir = file_ops.project_root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text("name: CI\n")
    result = await dir_delete(file_ops, {"path": ".github/workflows", "recursive": True})
    assert result.startswith("Error")
    assert (workflow_dir / "ci.yml").exists()


# ── Approval policy ─────────────────────────────────────────────────


def test_new_tools_are_auto_approved():
    """File / directory mutation tools are auto-approved.

    The chat contract is that the model must ask the user in plain
    text before calling any of these and only proceed after explicit
    consent — see the system prompt's MUTATION TOOLS rules. The
    UI-level approval gate was removed in favour of pure
    conversational consent (Claude-style). Pinning this prevents an
    accidental refactor from re-introducing the approval popup, which
    would silently change the UX.

    ``shell_exec`` and ``git_commit`` deliberately stay on approval
    because they can do truly arbitrary things (network, git push,
    history rewrites) and one explicit click is worth the friction.
    """
    from polyglot_ai.core.ai.tools.definitions import AUTO_APPROVE, REQUIRES_APPROVAL

    # dir_create is low-risk and stays auto-approved
    assert "dir_create" in AUTO_APPROVE
    assert "dir_create" not in REQUIRES_APPROVAL
    # File mutations require explicit UI approval (security hardening)
    for name in ("file_write", "file_patch", "file_delete", "dir_delete"):
        assert name in REQUIRES_APPROVAL, f"{name} must require approval"
        assert name not in AUTO_APPROVE, f"{name} must NOT be auto-approved"
    # Sensitive tools that stay on approval — pinned so they don't
    # silently get auto-approved by the same kind of refactor.
    for name in ("shell_exec", "git_commit"):
        assert name in REQUIRES_APPROVAL, f"{name} must still require approval"
        assert name not in AUTO_APPROVE, f"{name} must NOT be auto-approved"


def test_new_tools_have_definitions():
    """Tool definitions must include the new tools so the model can call them."""
    from polyglot_ai.core.ai.tools.definitions import TOOL_DEFINITIONS

    names = {entry["function"]["name"] for entry in TOOL_DEFINITIONS}
    assert {"file_delete", "dir_create", "dir_delete"} <= names
