"""Regression tests for the Arduino panel/dialog stylesheets.

The "Could not parse stylesheet of object QFrame" warning Qt emitted
in production was caused by a recurring f-string vs. plain-string
brace-count mistake:

    self.setStyleSheet(
        f"QFrame {{ background: ...; "      # f-string: {{ → {
        ...
        "border-radius: 6px; }}"            # plain string: }} → }}
    )

The opening uses an f-string so ``{{`` becomes one literal ``{``,
but the closing literal is a plain string, so ``}}`` passes through
as two literal ``}``. The CSS ends up with mismatched braces and
Qt's stylesheet parser silently fails.

These tests construct the affected widgets and verify the final
``styleSheet()`` text Qt sees has balanced braces. Anything caught
here lands in CI before users see the warning spew.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.dialogs.arduino_change_dialog import ArduinoChangeDialog  # noqa: E402
from polyglot_ai.ui.panels.arduino_panel import (  # noqa: E402
    _ReadOnlyCodeView,
    _StepCard,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _balanced_braces(qss: str) -> bool:
    """Return True iff every ``{`` in ``qss`` has a matching ``}``.

    We don't try to validate selector syntax — Qt's QSS parser does
    that. Brace balance is the cheap thing to check here, and it's
    the single cause of every warning we hit.
    """
    return qss.count("{") == qss.count("}")


class TestStylesheetBraceBalance:
    def test_step_card(self, qapp):
        card = _StepCard(1, "Test")
        assert _balanced_braces(card.styleSheet()), (
            f"_StepCard QSS has unbalanced braces:\n{card.styleSheet()}"
        )

    def test_read_only_code_view(self, qapp):
        view = _ReadOnlyCodeView()
        assert _balanced_braces(view.styleSheet()), (
            f"_ReadOnlyCodeView QSS has unbalanced braces:\n{view.styleSheet()}"
        )

    def test_change_dialog_target_box(self, qapp, tmp_path):
        dlg = ArduinoChangeDialog(tmp_path)
        # The target box is a child QFrame — its sheet is the one
        # Qt was warning about ("Could not parse stylesheet of
        # object QFrame(0x...)") on every dialog open.
        assert _balanced_braces(dlg._target_box.styleSheet()), (
            f"_target_box QSS has unbalanced braces:\n{dlg._target_box.styleSheet()}"
        )
        # And the dialog itself, plus a representative tab button,
        # for good measure.
        assert _balanced_braces(dlg.styleSheet())
        assert _balanced_braces(dlg._tab_starter.styleSheet())
