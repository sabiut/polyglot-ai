"""UI tests for the Web Tests tab (Playwright Test Agents view).

Pins the discoverability + dispatch behavior:

* the view renders empty without a project, with all action buttons
  disabled
* setting a project root with no specs/ or tests/ keeps Plan + Heal
  enabled (they're inputs-only) but Generate disabled
* dropping a plan in specs/ enables Generate
* dropping a Playwright spec in tests/ shows it in the list
* clicking Plan / Generate / Heal — once the dialog is accepted — calls
  the parent window's chat_panel.prefill_input with a correctly-formed
  /workflow command
* a regular pytest unit test in tests/ is NOT picked up (only files
  that import playwright count)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from polyglot_ai.ui.panels.web_tests_view import (
    WebTestsView,
    _GeneratorDialog,
    _HealerDialog,
    _PlannerDialog,
    _is_playwright_test,
)


@pytest.fixture
def view(qtbot):
    v = WebTestsView()
    qtbot.addWidget(v)
    v.show()
    return v


# ── Empty / disabled state ─────────────────────────────────────────


def test_no_project_disables_all_actions(view):
    assert view._plan_btn.isEnabled() is False
    assert view._generate_btn.isEnabled() is False
    assert view._heal_btn.isEnabled() is False
    assert view._plans_list.count() == 0
    assert view._tests_list.count() == 0


def test_empty_project_enables_plan_and_heal_but_not_generate(view, tmp_path):
    view.set_project_root(tmp_path)
    assert view._plan_btn.isEnabled() is True
    assert view._heal_btn.isEnabled() is True
    # No specs/ yet → nothing to generate from
    assert view._generate_btn.isEnabled() is False


# ── Discovery ───────────────────────────────────────────────────────


def test_plan_discovered_in_specs_dir(view, tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "guest-checkout.md").write_text("# plan", encoding="utf-8")
    (specs / "basic-operations.md").write_text("# plan", encoding="utf-8")

    view.set_project_root(tmp_path)

    assert view._plans_list.count() == 2
    # Generate is unlocked once at least one plan exists
    assert view._generate_btn.isEnabled() is True


def test_playwright_ts_spec_discovered(view, tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "checkout.spec.ts").write_text(
        "import { test, expect } from '@playwright/test';", encoding="utf-8"
    )
    view.set_project_root(tmp_path)
    assert view._tests_list.count() == 1


def test_pytest_unit_test_is_not_picked_up_as_web_test(view, tmp_path):
    """A plain pytest unit test (no Playwright import) must NOT appear
    in the Web Tests list — that's the Pytest tab's job."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "def test_add(): assert 1 + 1 == 2\n", encoding="utf-8"
    )
    view.set_project_root(tmp_path)
    assert view._tests_list.count() == 0


def test_pytest_playwright_test_is_picked_up(view, tmp_path):
    """A ``test_*.py`` that imports playwright IS a Web Test."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_login.py").write_text(
        "from playwright.sync_api import Page, expect\ndef test_login(page: Page): ...\n",
        encoding="utf-8",
    )
    view.set_project_root(tmp_path)
    assert view._tests_list.count() == 1


def test_refresh_after_dropping_plan_unlocks_generate(view, tmp_path):
    view.set_project_root(tmp_path)
    assert view._generate_btn.isEnabled() is False

    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "plan.md").write_text("# plan", encoding="utf-8")

    view.refresh()
    assert view._generate_btn.isEnabled() is True


def test_is_playwright_test_helper(tmp_path):
    """The heuristic should match common playwright import styles and
    reject ordinary pytest tests."""
    pw = tmp_path / "pw.py"
    pw.write_text("from playwright.sync_api import Page\n", encoding="utf-8")
    unit = tmp_path / "unit.py"
    unit.write_text("def test_x(): pass\n", encoding="utf-8")

    assert _is_playwright_test(pw) is True
    assert _is_playwright_test(unit) is False


# ── Dispatch to chat ────────────────────────────────────────────────


def _install_fake_window(view: WebTestsView, monkeypatch) -> MagicMock:
    """Replace ``view.window()`` with a fake that exposes a mock
    ``chat_panel.prefill_input``. Returns the mock so tests can
    assert what was dispatched.
    """
    fake_chat = MagicMock()
    fake_window = MagicMock()
    fake_window.chat_panel = fake_chat
    fake_window._right_tabs = None  # don't try to switch tabs in tests
    monkeypatch.setattr(view, "window", lambda: fake_window)
    return fake_chat


def test_plan_button_dispatches_workflow_command(view, tmp_path, qtbot, monkeypatch):
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    # Stub the dialog out — instead of opening, fill in values + accept.
    captured = {}

    def fake_exec(self):
        self.url.setText("http://localhost:3000")
        self.scenario.setText("guest checkout flow")
        self.feature.setText("guest-checkout")
        self.language.setCurrentText("typescript")
        captured["values"] = self.values()
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_PlannerDialog, "exec", fake_exec)

    view._on_plan_clicked()

    # Multi-word scenario must be quoted so the /workflow arg parser
    # treats it as one value
    fake_chat.prefill_input.assert_called_once()
    cmd = fake_chat.prefill_input.call_args.args[0]
    assert cmd.startswith("/workflow playwright-planner")
    assert "--url http://localhost:3000" in cmd
    assert '--scenario "guest checkout flow"' in cmd
    assert "--feature guest-checkout" in cmd
    assert "--language typescript" in cmd
    # env_file is empty by default — must NOT clutter the command
    assert "--env_file" not in cmd


def test_plan_with_env_file_appends_env_file_arg(view, tmp_path, qtbot, monkeypatch):
    """When the user sets the credentials file, the dispatched command
    must include ``--env_file <path>`` so the planner sources it before
    exploring the app."""
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        self.url.setText("https://staging.example.com")
        self.scenario.setText("admin dashboard")
        self.feature.setText("admin-dashboard")
        self.language.setCurrentText("typescript")
        self.env_file.setText("env.sh")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_PlannerDialog, "exec", fake_exec)
    view._on_plan_clicked()

    cmd = fake_chat.prefill_input.call_args.args[0]
    assert "--env_file env.sh" in cmd


def test_plan_with_env_file_containing_space_is_quoted(view, tmp_path, qtbot, monkeypatch):
    """Paths with spaces must be wrapped in quotes so the /workflow
    arg parser keeps them as a single token."""
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        self.url.setText("https://staging.example.com")
        self.scenario.setText("admin")
        self.feature.setText("admin")
        self.env_file.setText("My Project/env.sh")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_PlannerDialog, "exec", fake_exec)
    view._on_plan_clicked()

    cmd = fake_chat.prefill_input.call_args.args[0]
    assert '--env_file "My Project/env.sh"' in cmd


def test_generate_button_dispatches_workflow_command(view, tmp_path, qtbot, monkeypatch):
    # Set up a plan so Generate is enabled
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "plan.md").write_text("# plan", encoding="utf-8")

    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        self.url.setText("http://localhost:3000")
        self.language.setCurrentText("python-pytest")
        # plan combo is already populated; just leave the first entry
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_GeneratorDialog, "exec", fake_exec)

    view._on_generate_clicked()

    fake_chat.prefill_input.assert_called_once()
    cmd = fake_chat.prefill_input.call_args.args[0]
    assert cmd.startswith("/workflow playwright-generator")
    assert "--plan " in cmd
    assert "plan.md" in cmd
    assert "--url http://localhost:3000" in cmd
    assert "--language python-pytest" in cmd


def test_heal_button_dispatches_workflow_command(view, tmp_path, qtbot, monkeypatch):
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        self.test.setEditText("tests/checkout.spec.ts")
        self.url.setText("http://localhost:3000")
        self.language.setCurrentText("typescript")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_HealerDialog, "exec", fake_exec)

    view._on_heal_clicked()

    fake_chat.prefill_input.assert_called_once()
    cmd = fake_chat.prefill_input.call_args.args[0]
    assert cmd.startswith("/workflow playwright-healer")
    assert "--test tests/checkout.spec.ts" in cmd
    assert "--url http://localhost:3000" in cmd
    assert "--language typescript" in cmd


def test_cancelled_dialog_dispatches_nothing(view, tmp_path, qtbot, monkeypatch):
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_reject(self):
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_PlannerDialog, "exec", fake_reject)
    view._on_plan_clicked()
    fake_chat.prefill_input.assert_not_called()


def test_double_click_plan_invokes_generator_with_preselect(view, tmp_path, qtbot, monkeypatch):
    """Double-clicking a plan row in the list opens the generator
    dialog with that plan already selected."""
    specs = tmp_path / "specs"
    specs.mkdir()
    plan_path = specs / "checkout.md"
    plan_path.write_text("# plan", encoding="utf-8")

    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    seen_preselect: dict[str, str | None] = {"value": None}

    def fake_exec(self):
        # The dialog's combo should already have our plan selected
        seen_preselect["value"] = self.plan.currentText()
        self.url.setText("http://localhost:3000")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_GeneratorDialog, "exec", fake_exec)

    item = view._plans_list.item(0)
    view._on_plan_double_clicked(item)

    assert seen_preselect["value"] is not None
    assert seen_preselect["value"].endswith("checkout.md")
    fake_chat.prefill_input.assert_called_once()


# ── Quoting helper ──────────────────────────────────────────────────


def test_quote_helper_wraps_multiword_values(view):
    assert view._quote("simple") == "simple"
    assert view._quote("two words") == '"two words"'
    # Already-quoted strings are left alone
    assert view._quote('"already quoted"') == '"already quoted"'
    # Embedded quotes get escaped
    assert view._quote('he said "hi"') == '"he said \\"hi\\""'


# ── Project lifecycle ──────────────────────────────────────────────


def test_set_project_root_none_clears_state(view, tmp_path):
    """Clearing the project (closing the folder) must reset the lists
    and disable every action. A regression that kept stale plans
    visible would let the user dispatch the Generate workflow against
    a path that no longer corresponds to the open project."""
    # Populate first so we can verify the clear actually empties.
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "plan.md").write_text("# plan", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "checkout.spec.ts").write_text(
        "import { test } from '@playwright/test';", encoding="utf-8"
    )

    view.set_project_root(tmp_path)
    assert view._plans_list.count() == 1
    assert view._tests_list.count() == 1
    assert view._plan_btn.isEnabled() is True

    # Now close the project.
    view.set_project_root(None)
    assert view._plans_list.count() == 0
    assert view._tests_list.count() == 0
    assert view._plan_btn.isEnabled() is False
    assert view._generate_btn.isEnabled() is False
    assert view._heal_btn.isEnabled() is False


def test_recursive_discovery_walks_subdirectories(view, tmp_path):
    """``_discover_tests`` uses ``rglob`` — confirm tests nested under
    ``tests/e2e/`` show up in the list, not just top-level ones."""
    tests_dir = tmp_path / "tests" / "e2e" / "checkout"
    tests_dir.mkdir(parents=True)
    (tests_dir / "guest.spec.ts").write_text(
        "import { test } from '@playwright/test';", encoding="utf-8"
    )
    view.set_project_root(tmp_path)
    assert view._tests_list.count() == 1


# ── Healer language auto-detection ──────────────────────────────────


def test_healer_dialog_auto_selects_pytest_for_python_file(view, tmp_path, qtbot, monkeypatch):
    """Double-clicking a ``.py`` test should land in the healer with
    language pre-set to ``python-pytest``. Mismatched language would
    have the healer try to run the wrong test framework."""
    tests = tmp_path / "tests"
    tests.mkdir()
    py_test = tests / "test_login.py"
    py_test.write_text(
        "from playwright.sync_api import Page\ndef test_login(page: Page): ...\n",
        encoding="utf-8",
    )

    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    captured_language: dict[str, str] = {}

    def fake_exec(self):
        # The dialog __init__ should have already switched language
        # because the preselect ends in .py.
        captured_language["value"] = self.language.currentText()
        self.url.setText("http://localhost:3000")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_HealerDialog, "exec", fake_exec)

    item = view._tests_list.item(0)
    view._on_test_double_clicked(item)

    assert captured_language["value"] == "python-pytest"
    fake_chat.prefill_input.assert_called_once()


def test_healer_dialog_defaults_to_typescript_for_spec_ts(view, tmp_path, qtbot, monkeypatch):
    """Counter-test: a ``.spec.ts`` preselect leaves language as
    typescript. Catches a regression where the .py special-case
    flipped the default the wrong way for typescript users."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "checkout.spec.ts").write_text(
        "import { test } from '@playwright/test';", encoding="utf-8"
    )

    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    captured_language: dict[str, str] = {}

    def fake_exec(self):
        captured_language["value"] = self.language.currentText()
        self.url.setText("http://localhost:3000")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_HealerDialog, "exec", fake_exec)
    item = view._tests_list.item(0)
    view._on_test_double_clicked(item)

    assert captured_language["value"] == "typescript"
    fake_chat.prefill_input.assert_called_once()


# ── Empty-field validation ─────────────────────────────────────────


def test_planner_dialog_accepted_with_empty_required_field_notifies(
    view, tmp_path, qtbot, monkeypatch
):
    """If the dialog comes back Accepted but ``url`` is blank, the
    panel must SURFACE the missing field via the hint rather than
    silently doing nothing. Regression test for the "I clicked Plan
    and nothing happened" bug."""
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        # Fill scenario + feature but leave url blank
        self.url.setText("")
        self.scenario.setText("guest checkout")
        self.feature.setText("guest-checkout")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_PlannerDialog, "exec", fake_exec)
    view._on_plan_clicked()

    # No dispatch
    fake_chat.prefill_input.assert_not_called()
    # User saw why
    assert "url" in view._hint.text().lower()


def test_healer_includes_env_file_when_set(view, tmp_path, qtbot, monkeypatch):
    """env_file mirrors the planner — when the user provides it, the
    healer command must carry it through so live-DOM inspection sees
    the authenticated app, not the login wall."""
    fake_chat = _install_fake_window(view, monkeypatch)
    view.set_project_root(tmp_path)

    def fake_exec(self):
        self.test.setEditText("tests/admin.spec.ts")
        self.url.setText("https://staging.example.com")
        self.language.setCurrentText("typescript")
        self.env_file.setText("env.sh")
        from PyQt6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_HealerDialog, "exec", fake_exec)
    view._on_heal_clicked()

    cmd = fake_chat.prefill_input.call_args.args[0]
    assert "--env_file env.sh" in cmd


# ── Tightened Playwright detection ─────────────────────────────────


def test_is_playwright_test_rejects_comment_only_mention(tmp_path):
    """Old heuristic matched the literal substring ``"playwright"``,
    so a file mentioning Playwright in a comment registered as a
    Playwright test. The tightened regex requires a real import."""
    f = tmp_path / "test_unrelated.py"
    f.write_text(
        "# TODO: someday we should port this to Playwright\ndef test_x(): assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    assert _is_playwright_test(f) is False


def test_is_playwright_test_accepts_from_import(tmp_path):
    f = tmp_path / "test_pw.py"
    f.write_text(
        "from playwright.sync_api import Page, expect\ndef test_x(page: Page): ...\n",
        encoding="utf-8",
    )
    assert _is_playwright_test(f) is True


def test_is_playwright_test_accepts_bare_import(tmp_path):
    """``import playwright`` (without dotted attr access) must also count."""
    f = tmp_path / "test_pw.py"
    f.write_text("import playwright\n\ndef test_x(): ...\n", encoding="utf-8")
    assert _is_playwright_test(f) is True
