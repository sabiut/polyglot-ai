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
