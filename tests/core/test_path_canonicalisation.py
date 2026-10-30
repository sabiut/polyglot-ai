"""Safety rules must judge the canonical project-relative path.

Review findings (both reproduced):
- apply_code_block checked symlinks on the resolved path, so a block for
  ``notes.txt -> .env`` overwrote ``.env``.
- FileOperations passed the raw path to rules expecting a relative one,
  so ``/abs/proj/.github/workflows/x.yml``, ``./.github/workflows/x.yml``
  and ``/abs/proj/.git/hooks/pre-commit`` (code execution on the next
  commit) were all writable.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from polyglot_ai.core.ai.code_applier import apply_code_block
from polyglot_ai.core.ai.tools import file_tools
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.file_ops import FileOperations
from polyglot_ai.core.file_safety import is_sensitive_path, resolve_project_path
from polyglot_ai.core.sandbox import Sandbox


@pytest.fixture
def root(tmp_path):
    r = tmp_path.resolve()
    (r / ".env").write_text("SECRET=1\n")
    (r / ".git" / "hooks").mkdir(parents=True)
    (r / ".git" / "config").write_text("[remote]\n\turl = https://u:tok@host/x\n")
    (r / "src").mkdir()
    os.symlink(".env", r / "notes.txt")
    return r


@pytest.fixture
def ops(root):
    f = FileOperations(EventBus())
    f.set_project_root(root)
    return f


def _ci_spellings(root):
    return [
        str(root / ".github" / "workflows" / "demo.yml"),
        "./.github/workflows/demo.yml",
        str(root / ".git" / "hooks" / "pre-commit"),
        "./.git/hooks/pre-commit",
        str(root / ".husky" / "pre-push"),
    ]


class TestResolver:
    def test_returns_canonical_relative_path(self, root):
        assert resolve_project_path(root, str(root / "src" / "a.py"))[1] == "src/a.py"
        assert resolve_project_path(root, "./src/a.py")[1] == "src/a.py"
        assert resolve_project_path(root, "src//a.py")[1] == "src/a.py"

    @pytest.mark.parametrize("bad", ["notes.txt", "../x", "src/../.env", "/etc/passwd"])
    def test_refuses_symlinks_dotdot_and_outside(self, root, bad):
        with pytest.raises(PermissionError):
            resolve_project_path(root, bad)

    def test_sensitive_check_ignores_dot_prefix(self):
        assert is_sensitive_path("./.github/workflows/x.yml")
        assert is_sensitive_path(".git/hooks/pre-commit")
        assert not is_sensitive_path(".github/CODEOWNERS")


class TestCodeApplier:
    def test_block_through_symlink_does_not_overwrite_target(self, root):
        ok, msg = apply_code_block(root, {"path": "notes.txt", "content": "overwritten content!!"})
        assert not ok and "symlink" in msg
        assert (root / ".env").read_text() == "SECRET=1\n"

    def test_ci_and_hook_paths_refused_in_every_spelling(self, root):
        for path in _ci_spellings(root):
            ok, msg = apply_code_block(root, {"path": path, "content": "on: push\n  jobs: {}"})
            assert not ok, path
        assert not (root / ".git" / "hooks" / "pre-commit").exists()
        assert not (root / ".github").exists()

    def test_normal_block_still_applies(self, root):
        ok, _ = apply_code_block(
            root,
            {"path": str(root / "src" / "a.py"), "content": "def add(a, b):\n    return a + b\n"},
        )
        assert ok and (root / "src" / "a.py").exists()


class TestFileOps:
    def test_write_refuses_ci_and_hook_paths_in_every_spelling(self, ops, root):
        for path in _ci_spellings(root):
            with pytest.raises(PermissionError):
                ops.write(path, "on: push\n")
        assert not (root / ".git" / "hooks" / "pre-commit").exists()

    def test_mkdir_and_delete_use_the_canonical_path(self, ops, root):
        with pytest.raises(PermissionError):
            ops.make_directory(str(root / ".github" / "workflows"))
        with pytest.raises(PermissionError):
            ops.delete(str(root / ".git" / "config"))
        assert (root / ".git" / "config").exists()

    def test_delete_refuses_project_root(self, ops, root):
        for spelling in (".", str(root)):
            with pytest.raises(PermissionError, match="project root"):
                ops.delete(spelling, force_directory=True)


class TestTools:
    def test_file_read_hidden_dir_rule_applies_to_absolute_paths(self, ops, root):
        result = asyncio.run(file_tools.file_read(ops, {"path": str(root / ".git" / "config")}))
        assert result.startswith("Error") and "tok" not in result

    def test_file_write_refuses_absolute_workflow_path(self, ops, root):
        path = str(root / ".github" / "workflows" / "demo.yml")
        result = asyncio.run(
            file_tools.file_write(Sandbox(root), ops, {"path": path, "content": "on: push\n"})
        )
        assert result.startswith("Error")
        assert not (root / ".github").exists()
