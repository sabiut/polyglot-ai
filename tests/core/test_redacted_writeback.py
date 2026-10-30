"""A redacted read followed by a write must not replace real secrets on disk.

file_read shows the model ``[SECRET_REDACTED]`` in place of keys. Writing
that content back used to overwrite the real key with the placeholder
(reproduced in review: the original survived only in .polyglot-backups).
"""

from __future__ import annotations

import asyncio

import pytest

from polyglot_ai.core.ai.tools import file_tools
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.file_ops import FileOperations
from polyglot_ai.core.sandbox import Sandbox

SECRET_FILE = 'API_KEY = "sk-' + "a" * 30 + '"\nDEBUG = False\n'


@pytest.fixture
def env(tmp_path):
    root = tmp_path.resolve()
    (root / "settings.py").write_text(SECRET_FILE)
    ops = FileOperations(EventBus())
    ops.set_project_root(root)
    return root, Sandbox(root), ops


def _run(coro):
    return asyncio.run(coro)


def test_read_then_write_back_is_refused(env):
    root, sandbox, ops = env
    seen = _run(file_tools.file_read(ops, {"path": "settings.py"}))
    assert "[SECRET_REDACTED]" in seen and "sk-aaaa" not in seen
    result = _run(
        file_tools.file_write(
            sandbox, ops, {"path": "settings.py", "content": seen.replace("False", "True")}
        )
    )
    assert result.startswith("Error") and "real secrets" in result
    assert (root / "settings.py").read_text() == SECRET_FILE


def test_patch_that_avoids_the_secret_line_works(env):
    root, sandbox, ops = env
    result = _run(
        file_tools.file_patch(
            sandbox,
            ops,
            {"path": "settings.py", "old_text": "DEBUG = False", "new_text": "DEBUG = True"},
        )
    )
    assert result.startswith("Patched")
    assert (root / "settings.py").read_text() == SECRET_FILE.replace("False", "True")


def test_patch_introducing_a_placeholder_is_refused(env):
    root, sandbox, ops = env
    result = _run(
        file_tools.file_patch(
            sandbox,
            ops,
            {
                "path": "settings.py",
                "old_text": "DEBUG = False",
                "new_text": 'TOKEN = "[SECRET_REDACTED]"',
            },
        )
    )
    assert result.startswith("Error") and "real secrets" in result


def test_patch_with_placeholder_in_old_text_explains_the_miss(env):
    _root, sandbox, ops = env
    result = _run(
        file_tools.file_patch(
            sandbox,
            ops,
            {
                "path": "settings.py",
                "old_text": "API_KEY = [SECRET_REDACTED]",
                "new_text": "API_KEY = [SECRET_REDACTED]",
            },
        )
    )
    assert "avoids the secret lines" in result


def test_files_that_already_contain_the_placeholder_text_stay_editable(env):
    root, sandbox, ops = env
    src = 'PLACEHOLDER = "[SECRET_REDACTED]"\nX = 1\n'
    (root / "redact.py").write_text(src)
    result = _run(
        file_tools.file_write(
            sandbox, ops, {"path": "redact.py", "content": src.replace("X = 1", "X = 2")}
        )
    )
    assert result.startswith("Successfully")


def test_new_file_seeded_with_placeholders_is_refused(env):
    """Placeholder text in a brand-new file almost always means copied redacted
    content (e.g. duplicating a config); refusing is the safe call."""
    root, sandbox, ops = env
    result = _run(
        file_tools.file_write(
            sandbox, ops, {"path": "copy_of_settings.py", "content": "KEY = [SECRET_REDACTED]\n"}
        )
    )
    assert result.startswith("Error")
    assert not (root / "copy_of_settings.py").exists()
