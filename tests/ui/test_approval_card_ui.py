"""The inline approval card's redesigned states and the tool-activity line."""

from __future__ import annotations

from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels.chat_panel import format_tool_activity
from polyglot_ai.ui.panels.inline_approval_card import InlineApprovalCard


class TestApprovalCard:
    def test_pending_card_shows_title_detail_and_buttons(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "ls -la"}')
        qtbot.addWidget(card)
        assert card._label.text() == "Run command"
        assert card._detail.text() == "ls -la"
        assert card._detail.isVisibleTo(card)
        assert card._hint.text() == "Needs your approval."
        for btn in (card._details_btn, card._reject_btn, card._approve_btn):
            assert not btn.isHidden()

    def test_dangerous_card_warns_in_hint_and_title(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "rm -rf build"}')
        qtbot.addWidget(card)
        assert "destructive" in card._hint.text()
        assert tc.get("accent_warning") in card._label.styleSheet()
        assert tc.get("accent_warning") in card.styleSheet()  # edge colour

    def test_file_tool_shows_path_detail(self, qtbot):
        card = InlineApprovalCard("file_write", '{"path": "src/app.py", "content": "x"}')
        qtbot.addWidget(card)
        assert card._label.text() == "Write file"
        assert card._detail.text() == "src/app.py"

    def test_approve_collapses_to_record(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "pwd"}')
        qtbot.addWidget(card)
        decisions = []
        card.decided.connect(decisions.append)
        card._approve_btn.click()
        assert decisions == [True]
        assert card._label.text().startswith("✓ Approved — Run command")
        assert card._hint.isHidden()
        assert card._approve_btn.isHidden() and card._reject_btn.isHidden()
        assert card._detail.text() == "pwd"  # the record keeps what was approved
        assert tc.get("accent_success_muted") in card.styleSheet()

    def test_reject_collapses_to_record(self, qtbot):
        card = InlineApprovalCard("file_delete", '{"path": "a.py"}')
        qtbot.addWidget(card)
        card._reject_btn.click()
        assert card._label.text().startswith("✗ Rejected — Delete file")
        assert tc.get("accent_error") in card.styleSheet()

    def test_decision_is_emitted_only_once(self, qtbot):
        card = InlineApprovalCard("shell_exec", '{"command": "pwd"}')
        qtbot.addWidget(card)
        decisions = []
        card.decided.connect(decisions.append)
        card._approve_btn.click()
        card.force_decision(False)
        assert decisions == [True]


class TestToolActivityLine:
    def test_success_includes_detail(self):
        html, failed = format_tool_activity("Ran command", '{"command": "ls -la"}', "total 0")
        assert not failed
        assert html.startswith("✓ Ran command")
        assert "ls -la" in html

    def test_error_result_is_marked_failed(self):
        html, failed = format_tool_activity(
            "Ran command", '{"command": "cat nope"}', "Error: No such file"
        )
        assert failed
        assert html.startswith("✗ Ran command — failed")

    def test_detail_is_escaped_and_elided(self):
        long_cmd = "echo " + "<b>" * 60
        html, _ = format_tool_activity("Ran command", f'{{"command": "{long_cmd}"}}', "")
        assert "<b>" not in html.split("</span>")[0].split(">")[-1]
        assert "&lt;b&gt;" in html
        assert "…" in html

    def test_bad_arguments_fall_back_to_label_only(self):
        html, failed = format_tool_activity("Read file", "{not json", "contents")
        assert html == "✓ Read file"
        assert not failed
