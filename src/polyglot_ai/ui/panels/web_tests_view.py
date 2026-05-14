"""Playwright Test Agents view — the "🎭 Web Tests" tab in TestPanel.

Surfaces the three Playwright workflows (planner, generator, healer)
as a first-class UI:

* a top action row with **Plan**, **Generate**, **Heal** buttons that
  open small input dialogs and then dispatch a ``/workflow ...``
  command to the chat panel
* a list of Markdown plans found under ``specs/``; double-click a
  plan to open the Generator dialog preselected for that file
* a list of Playwright test files found under ``tests/``;
  double-click a test to open the Healer dialog preselected for it

Live pass/fail status and per-row buttons are deliberately not
implemented yet — they require wiring into the Playwright runner,
which lives downstream. The list rows are read-only path entries.

The view never edits files directly. Everything routes through the
chat panel's ``prefill_input(...)`` so the user sees the workflow
about to run and can edit args before sending — same pattern that
``TestPanel._send_to_chat`` already uses for pytest "Fix with AI".
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover
    from polyglot_ai.ui.panels.chat_panel import ChatPanel

logger = logging.getLogger(__name__)


# Item role for QListWidget items — store the file path on the item
# so action handlers can pick it up without re-globbing.
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1


# ── Shared styling ─────────────────────────────────────────────────


# Cached path to the painted chevron asset. Generated once on first
# use; QSS ``url(...)`` only accepts file paths, so we have to write
# the pixmap to disk before we can reference it from a stylesheet.
_CHEVRON_PATH: str | None = None


def _chevron_icon_path() -> str:
    """Return a path to a small down-chevron PNG used in QComboBox styling.

    Painted once via QPainter and cached at the module level — the
    same path is reused across every dialog instance for the lifetime
    of the process. Lives in the system temp dir so we don't pollute
    the user's project tree with a generated asset.
    """
    global _CHEVRON_PATH
    if _CHEVRON_PATH and os.path.isfile(_CHEVRON_PATH):
        return _CHEVRON_PATH

    path = os.path.join(tempfile.gettempdir(), "polyglot-ai-chevron.png")

    pm = QPixmap(12, 8)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#aaaaaa"))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Two-segment chevron: ╲╱ pointing down
    painter.drawLine(QPointF(2.5, 2.5), QPointF(6.0, 6.0))
    painter.drawLine(QPointF(6.0, 6.0), QPointF(9.5, 2.5))
    painter.end()
    pm.save(path, "PNG")

    _CHEVRON_PATH = path
    return path


def _input_css() -> str:
    """QSS for the form inputs used in the planner/generator/healer dialogs.

    Centralised here so a tweak to the dropdown chevron, focus colour,
    or padding propagates to every dialog in one place. The QComboBox
    drop-down rules use a painted PNG because Qt's default platform
    arrow disappears as soon as you set any custom background/border
    on QComboBox — without this, the Language field looks identical
    to a QLineEdit.
    """
    chevron = _chevron_icon_path().replace("\\", "/")
    return (
        "QLineEdit, QComboBox { background: #1e1e1e; color: #ddd; "
        "border: 1px solid #444; border-radius: 3px; padding: 5px 8px; "
        "font-size: 12px; }"
        "QLineEdit:focus, QComboBox:focus { border-color: #0e639c; }"
        "QComboBox::drop-down { subcontrol-origin: padding; "
        "subcontrol-position: center right; width: 22px; "
        "border-left: 1px solid #3a3a3a; }"
        "QComboBox::down-arrow { "
        f"image: url({chevron}); "
        "width: 10px; height: 7px; margin-right: 6px; }"
        "QComboBox::down-arrow:on { top: 1px; }"
        "QComboBox QAbstractItemView { background: #252526; color: #ddd; "
        "border: 1px solid #444; selection-background-color: #094771; "
        "selection-color: #ffffff; outline: 0; }"
    )


def _button_box_css() -> str:
    """QSS for the OK/Cancel button box across all three dialogs.

    Targets ``QDialogButtonBox > QPushButton`` so we only restyle
    buttons that live inside a dialog button box — we don't want to
    cascade into the list-row buttons elsewhere on the dialog. The
    primary action uses ``[default="true"]`` because Qt assigns
    ``setDefault(True)`` to the Ok button, which exposes that property
    to QSS even when the button is not currently focused.
    """
    return (
        # All buttons: secondary look by default (Cancel + non-defaults)
        "QDialogButtonBox > QPushButton { "
        "  background: #3c3c3c; "
        "  color: #d8d8d8; "
        "  border: 1px solid #4a4a4a; "
        "  border-radius: 4px; "
        "  padding: 7px 20px; "
        "  font-size: 12px; "
        "  font-weight: 500; "
        "  min-width: 86px; "
        "}"
        "QDialogButtonBox > QPushButton:hover { "
        "  background: #4a4a4a; "
        "  border-color: #5a5a5a; "
        "  color: #ffffff; "
        "}"
        "QDialogButtonBox > QPushButton:pressed { "
        "  background: #2d2d2d; "
        "}"
        "QDialogButtonBox > QPushButton:focus { "
        "  outline: none; "
        "  border-color: #0e639c; "
        "}"
        # Primary action — the default button. Qt sets the ``default``
        # property on whichever button has ``setDefault(True)``, which
        # for QDialogButtonBox.Ok happens automatically.
        'QDialogButtonBox > QPushButton[default="true"] { '
        "  background: #0e639c; "
        "  color: #ffffff; "
        "  border-color: #0e639c; "
        "  font-weight: 600; "
        "}"
        'QDialogButtonBox > QPushButton[default="true"]:hover { '
        "  background: #1177bb; "
        "  border-color: #1a8ae8; "
        "}"
        'QDialogButtonBox > QPushButton[default="true"]:pressed { '
        "  background: #094771; "
        "}"
        'QDialogButtonBox > QPushButton[default="true"]:disabled { '
        "  background: #2c4156; "
        "  color: #8aa8c0; "
        "  border-color: #2c4156; "
        "}"
    )


def _make_button_box(
    ok_label: str,
    parent: QWidget | None = None,
) -> QDialogButtonBox:
    """Build a styled OK/Cancel button box with platform icons stripped.

    Why this exists:

    1. The default ``QDialogButtonBox`` on Linux desktop themes paints
       coloured platform icons next to the labels (red dot for Cancel,
       green check for OK). They look unprofessional inside a dark
       app-specific dialog — we want flat verb labels only.
    2. The primary button text is action-specific ("Plan", "Generate",
       "Heal") rather than generic "OK", which reads as a real
       affordance instead of an acknowledgement.
    3. All three dialogs share the exact same look — extracting this
       removes ~10 lines of duplicated styling per dialog.
    """
    from PyQt6.QtGui import QIcon

    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=parent,
    )
    # Strip the platform icons that the Linux Qt platform plugin adds
    # to the standard buttons — we want labels only.
    for btn in box.buttons():
        btn.setIcon(QIcon())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

    ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
    if ok_btn is not None:
        ok_btn.setText(ok_label)
        # Qt sets default=True on Ok automatically, but be explicit so
        # the QSS ``[default="true"]`` selector fires reliably across
        # platforms.
        ok_btn.setDefault(True)

    cancel_btn = box.button(QDialogButtonBox.StandardButton.Cancel)
    if cancel_btn is not None:
        cancel_btn.setAutoDefault(False)

    box.setStyleSheet(_button_box_css())
    box.setCenterButtons(False)
    return box


# ── Input dialogs ───────────────────────────────────────────────────


class _PlannerDialog(QDialog):
    """Collects url + scenario + feature slug + language + optional env
    file for the planner.

    The env file is optional — only needed when planning a flow that
    requires auth. The planner workflow sources it before exploring
    and uses TEST_USER / TEST_PASS to log in via the live browser.
    Public/unauthenticated flows leave it blank.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎭 Plan Playwright tests")
        self.setModal(True)
        # A touch taller now that we've added the credentials row.
        self.resize(540, 330)
        self.setStyleSheet("QDialog { background: #252526; }")

        form = QFormLayout(self)
        form.setContentsMargins(16, 14, 16, 12)
        form.setSpacing(10)

        label_css = "QLabel { color: #ccc; font-size: 12px; background: transparent; }"
        input_css = _input_css()

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://staging.example.com")
        self.url.setStyleSheet(input_css)

        self.scenario = QLineEdit()
        self.scenario.setPlaceholderText("guest checkout end-to-end")
        self.scenario.setStyleSheet(input_css)

        self.feature = QLineEdit()
        self.feature.setPlaceholderText("guest-checkout")
        self.feature.setStyleSheet(input_css)

        self.language = QComboBox()
        self.language.addItems(["typescript", "python-pytest"])
        self.language.setStyleSheet(input_css)

        # Optional creds file. The planner only consults this if the
        # field is non-empty AND the scenario hits an auth-gated page.
        self.env_file = QLineEdit()
        self.env_file.setPlaceholderText("env.sh  (optional, for auth-gated flows)")
        self.env_file.setToolTip(
            "Path to a gitignored shell file exporting TEST_USER and TEST_PASS.\n"
            "Leave empty for public/unauthenticated flows.\n"
            "Use test/staging credentials only — never production."
        )
        self.env_file.setStyleSheet(input_css)

        for label, widget in (
            ("URL", self.url),
            ("Scenario", self.scenario),
            ("Feature slug", self.feature),
            ("Language", self.language),
            ("Credentials file", self.env_file),
        ):
            lbl = QLabel(label)
            lbl.setStyleSheet(label_css)
            form.addRow(lbl, widget)

        # A small hint row below the form spelling out when this is
        # needed. Spans both columns by adding a single QLabel as a
        # row (no field widget).
        hint = QLabel(
            "Credentials file is optional. Required only when the scenario "
            "needs login — use a gitignored env.sh with test creds."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "color: #888; font-size: 10px; padding: 2px 2px 6px 2px; background: transparent;"
        )
        form.addRow(hint)

        buttons = _make_button_box("Create plan", parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "url": self.url.text().strip(),
            "scenario": self.scenario.text().strip(),
            "feature": self.feature.text().strip(),
            "language": self.language.currentText(),
            "env_file": self.env_file.text().strip(),
        }


class _GeneratorDialog(QDialog):
    """Collects plan path + url + language for the generator."""

    def __init__(
        self,
        plans: list[Path],
        parent: QWidget | None = None,
        preselect: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎭 Generate Playwright tests")
        self.setModal(True)
        self.resize(520, 240)
        self.setStyleSheet("QDialog { background: #252526; }")

        form = QFormLayout(self)
        form.setContentsMargins(16, 14, 16, 12)
        form.setSpacing(10)

        label_css = "QLabel { color: #ccc; font-size: 12px; background: transparent; }"
        input_css = _input_css()

        self.plan = QComboBox()
        for p in plans:
            self.plan.addItem(str(p))
        if preselect is not None:
            idx = self.plan.findText(str(preselect))
            if idx >= 0:
                self.plan.setCurrentIndex(idx)
        self.plan.setStyleSheet(input_css)

        self.url = QLineEdit()
        self.url.setPlaceholderText("http://localhost:3000")
        self.url.setStyleSheet(input_css)

        self.language = QComboBox()
        self.language.addItems(["typescript", "python-pytest"])
        self.language.setStyleSheet(input_css)

        for label, widget in (
            ("Plan", self.plan),
            ("Base URL", self.url),
            ("Language", self.language),
        ):
            lbl = QLabel(label)
            lbl.setStyleSheet(label_css)
            form.addRow(lbl, widget)

        buttons = _make_button_box("Generate tests", parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "plan": self.plan.currentText().strip(),
            "url": self.url.text().strip(),
            "language": self.language.currentText(),
        }


class _HealerDialog(QDialog):
    """Collects test path + url + language + optional env_file for the healer.

    ``env_file`` mirrors the planner — required when the failing test
    exercises an auth-gated flow. Without it, the healer's live-DOM
    inspection hits the login wall instead of the real page and the
    diagnosis is wrong ("element not found" — because the page being
    inspected is the login form, not the failing step's page).
    """

    def __init__(
        self,
        tests: list[Path],
        parent: QWidget | None = None,
        preselect: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎭 Heal a failing test")
        self.setModal(True)
        self.resize(540, 290)
        self.setStyleSheet("QDialog { background: #252526; }")

        form = QFormLayout(self)
        form.setContentsMargins(16, 14, 16, 12)
        form.setSpacing(10)

        label_css = "QLabel { color: #ccc; font-size: 12px; background: transparent; }"
        input_css = _input_css()

        self.test = QComboBox()
        self.test.setEditable(True)
        for t in tests:
            self.test.addItem(str(t))
        if preselect is not None:
            idx = self.test.findText(str(preselect))
            if idx >= 0:
                self.test.setCurrentIndex(idx)
            else:
                self.test.setEditText(str(preselect))
        self.test.setStyleSheet(input_css)

        self.url = QLineEdit()
        self.url.setPlaceholderText("http://localhost:3000")
        self.url.setStyleSheet(input_css)

        self.language = QComboBox()
        self.language.addItems(["typescript", "python-pytest"])
        # If the preselected test ends in .py, default to pytest
        if preselect is not None and str(preselect).endswith(".py"):
            self.language.setCurrentText("python-pytest")
        self.language.setStyleSheet(input_css)

        # Optional creds file — same shape as the planner.
        self.env_file = QLineEdit()
        self.env_file.setPlaceholderText("env.sh  (optional, for auth-gated flows)")
        self.env_file.setToolTip(
            "Path to a gitignored shell file exporting TEST_USER and TEST_PASS.\n"
            "Required when the failing test runs behind login — without it the\n"
            "healer inspects the login page instead of the real failing page.\n"
            "Use test/staging credentials only — never production."
        )
        self.env_file.setStyleSheet(input_css)

        for label, widget in (
            ("Failing test", self.test),
            ("Base URL", self.url),
            ("Language", self.language),
            ("Credentials file", self.env_file),
        ):
            lbl = QLabel(label)
            lbl.setStyleSheet(label_css)
            form.addRow(lbl, widget)

        buttons = _make_button_box("Heal test", parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "test": self.test.currentText().strip(),
            "url": self.url.text().strip(),
            "language": self.language.currentText(),
            "env_file": self.env_file.text().strip(),
        }


# ── Main view ───────────────────────────────────────────────────────


# Test files we recognise as Playwright tests. Keep this list narrow:
# any ``test_*.py`` could be a pytest unit test, so we require the
# ``playwright`` import marker via the heuristic in ``_is_playwright_test``.
_PLAYWRIGHT_SPEC_GLOBS = ("*.spec.ts", "*.spec.js", "test_*.py")


class WebTestsView(QWidget):
    """The 🎭 Web Tests tab content for TestPanel.

    The view is intentionally read-only when no project is open — every
    button is disabled until ``set_project_root`` has been called with a
    real path. This mirrors how ``TestPanel`` (the pytest sibling) gates
    its actions, so the two tabs feel consistent.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._setup_ui()

    # ── Build ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top action row — Plan / Generate / Heal buttons. These open
        # input dialogs and dispatch the matching ``/workflow`` command
        # to chat. Disabled until a project is open.
        action_row = QFrame()
        action_row.setStyleSheet("background: #1f1f1f; border-bottom: 1px solid #333;")
        ar = QHBoxLayout(action_row)
        ar.setContentsMargins(10, 8, 10, 8)
        ar.setSpacing(6)

        self._plan_btn = self._action_button("📝  Plan…", "Explore the app and write a test plan")
        self._plan_btn.clicked.connect(self._on_plan_clicked)
        ar.addWidget(self._plan_btn)

        self._generate_btn = self._action_button(
            "⚙️  Generate…", "Turn a plan into runnable Playwright tests"
        )
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        ar.addWidget(self._generate_btn)

        self._heal_btn = self._action_button("🩺  Heal…", "Diagnose and fix a failing test")
        self._heal_btn.clicked.connect(self._on_heal_clicked)
        ar.addWidget(self._heal_btn)

        ar.addStretch()

        self._refresh_btn = self._action_button("⟳", "Refresh plan and test lists", small=True)
        self._refresh_btn.clicked.connect(self.refresh)
        ar.addWidget(self._refresh_btn)

        layout.addWidget(action_row)

        # Plans section
        plans_header = self._section_header("PLANS  •  specs/")
        layout.addWidget(plans_header)

        self._plans_list = self._make_list_widget()
        self._plans_list.itemDoubleClicked.connect(self._on_plan_double_clicked)
        layout.addWidget(self._plans_list, stretch=1)

        # Tests section
        tests_header = self._section_header("TESTS  •  tests/")
        layout.addWidget(tests_header)

        self._tests_list = self._make_list_widget()
        self._tests_list.itemDoubleClicked.connect(self._on_test_double_clicked)
        layout.addWidget(self._tests_list, stretch=1)

        # Empty-state hint at the bottom (always visible — never noisy)
        self._hint = QLabel(
            "Open a project to discover plans (specs/) and Playwright tests (tests/)."
        )
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet(
            "color: #777; font-size: 11px; padding: 8px 12px; background: transparent;"
        )
        layout.addWidget(self._hint)

        self._set_actions_enabled(False)

    # ── Helpers: small UI bits ──────────────────────────────────────

    def _action_button(self, text: str, tooltip: str, *, small: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if small:
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #aaa; border: none; "
                "padding: 0 8px; font-size: 13px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.08); border-radius: 3px; }"
                "QPushButton:disabled { color: #555; }"
            )
        else:
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                "QPushButton { background: #2d2d30; color: #ddd; border: 1px solid #3f3f46; "
                "border-radius: 3px; padding: 4px 10px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background: #094771; border-color: #0e639c; }"
                "QPushButton:disabled { background: #2a2a2a; color: #555; border-color: #333; }"
            )
        return btn

    def _section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedHeight(22)
        lbl.setStyleSheet(
            "QLabel { color: #888; font-size: 10px; font-weight: 600; "
            "letter-spacing: 0.5px; padding: 4px 10px; "
            "background: #232323; border-top: 1px solid #2a2a2a; "
            "border-bottom: 1px solid #2a2a2a; }"
        )
        return lbl

    def _make_list_widget(self) -> QListWidget:
        lw = QListWidget()
        lw.setStyleSheet(
            "QListWidget { background: #1e1e1e; color: #ddd; border: none; "
            "outline: none; font-size: 12px; }"
            "QListWidget::item { padding: 0; }"
            "QListWidget::item:selected { background: #094771; }"
            "QListWidget::item:hover { background: #2a2d2e; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; }"
        )
        return lw

    # ── Public API ──────────────────────────────────────────────────

    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None
        self.refresh()

    def refresh(self) -> None:
        """Re-scan ``specs/`` and ``tests/`` for plans and Playwright tests."""
        self._plans_list.clear()
        self._tests_list.clear()

        if self._project_root is None or not self._project_root.is_dir():
            self._hint.setText(
                "Open a project to discover plans (specs/) and Playwright tests (tests/)."
            )
            self._hint.show()
            self._set_actions_enabled(False)
            return

        # Plan + Heal don't need pre-existing plans/tests on disk
        # (Plan creates one; Heal can take a typed-in test name), so
        # they enable as soon as a project is open. Generate needs at
        # least one plan to point at, so it gates on ``plans``.
        plans = self._discover_plans()
        tests = self._discover_tests()

        for path in plans:
            self._plans_list.addItem(self._build_plan_item(path))

        for path in tests:
            self._tests_list.addItem(self._build_test_item(path))

        # Hint reflects what was found
        bits = []
        if plans:
            bits.append(f"{len(plans)} plan(s)")
        else:
            bits.append("no plans yet — click Plan… to create one")
        if tests:
            bits.append(f"{len(tests)} Playwright test file(s)")
        else:
            bits.append("no Playwright tests yet")
        self._hint.setText("  •  ".join(bits))
        self._hint.show()

        self._plan_btn.setEnabled(True)
        self._generate_btn.setEnabled(bool(plans))
        self._heal_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)

    # ── Discovery ───────────────────────────────────────────────────

    def _discover_plans(self) -> list[Path]:
        """Markdown plans under ``specs/`` — the convention the planner writes to."""
        if self._project_root is None:
            return []
        specs_dir = self._project_root / "specs"
        if not specs_dir.is_dir():
            return []
        return sorted(specs_dir.glob("*.md"))

    def _discover_tests(self) -> list[Path]:
        """Playwright test files under ``tests/``.

        ``test_*.py`` files are filtered to those that import Playwright —
        plain pytest unit tests live in the same directory but belong to
        the other tab.
        """
        if self._project_root is None:
            return []
        tests_dir = self._project_root / "tests"
        if not tests_dir.is_dir():
            return []

        found: list[Path] = []
        for pattern in _PLAYWRIGHT_SPEC_GLOBS:
            for path in tests_dir.rglob(pattern):
                if not path.is_file():
                    continue
                if path.suffix == ".py" and not _is_playwright_test(path):
                    continue
                found.append(path)
        return sorted(set(found))

    # ── List row builders ───────────────────────────────────────────

    def _build_plan_item(self, path: Path) -> QListWidgetItem:
        item = QListWidgetItem()
        try:
            rel = path.relative_to(self._project_root) if self._project_root else path
        except ValueError:
            rel = path
        item.setText(f"  📄  {rel}")
        item.setData(_PATH_ROLE, str(path))
        item.setToolTip("Double-click to run the Generator against this plan")
        return item

    def _build_test_item(self, path: Path) -> QListWidgetItem:
        item = QListWidgetItem()
        try:
            rel = path.relative_to(self._project_root) if self._project_root else path
        except ValueError:
            rel = path
        # Path-only row — no live status. The Heal dialog accepts any
        # path the user types in, so the row's only job is "let the
        # user point at a test without remembering the directory".
        item.setText(f"  🎭  {rel}")
        item.setData(_PATH_ROLE, str(path))
        item.setToolTip("Double-click to heal this test")
        return item

    # ── Action handlers ─────────────────────────────────────────────

    def _on_plan_clicked(self) -> None:
        dlg = _PlannerDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        missing = [k for k in ("url", "scenario", "feature") if not v.get(k)]
        if missing:
            # The dialog Accepted but a required field came back empty.
            # Surface that visibly instead of silently doing nothing —
            # a no-op after a confirmed dialog reads as a broken button.
            self._notify(f"Missing required field(s): {', '.join(missing)}.")
            return
        cmd = (
            f"/workflow playwright-planner "
            f"--url {self._quote(v['url'])} "
            f"--scenario {self._quote(v['scenario'])} "
            f"--feature {self._quote(v['feature'])} "
            f"--language {v['language']}"
        )
        # Only append --env_file when the user actually set one.
        # Empty default keeps the workflow's "skip auth" branch happy
        # and avoids cluttering the chat input with --env_file "" .
        if v.get("env_file"):
            cmd += f" --env_file {self._quote(v['env_file'])}"
        self._dispatch_to_chat(cmd)

    def _on_generate_clicked(
        self, _checked: bool = False, *, preselect: Path | None = None
    ) -> None:
        plans = self._discover_plans()
        if not plans:
            self._notify("No plans found in specs/ — run Plan first.")
            return
        dlg = _GeneratorDialog(plans, parent=self, preselect=preselect)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        missing = [k for k in ("plan", "url") if not v.get(k)]
        if missing:
            self._notify(f"Missing required field(s): {', '.join(missing)}.")
            return
        cmd = (
            f"/workflow playwright-generator "
            f"--plan {self._quote(v['plan'])} "
            f"--url {self._quote(v['url'])} "
            f"--language {v['language']}"
        )
        self._dispatch_to_chat(cmd)

    def _on_heal_clicked(self, _checked: bool = False, *, preselect: Path | None = None) -> None:
        tests = self._discover_tests()
        dlg = _HealerDialog(tests, parent=self, preselect=preselect)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        missing = [k for k in ("test", "url") if not v.get(k)]
        if missing:
            self._notify(f"Missing required field(s): {', '.join(missing)}.")
            return
        cmd = (
            f"/workflow playwright-healer "
            f"--test {self._quote(v['test'])} "
            f"--url {self._quote(v['url'])} "
            f"--language {v['language']}"
        )
        # Same env_file pattern as the planner — only append when set.
        if v.get("env_file"):
            cmd += f" --env_file {self._quote(v['env_file'])}"
        self._dispatch_to_chat(cmd)

    def _on_plan_double_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(_PATH_ROLE)
        if not path_str:
            return
        self._on_generate_clicked(preselect=Path(path_str))

    def _on_test_double_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(_PATH_ROLE)
        if not path_str:
            return
        self._on_heal_clicked(preselect=Path(path_str))

    # ── Dispatch ────────────────────────────────────────────────────

    def _dispatch_to_chat(self, command: str) -> None:
        """Pre-fill the chat input with the workflow command, then focus chat.

        Same pattern as ``TestPanel._send_to_chat`` — we lean on the
        chat panel's ``prefill_input`` so the user sees exactly what
        will run and can edit args before pressing Enter.

        Any failure on the dispatch path (no chat panel, prefill API
        missing, widget destroyed mid-call) is surfaced via
        ``_notify`` so the user sees *something* — a silent return
        after clicking Plan/Generate/Heal looks identical to "the
        button doesn't work" and is unactionable.
        """
        window = self.window()
        chat: ChatPanel | None = getattr(window, "chat_panel", None)
        if chat is None:
            logger.warning("WebTestsView: chat_panel not available on %r", window)
            self._notify("Chat panel not available — cannot dispatch workflow.")
            return
        try:
            chat.prefill_input(command)
        except AttributeError:
            logger.warning("chat_panel.prefill_input missing; trying private _input")
            try:
                chat._input.setPlainText(command)
                chat._input.setFocus()
            except Exception:
                logger.exception("WebTestsView: failed to populate chat input")
                self._notify(
                    "Could not pre-fill chat input — see logs. "
                    "Copy the command from the log and paste it into chat manually."
                )
                return

        # Switch the right-side tabs to chat so the user sees the
        # prefilled command. The right tab widget lives on main_window.
        # If switching fails, the prefill itself still landed — note
        # that in the hint so the user knows to find the Chat tab.
        right_tabs = getattr(window, "_right_tabs", None)
        if right_tabs is not None:
            try:
                idx = right_tabs.indexOf(chat)
                if idx >= 0:
                    right_tabs.setCurrentIndex(idx)
            except Exception:
                logger.exception("WebTestsView: failed to switch to chat tab")
                self._notify("Workflow command ready — switch to the Chat tab to send it.")
                return

        # Happy path — tell the user where the command landed.
        self._notify("Workflow command sent to Chat — review and press Enter to run.")

    # ── Utilities ───────────────────────────────────────────────────

    @staticmethod
    def _quote(s: str) -> str:
        """Wrap multi-word values in double quotes so the /workflow parser
        keeps them as a single arg. parse_workflow_args splits on
        whitespace so unquoted "guest checkout" would become two args.
        """
        if " " in s and not (s.startswith('"') and s.endswith('"')):
            # Escape any embedded quotes
            inner = s.replace('"', '\\"')
            return f'"{inner}"'
        return s

    def _set_actions_enabled(self, enabled: bool) -> None:
        for btn in (self._plan_btn, self._generate_btn, self._heal_btn, self._refresh_btn):
            btn.setEnabled(enabled)

    def _notify(self, text: str) -> None:
        """Show a transient message via the hint label."""
        self._hint.setText(text)
        self._hint.show()


_PLAYWRIGHT_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+playwright(\.|\s|$)",
    re.MULTILINE | re.IGNORECASE,
)


def _is_playwright_test(path: Path) -> bool:
    """Heuristic: ``test_*.py`` files that actually import Playwright.

    The earlier implementation matched the substring ``"playwright"``
    anywhere in the first 4 KB, which false-positived on files that
    merely mentioned Playwright in a comment (e.g. ``# TODO: port to
    Playwright``). The regex above requires a real ``from playwright``
    or ``import playwright`` on its own line. Generous read window
    (~4 KB) keeps lazy / conditional imports working.

    ``OSError`` is logged at debug — a permission-denied or unreadable
    file shouldn't be silent; the user may have intended to discover
    it. Returns ``False`` so we don't crash discovery.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError as exc:
        logger.debug("Could not read %s for Playwright detection: %s", path, exc)
        return False
    return _PLAYWRIGHT_IMPORT_PATTERN.search(head) is not None
