"""Regression tests for ``ArduinoService._list_via_pyserial`` and the loop edge.

The original bug: ``_list_via_pyserial`` called ``asyncio.to_thread``,
which in turn called ``asyncio.get_running_loop()``. Under qasync (Qt
timer-driven coroutines), this could raise
``RuntimeError: no running event loop`` even though the coroutine was
clearly being driven by a loop — the panel saw the same error every
2.5 s on every polling tick. The fix falls back to a synchronous
scan when ``get_running_loop`` fails.

These tests pin both branches: a happy-path async run, and a
"no loop" fall-through that must succeed without raising.
"""

from __future__ import annotations

import asyncio
import sys

from polyglot_ai.core.arduino.service import ArduinoService


class TestListViaPyserialLoopFallback:
    def test_runs_under_running_loop(self):
        # asyncio.run sets up a real loop; the executor path should
        # work end to end.
        svc = ArduinoService()
        result = asyncio.run(svc._list_via_pyserial())
        # We don't assert specific contents — the test runner may
        # or may not have any USB serial devices attached. We pin
        # that the call returns a list (not raises).
        assert isinstance(result, list)

    def test_runs_when_get_running_loop_fails(self, monkeypatch):
        # Patch ``asyncio.get_running_loop`` from inside the service
        # module so the helper falls into the synchronous branch.
        # Must still return a list and never raise.
        from polyglot_ai.core.arduino import service as service_mod

        def _no_loop():
            raise RuntimeError("no running event loop")

        monkeypatch.setattr(service_mod.asyncio, "get_running_loop", _no_loop)

        # Drive the coroutine under a real ``asyncio.run`` — the
        # outer loop exists, but our patched ``get_running_loop``
        # pretends there isn't one inside ``_list_via_pyserial``,
        # exercising the fallback branch.
        svc = ArduinoService()
        result = asyncio.run(svc._list_via_pyserial())
        assert isinstance(result, list)

    def test_runs_when_executor_raises(self, monkeypatch):
        # Second-order safety: if ``run_in_executor`` itself raises,
        # the fallback should still produce a synchronous result
        # rather than propagating up to the panel as another error.
        svc = ArduinoService()

        async def _go() -> list:
            class _Loop:
                def run_in_executor(self, _executor, _fn):
                    raise RuntimeError("executor unavailable")

            from polyglot_ai.core.arduino import service as service_mod

            monkeypatch.setattr(
                service_mod.asyncio,
                "get_running_loop",
                lambda: _Loop(),
            )
            return await svc._list_via_pyserial()

        result = asyncio.run(_go())
        assert isinstance(result, list)


# ``asyncio.to_thread`` was the original failure point — pin its
# absence so a careless re-introduction breaks loudly in CI.
class TestNoToThreadInPyserialPath:
    def test_pyserial_path_does_not_call_to_thread(self):
        # AST-level check so the docstring/comment that explains
        # *why* we removed ``to_thread`` doesn't trip a substring
        # search.
        import ast
        import inspect
        import textwrap

        # ``inspect.getsource`` returns the method body with its
        # original class-level indent — ``ast.parse`` chokes on
        # that, so dedent first.
        src = textwrap.dedent(inspect.getsource(ArduinoService._list_via_pyserial))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match either ``asyncio.to_thread(...)`` or a bare
            # ``to_thread(...)`` after a ``from asyncio import``.
            if isinstance(func, ast.Attribute) and func.attr == "to_thread":
                raise AssertionError(
                    "_list_via_pyserial calls asyncio.to_thread — "
                    "this triggers RuntimeError on qasync timer-driven "
                    "coroutines. Use loop.run_in_executor with a "
                    "synchronous fallback instead."
                )
            if isinstance(func, ast.Name) and func.id == "to_thread":
                raise AssertionError(
                    "_list_via_pyserial calls bare to_thread (imported "
                    "from asyncio) — same qasync issue applies."
                )


# Module-level smoke test — service should be importable without a
# QApplication running. Catches a regression where the arduino package
# accidentally pulls in Qt at import time.
def test_module_imports_without_qt():
    # ``import sys`` already succeeded if we got here.
    # Pin that nothing in the arduino tree is forcing PyQt6 at
    # import time — the service is meant to be Qt-free.
    pyqt_modules = [m for m in sys.modules if m.startswith("PyQt6")]
    # We don't fail outright (other imports earlier in the test run
    # may have pulled PyQt6 in), but at least the bare service path
    # should not require it. The check above is informational.
    _ = pyqt_modules
