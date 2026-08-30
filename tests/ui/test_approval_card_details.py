"""Tests for the approval card's Details escalation and danger tint.

Audit findings: ApprovalDialog (rich diff preview) was dead code the
inline card never opened, the card discarded the current_content it
was handed, and sandbox.is_dangerous_command had no callers.
"""

from __future__ import annotations


from polyglot_ai.core.sandbox import Sandbox
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.dialogs.approval_dialog import ApprovalDialog
from polyglot_ai.ui.panels.inline_approval_card import InlineApprovalCard


class TestDangerTint:
    def test_dangerous_shell_command_flagged(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "rm -rf build"}')
        qtbot.addWidget(card)
        assert card._dangerous is True
        assert tc.get("accent_warning") in card._label.styleSheet()

    def test_benign_shell_command_not_flagged(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "ls -la"}')
        qtbot.addWidget(card)
        assert card._dangerous is False

    def test_non_shell_tool_not_flagged(self, qtbot):
        card = InlineApprovalCard("file_write", '{"path": "a.py", "content": "x"}')
        qtbot.addWidget(card)
        assert card._dangerous is False

    def test_static_call_needs_no_instance(self):
        assert Sandbox.is_dangerous_command("pip install requests") is True
        assert Sandbox.is_dangerous_command("") is True  # fail-safe


class TestDetailsEscalation:
    def test_card_keeps_content_for_details(self, qtbot):
        card = InlineApprovalCard(
            "file_write", '{"path": "a.py", "content": "new"}', current_content="old"
        )
        qtbot.addWidget(card)
        assert card._current_content == "old"
        assert card._details_btn.text() == "Details…"

    def test_dialog_decision_resolves_card(self, qtbot, monkeypatch):
        card = InlineApprovalCard("file_write", '{"path": "a.py", "content": "new"}')
        qtbot.addWidget(card)
        decisions = []
        card.decided.connect(decisions.append)

        def fake_exec(dlg):
            dlg._approve()  # user clicked Approve in the dialog
            return 1

        monkeypatch.setattr(ApprovalDialog, "exec", fake_exec)
        card._show_details()

        assert decisions == [True]
        assert card._details_btn.isHidden()

    def test_closing_dialog_leaves_card_pending(self, qtbot, monkeypatch):
        card = InlineApprovalCard("file_write", '{"path": "a.py", "content": "new"}')
        qtbot.addWidget(card)
        decisions = []
        card.decided.connect(decisions.append)

        # Esc / window-X path: exec returns without either button.
        monkeypatch.setattr(ApprovalDialog, "exec", lambda dlg: 0)
        card._show_details()

        assert decisions == []
        assert not card._decided_already

    def test_dialog_reject_resolves_card_rejected(self, qtbot, monkeypatch):
        card = InlineApprovalCard("shell_exec", '{"command": "rm -rf /tmp/x"}')
        qtbot.addWidget(card)
        decisions = []
        card.decided.connect(decisions.append)

        def fake_exec(dlg):
            dlg._reject()
            return 0

        monkeypatch.setattr(ApprovalDialog, "exec", fake_exec)
        card._show_details()

        assert decisions == [False]


class TestApprovalDialogDecidedFlag:
    def test_undecided_by_default(self, qtbot):
        dlg = ApprovalDialog("file_write", '{"path": "a.py", "content": "x"}', "old")
        qtbot.addWidget(dlg)
        assert dlg.explicitly_decided is False

    def test_approve_sets_flag(self, qtbot):
        dlg = ApprovalDialog("file_write", '{"path": "a.py", "content": "x"}', "old")
        qtbot.addWidget(dlg)
        dlg._approve()
        assert dlg.explicitly_decided is True
        assert dlg.approved is True
