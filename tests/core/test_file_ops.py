"""Tests for FileOperations."""

import pytest

from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.file_ops import FileOperations


@pytest.fixture
def file_ops(tmp_path):
    bus = EventBus()
    ops = FileOperations(bus)
    project = tmp_path / "project"
    project.mkdir()
    (project / "test.txt").write_text("hello world")
    (project / "subdir").mkdir()
    (project / "subdir" / "nested.py").write_text("print('hi')")
    ops.set_project_root(project)
    return ops


def test_read_file(file_ops):
    content = file_ops.read("test.txt")
    assert content == "hello world"


def test_read_nested(file_ops):
    content = file_ops.read("subdir/nested.py")
    assert "print" in content


def test_read_nonexistent(file_ops):
    with pytest.raises(FileNotFoundError):
        file_ops.read("nonexistent.txt")


def test_write_file(file_ops):
    file_ops.write("new.txt", "new content")
    assert file_ops.read("new.txt") == "new content"


def test_write_creates_dirs(file_ops):
    file_ops.write("deep/nested/file.txt", "deep content")
    assert file_ops.read("deep/nested/file.txt") == "deep content"


def test_path_traversal_blocked(file_ops):
    with pytest.raises(PermissionError):
        file_ops.read("../../etc/passwd")


def test_list_dir(file_ops):
    tree = file_ops.list_dir()
    assert "test.txt" in tree
    assert "subdir" in tree


def test_delete_file(file_ops):
    file_ops.write("to_delete.txt", "bye")
    file_ops.delete("to_delete.txt")
    with pytest.raises(FileNotFoundError):
        file_ops.read("to_delete.txt")


# ── delete() safety + new make_directory ────────────────────────────


def test_delete_missing_file_raises(file_ops):
    with pytest.raises(FileNotFoundError):
        file_ops.delete("never_existed.txt")


def test_delete_directory_requires_force(file_ops):
    file_ops.write("d/keep.txt", "x")
    with pytest.raises(PermissionError, match="recursively"):
        file_ops.delete("d")
    # Sanity: file still there.
    assert file_ops.read("d/keep.txt") == "x"


def test_delete_directory_force(file_ops):
    file_ops.write("d/a.txt", "x")
    file_ops.write("d/b.txt", "y")
    file_ops.delete("d", force_directory=True)
    with pytest.raises(FileNotFoundError):
        file_ops.read("d/a.txt")


def test_delete_project_root_blocked(file_ops):
    with pytest.raises(PermissionError, match="project root"):
        file_ops.delete(".", force_directory=True)


def test_delete_path_traversal_blocked(file_ops):
    with pytest.raises(PermissionError):
        file_ops.delete("../../etc/passwd")


def test_delete_sensitive_path_blocked(file_ops):
    # Seed directly — file_ops.write itself blocks sensitive paths.
    workflow = file_ops.project_root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\n")
    with pytest.raises(PermissionError, match="CI"):
        file_ops.delete(".github/workflows/ci.yml")


def test_delete_symlink_blocked(file_ops):
    """A symlink inside the project root is itself a forbidden delete target."""
    import os

    real = file_ops.project_root / "real.txt"
    real.write_text("real content")
    link = file_ops.project_root / "link.txt"
    os.symlink(real, link)
    with pytest.raises(PermissionError, match="symlink"):
        file_ops.delete("link.txt")
    # Real file untouched.
    assert real.exists()


def test_make_directory_creates_parents(file_ops):
    file_ops.make_directory("a/b/c")
    assert (file_ops.project_root / "a" / "b" / "c").is_dir()


def test_make_directory_idempotent(file_ops):
    file_ops.make_directory("dir")
    file_ops.make_directory("dir")  # second call must not raise
    assert (file_ops.project_root / "dir").is_dir()


def test_make_directory_existing_file_raises(file_ops):
    file_ops.write("conflict", "this is a file")
    with pytest.raises(FileExistsError):
        file_ops.make_directory("conflict")


def test_make_directory_sensitive_path_blocked(file_ops):
    with pytest.raises(PermissionError, match="CI"):
        file_ops.make_directory(".github/workflows/new")


def test_make_directory_path_traversal_blocked(file_ops):
    with pytest.raises(PermissionError):
        file_ops.make_directory("../escape")
