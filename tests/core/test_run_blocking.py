"""Tests for ``run_blocking`` — the defensive replacement for ``asyncio.to_thread``.

The bug it fixes: under qasync, Qt-click → safe_task → coroutine
chains can land inside ``asyncio.to_thread`` with no running event
loop visible to ``asyncio.get_running_loop()``. Bare ``to_thread``
raises ``RuntimeError: no running event loop``; users see "Installer
crashed: no running event loop" or 2.5 s board-detector error spam.

``run_blocking`` falls back to a real ``threading.Thread`` + Qt
event-pump in that case so the call still completes.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core.async_utils import run_blocking  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _slow_add(a: int, b: int) -> int:
    """Small CPU-bound payload used by the tests below."""
    time.sleep(0.01)
    return a + b


class TestRunBlockingHappyPath:
    """The standard async path must keep working — we only added a fallback."""

    def test_runs_under_normal_loop(self, qapp):
        result = asyncio.run(run_blocking(_slow_add, 2, 3))
        assert result == 5

    def test_propagates_exceptions(self, qapp):
        def _explode():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            asyncio.run(run_blocking(_explode))


class TestRunBlockingFallback:
    """When ``get_running_loop`` raises, fall back to a real thread."""

    def test_fallback_when_get_running_loop_fails(self, qapp):
        from polyglot_ai.core import async_utils

        # Patch get_running_loop on the module so the helper falls
        # into the thread-pumping branch. ``asyncio.run`` provides
        # the outer loop the test itself runs under, but the
        # internal call sees the patched RuntimeError.

        def _no_loop():
            raise RuntimeError("no running event loop")

        with patch.object(async_utils.asyncio, "get_running_loop", _no_loop):
            # ``asyncio.run`` requires SOME loop, so spin up the
            # fallback by calling the sync helper directly. The
            # fallback doesn't await anything — it threads + pumps.
            result = async_utils._run_in_thread_pumping_qt(_slow_add, 4, 5)
        assert result == 9

    def test_fallback_propagates_exceptions(self, qapp):
        from polyglot_ai.core.async_utils import _run_in_thread_pumping_qt

        def _explode():
            raise OSError("disk gone")

        with pytest.raises(OSError, match="disk gone"):
            _run_in_thread_pumping_qt(_explode)

    def test_fallback_does_not_drop_return_value_on_subclass_exception(self, qapp):
        # ``_run_in_thread_pumping_qt`` catches BaseException to
        # forward KeyboardInterrupt etc. — pin that the *value*
        # path still returns cleanly when no exception happens.
        from polyglot_ai.core.async_utils import _run_in_thread_pumping_qt

        result = _run_in_thread_pumping_qt(lambda: "ok")
        assert result == "ok"


class TestNoToThreadInTouchedCallSites:
    """Once we've replaced a to_thread call with run_blocking, regress
    if someone reverts it. AST-level so a docstring or comment that
    *mentions* to_thread doesn't trip the check."""

    def _ast_calls_to_thread(self, src: str) -> bool:
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == "to_thread":
                    return True
                if isinstance(f, ast.Name) and f.id == "to_thread":
                    return True
        return False

    def test_dependency_dialog_run_install_all_does_not_call_to_thread(self):
        import inspect

        from polyglot_ai.ui.dialogs.dependency_dialog import DependencyDialog

        src = inspect.getsource(DependencyDialog._run_install_all)
        assert not self._ast_calls_to_thread(src), (
            "_run_install_all calls asyncio.to_thread — fails under "
            "qasync from a Qt-click chain. Use run_blocking instead."
        )

    def test_dependency_dialog_run_uv_install_does_not_call_to_thread(self):
        import inspect

        from polyglot_ai.ui.dialogs.dependency_dialog import DependencyDialog

        src = inspect.getsource(DependencyDialog._run_uv_install)
        assert not self._ast_calls_to_thread(src), (
            "_run_uv_install calls asyncio.to_thread — same qasync issue."
        )

    def test_arduino_circuitpython_upload_does_not_call_to_thread(self):
        import inspect

        from polyglot_ai.core.arduino.service import ArduinoService

        src = inspect.getsource(ArduinoService.upload_circuitpython)
        assert not self._ast_calls_to_thread(src), (
            "upload_circuitpython calls asyncio.to_thread — same qasync issue."
        )
