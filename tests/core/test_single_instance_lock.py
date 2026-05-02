"""Regression tests for the single-instance lock.

Pin two things:

- ``QLockFile.setStaleLockTime(0)`` is called *before* ``tryLock``.
  Without it, a crashed previous run leaves a lock file with a
  dead PID and every future launch silently fails to start.
- The ``app.py`` source still contains both the call and an
  explanatory comment so a future cleanup PR doesn't quietly
  remove either.

We don't run ``app.main`` itself — that requires a display, real
services, and the full Qt event loop. Source-level inspection is
the right granularity: the bug we're guarding against is a one-
line regression that AST analysis can detect.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent.parent / "src/polyglot_ai/app.py"


class TestStaleLockTimeIsConfigured:
    def _source(self) -> str:
        return APP_PY.read_text(encoding="utf-8")

    def test_set_stale_lock_time_call_is_present(self):
        # The literal call. Catches a removal but not reordering.
        source = self._source()
        assert "setStaleLockTime(0)" in source, (
            "setStaleLockTime(0) must be called on the QLockFile so "
            "a crashed previous run can't permanently lock users out."
        )

    def test_set_stale_lock_time_runs_before_try_lock(self):
        # AST-level check: in the function that constructs the
        # QLockFile, the setStaleLockTime call must precede the
        # tryLock call. Catches "QLockFile(...); lock.tryLock(...);
        # lock.setStaleLockTime(0)" which would be a no-op (the
        # lock attempt has already happened).
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "main":
                continue
            calls = [
                ast.unparse(c)
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr in {"setStaleLockTime", "tryLock"}
            ]
            stale_idx = next((i for i, t in enumerate(calls) if "setStaleLockTime" in t), None)
            try_idx = next((i for i, t in enumerate(calls) if "tryLock" in t), None)
            assert stale_idx is not None, "setStaleLockTime call missing in main()"
            assert try_idx is not None, "tryLock call missing in main()"
            assert stale_idx < try_idx, (
                "setStaleLockTime must be called before tryLock — "
                "otherwise the stale-PID check happens after the "
                "lock attempt has already failed."
            )
            return
        raise AssertionError("Couldn't find main() in app.py")

    def test_explanatory_comment_is_present(self):
        # The fix is a single line; the comment is what stops a
        # future cleanup PR from "tidying up" the apparent no-op
        # call. If someone wants to remove the comment they need
        # to update this test, which forces them to read the
        # context first.
        source = self._source()
        assert "stale lock" in source.lower(), (
            "Keep the comment explaining setStaleLockTime — it's the "
            "only way a future contributor learns why we set it."
        )
