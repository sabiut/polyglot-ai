"""The "Change project" modal for the Arduino panel.

Two tabs at the top:

- **Pick a starter** — the existing tile grid plus a language
  filter so the user can browse C++ / MicroPython / CircuitPython
  options without pre-committing to one.
- **Start blank** — a small form (project name + language) that
  scaffolds an empty sketch with the right boilerplate.

The dialog itself doesn't write any files. It computes a
``ChangeResult`` that the caller (the panel) materialises. Keeping
side effects out of the dialog makes it cheap to test and means a
"Cancel" click can never leave half-created files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.arduino.boards import Language
from polyglot_ai.core.arduino.project import (
    DetectedProject,
    detect_in,
    safe_folder_name,
)
from polyglot_ai.core.arduino.starters import (
    Starter,
    list_starters,
    starter_destination,
)
from polyglot_ai.ui import theme_colors as tc

# Friendly labels for the language chip / detection result.
_LANG_LABEL: dict[Language, str] = {
    Language.CPP: "C++",
    Language.MICROPYTHON: "Python (MicroPython)",
    Language.CIRCUITPYTHON: "Python (CircuitPython)",
}


@dataclass(frozen=True)
class ChangeResult:
    """What the dialog returns to the panel.

    Exactly one of ``starter`` / ``blank_*`` / ``existing`` is
    populated; the others are ``None``. The panel decides what to
    do with the choice (copy starter files, scaffold a blank, or
    just adopt an already-on-disk project).

    ``target_dir`` is meaningful only for the starter and blank
    paths — the existing-project path uses ``existing.project_dir``
    directly, since the user explicitly picked the folder it lives
    in.
    """

    target_dir: Path
    starter: Starter | None = None
    blank_name: str | None = None
    blank_language: Language | None = None
    existing: DetectedProject | None = None


class ArduinoChangeDialog(QDialog):
    """Modal that asks the user to choose how to swap the project."""

    def __init__(
        self,
        default_target: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change project")
        self.setModal(True)
        self.resize(720, 560)

        self._default_target = default_target
        self._target_dir = default_target
        self._chosen_starter: Starter | None = None
        self._chosen_existing: DetectedProject | None = None
        self._result: ChangeResult | None = None

        self._build()
        self._update_ok_state()
        self._refresh_preview()

    # ── Public ─────────────────────────────────────────────────────

    @property
    def result_value(self) -> ChangeResult | None:
        """Available after ``exec()`` returns ``Accepted``."""
        return self._result

    # ── UI ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {tc.get('bg_base')}; }} "
            f"QLabel {{ color: {tc.get('text_primary')}; "
            f"font-size: {tc.FONT_BASE}px; background: transparent; }} "
            f"QLineEdit {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; "
            "border-radius: 4px; padding: 6px 8px; "
            f"font-size: {tc.FONT_BASE}px; }} "
            f"QComboBox {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_input')}; "
            "border-radius: 4px; padding: 6px 8px; "
            f"font-size: {tc.FONT_BASE}px; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        # Tab strip — two big buttons that swap the QStackedWidget.
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_starter = self._tab_button("📦  Pick a starter")
        self._tab_blank = self._tab_button("📄  Start blank")
        self._tab_existing = self._tab_button("📂  Open existing")
        self._tab_group.addButton(self._tab_starter, 0)
        self._tab_group.addButton(self._tab_blank, 1)
        self._tab_group.addButton(self._tab_existing, 2)
        self._tab_starter.setChecked(True)
        self._tab_starter.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._tab_blank.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._tab_existing.clicked.connect(lambda: self._stack.setCurrentIndex(2))
        tabs.addWidget(self._tab_starter)
        tabs.addWidget(self._tab_blank)
        tabs.addWidget(self._tab_existing)
        tabs.addStretch()
        outer.addLayout(tabs)

        # Stack
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_starter_page())
        self._stack.addWidget(self._build_blank_page())
        self._stack.addWidget(self._build_existing_page())
        # Refresh OK-button state and the destination preview
        # whenever the page swaps so they reflect the active tab.
        self._stack.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._stack, 1)

        # Target-directory row + destination preview (shared by both
        # tabs). The preview line answers the user's most common
        # confusion: "if I pick this folder, where exactly does the
        # file end up?" It updates live as the choice changes.
        self._target_box = QFrame()
        # Single ``}`` to match the f-string's escaped ``{{`` —
        # ``}}`` here is a plain (non-f) string, so it would pass
        # through as two literal closers and Qt would warn
        # ``Could not parse stylesheet of object QFrame``.
        self._target_box.setStyleSheet(
            f"QFrame {{ background: {tc.get('bg_surface')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 6px; }"
        )
        tb = QVBoxLayout(self._target_box)
        tb.setContentsMargins(12, 10, 12, 10)
        tb.setSpacing(6)

        tr = QHBoxLayout()
        tr.setSpacing(10)
        tr.addWidget(QLabel("Save to:"))
        self._target_label = QLabel(str(self._default_target))
        self._target_label.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px;"
        )
        self._target_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tr.addWidget(self._target_label, 1)
        pick = QPushButton("Pick folder…")
        pick.clicked.connect(self._pick_target)
        tr.addWidget(pick)
        tb.addLayout(tr)

        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; background: transparent;"
        )
        self._preview_label.setWordWrap(True)
        tb.addWidget(self._preview_label)
        outer.addWidget(self._target_box)

        # Buttonbox
        self._bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self._bb.button(QDialogButtonBox.StandardButton.Ok).setText("Use it")
        self._bb.accepted.connect(self._on_accept)
        self._bb.rejected.connect(self.reject)
        outer.addWidget(self._bb)

    def _tab_button(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setMinimumHeight(40)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { "
            f"background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 2px solid {tc.get('border_card')}; "
            "border-radius: 8px; "
            f"padding: 8px 18px; "
            f"font-size: {tc.FONT_BASE}px; font-weight: 600; "
            "} "
            f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }} "
            "QPushButton:checked { "
            f"background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; "
            f"border-color: {tc.get('accent_primary')}; "
            "}"
        )
        return btn

    # ── Starter page ───────────────────────────────────────────────

    def _build_starter_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # Language filter
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Show:"))
        self._lang_filter = QComboBox()
        self._lang_filter.addItem("All languages", "all")
        self._lang_filter.addItem("C++", Language.CPP.value)
        self._lang_filter.addItem("Python (MicroPython)", Language.MICROPYTHON.value)
        self._lang_filter.addItem("Python (CircuitPython)", Language.CIRCUITPYTHON.value)
        self._lang_filter.currentIndexChanged.connect(self._refresh_starter_grid)
        filter_row.addWidget(self._lang_filter)
        filter_row.addStretch()
        v.addLayout(filter_row)

        # Scrollable tile grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background: {tc.get('bg_base')};")
        host = QWidget()
        host.setStyleSheet(f"background: {tc.get('bg_base')};")
        self._starter_grid = QGridLayout(host)
        self._starter_grid.setContentsMargins(0, 0, 0, 0)
        self._starter_grid.setSpacing(12)
        scroll.setWidget(host)
        v.addWidget(scroll, 1)

        self._refresh_starter_grid()
        return page

    def _refresh_starter_grid(self) -> None:
        # Clear
        for i in reversed(range(self._starter_grid.count())):
            item = self._starter_grid.takeAt(i)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()

        chosen_lang = self._lang_filter.currentData()
        starters = list_starters()
        if chosen_lang != "all":
            starters = [s for s in starters if s.language.value == chosen_lang]

        if not starters:
            empty = QLabel("No starters for this language yet.")
            empty.setStyleSheet(f"color: {tc.get('text_muted')};")
            self._starter_grid.addWidget(empty, 0, 0)
            self._chosen_starter = None
            self._update_ok_state()
            return

        # Reset selection on filter change so the OK button isn't
        # locked to a starter that's no longer visible.
        self._chosen_starter = None
        self._update_ok_state()

        cols = 3
        for idx, s in enumerate(starters):
            tile = _DialogStarterTile(s)
            tile.toggled.connect(
                lambda checked, st=s, t=tile: self._on_tile_toggled(checked, st, t)
            )
            self._starter_grid.addWidget(tile, idx // cols, idx % cols)

    def _on_tile_toggled(self, checked: bool, starter: Starter, tile: "_DialogStarterTile") -> None:
        if not checked:
            return
        for i in range(self._starter_grid.count()):
            w = self._starter_grid.itemAt(i).widget()
            if isinstance(w, _DialogStarterTile) and w is not tile:
                w.setChecked(False)
        self._chosen_starter = starter
        self._update_ok_state()
        self._refresh_preview()

    # ── Blank page ─────────────────────────────────────────────────

    def _build_blank_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        intro = QLabel(
            "Create an empty sketch with just enough boilerplate to start writing your own code."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {tc.get('text_secondary')};")
        v.addWidget(intro)

        # Name
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("Project name:"))
        self._blank_name = QLineEdit()
        self._blank_name.setPlaceholderText("my_sketch")
        self._blank_name.textChanged.connect(self._update_ok_state)
        self._blank_name.textChanged.connect(self._refresh_preview)
        name_row.addWidget(self._blank_name, 1)
        v.addLayout(name_row)

        # Language
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_row.addWidget(QLabel("Language:"))
        self._blank_lang = QComboBox()
        self._blank_lang.addItem("C++", Language.CPP.value)
        self._blank_lang.addItem("Python (MicroPython)", Language.MICROPYTHON.value)
        self._blank_lang.addItem("Python (CircuitPython)", Language.CIRCUITPYTHON.value)
        self._blank_lang.currentIndexChanged.connect(self._refresh_preview)
        lang_row.addWidget(self._blank_lang, 1)
        v.addLayout(lang_row)

        v.addStretch()
        return page

    # ── Existing-project page ──────────────────────────────────────

    def _build_existing_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        intro = QLabel(
            "Open a folder that already has your Arduino code. "
            "We'll spot the .ino, code.py or main.py inside."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {tc.get('text_secondary')};")
        v.addWidget(intro)

        pick_row = QHBoxLayout()
        pick_btn = QPushButton("📁  Choose folder…")
        pick_btn.setMinimumHeight(36)
        pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pick_btn.clicked.connect(self._pick_existing_folder)
        pick_btn.setStyleSheet(
            "QPushButton { "
            f"background: {tc.get('bg_surface_raised')}; "
            f"color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_secondary')}; "
            "border-radius: 6px; "
            f"padding: 8px 16px; font-size: {tc.FONT_BASE}px; "
            "font-weight: 600; "
            "} "
            f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }}"
        )
        pick_row.addWidget(pick_btn)
        pick_row.addStretch()
        v.addLayout(pick_row)

        # Selected-folder display
        self._existing_folder_label = QLabel("No folder picked yet.")
        self._existing_folder_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_SM}px; padding-top: 4px;"
        )
        self._existing_folder_label.setWordWrap(True)
        v.addWidget(self._existing_folder_label)

        # Detection result — green tick or red cross + explanation
        self._existing_result_label = QLabel("")
        self._existing_result_label.setWordWrap(True)
        self._existing_result_label.setStyleSheet(f"font-size: {tc.FONT_BASE}px; padding-top: 6px;")
        v.addWidget(self._existing_result_label)

        v.addStretch()
        return page

    def _pick_existing_folder(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self,
            "Open an Arduino project folder",
            str(self._target_dir),
        )
        if not picked:
            return
        folder = Path(picked)
        self._existing_folder_label.setText(str(folder))
        self._existing_folder_label.setStyleSheet(
            f"color: {tc.get('text_primary')}; font-size: {tc.FONT_SM}px; padding-top: 4px;"
        )
        detected = detect_in(folder)
        self._chosen_existing = detected
        if detected is None:
            self._existing_result_label.setText(
                "❌  No Arduino-shaped file in this folder.<br>"
                "<span style='color:" + tc.get("text_muted") + ";'>"
                "We're looking for <b>*.ino</b>, <b>code.py</b>, "
                "or <b>main.py</b> directly in this folder or one "
                "level deep.</span>"
            )
            self._existing_result_label.setStyleSheet(
                f"color: {tc.get('accent_danger')}; font-size: {tc.FONT_BASE}px; padding-top: 6px;"
            )
        else:
            self._existing_result_label.setText(
                f"✅  Found <b>{detected.entry_file.name}</b> "
                f"({_LANG_LABEL[detected.language]}) in "
                f"<b>{detected.project_dir}</b>"
            )
            self._existing_result_label.setStyleSheet(
                f"color: {tc.get('accent_success')}; font-size: {tc.FONT_BASE}px; padding-top: 6px;"
            )
        self._update_ok_state()

    # ── Target dir ────────────────────────────────────────────────

    def _pick_target(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self, "Save the project to…", str(self._target_dir)
        )
        if picked:
            self._target_dir = Path(picked)
            self._target_label.setText(picked)
            self._refresh_preview()

    def _on_tab_changed(self, index: int) -> None:
        # The "Save to / will be saved as" box only makes sense for
        # the create paths (starter, blank). For "Open existing" the
        # user picks a folder that already contains the project, so
        # we hide the box to avoid a misleading "Save to" hint.
        if hasattr(self, "_target_box"):
            self._target_box.setVisible(index != 2)
        self._update_ok_state()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Update the "Will be saved as:" line for the active tab.

        Pure path arithmetic via :func:`starter_destination` and a
        small mirror for the blank-project layout — never touches
        the filesystem so it's safe to fire on every keystroke.
        """
        if not hasattr(self, "_preview_label"):
            return  # construction order: row built before pages

        page = self._stack.currentIndex()
        if page == 0:  # starter tab
            if self._chosen_starter is None:
                self._preview_label.setText("Pick a starter to see where it'll be saved.")
                return
            dest = starter_destination(self._chosen_starter, self._target_dir)
            self._preview_label.setText(f"Will be saved as: <b>{dest}</b>")
            return

        # Blank tab
        name = self._blank_name.text().strip()
        if not name:
            self._preview_label.setText("Enter a project name to see where it'll be saved.")
            return
        safe = safe_folder_name(name)
        lang = Language(self._blank_lang.currentData())
        if lang is Language.CPP:
            dest = self._target_dir / safe / f"{safe}.ino"
        elif lang is Language.CIRCUITPYTHON:
            dest = self._target_dir / safe / "code.py"
        else:
            dest = self._target_dir / safe / "main.py"
        self._preview_label.setText(f"Will be saved as: <b>{dest}</b>")

    # ── Accept ────────────────────────────────────────────────────

    def _update_ok_state(self) -> None:
        if not hasattr(self, "_bb"):
            return
        page = self._stack.currentIndex()
        ok = self._bb.button(QDialogButtonBox.StandardButton.Ok)
        if page == 0:  # starter
            ok.setEnabled(self._chosen_starter is not None)
        elif page == 1:  # blank
            ok.setEnabled(bool(self._blank_name.text().strip()))
        else:  # existing
            ok.setEnabled(self._chosen_existing is not None)

    def _on_accept(self) -> None:
        page = self._stack.currentIndex()
        if page == 0:
            if self._chosen_starter is None:
                QMessageBox.information(self, "Pick one", "Pick a starter to continue.")
                return
            self._result = ChangeResult(target_dir=self._target_dir, starter=self._chosen_starter)
        elif page == 1:
            name = self._blank_name.text().strip()
            if not name:
                QMessageBox.information(self, "Project name", "Give your new project a name.")
                return
            lang = Language(self._blank_lang.currentData())
            self._result = ChangeResult(
                target_dir=self._target_dir,
                blank_name=name,
                blank_language=lang,
            )
        else:  # existing
            if self._chosen_existing is None:
                QMessageBox.information(
                    self,
                    "Pick a folder",
                    "Choose a folder that contains an Arduino-shaped file.",
                )
                return
            self._result = ChangeResult(
                target_dir=self._chosen_existing.project_dir,
                existing=self._chosen_existing,
            )
        self.accept()


# ── Tile reused only by this dialog ────────────────────────────────


class _DialogStarterTile(QPushButton):
    """A simpler tile than the panel's ``_StarterTile``.

    Lives in the dialog because the panel's tile imports module-level
    state we don't want to drag in here, and the dialog tile has
    slightly different size constraints (denser grid in a smaller
    window). Kept tiny: emoji, name, blurb.
    """

    def __init__(self, starter: Starter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.starter = starter
        self.setCheckable(True)
        self.setMinimumSize(190, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("")

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)

        emoji = QLabel(starter.emoji)
        emoji.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emoji.setStyleSheet("font-size: 28px; background: transparent;")
        emoji.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v.addWidget(emoji)

        name = QLabel(starter.name)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet(
            f"color: {tc.get('text_heading')}; "
            f"font-size: {tc.FONT_BASE}px; font-weight: 700; "
            "background: transparent;"
        )
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v.addWidget(name)

        blurb = QLabel(starter.blurb)
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blurb.setWordWrap(True)
        blurb.setStyleSheet(
            f"color: {tc.get('text_secondary')}; "
            f"font-size: {tc.FONT_SM}px; "
            "background: transparent;"
        )
        blurb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v.addWidget(blurb)

        self.setStyleSheet(
            "QPushButton { "
            f"background: {tc.get('bg_surface_raised')}; "
            f"border: 2px solid {tc.get('border_card')}; "
            "border-radius: 10px; "
            "} "
            f"QPushButton:hover {{ border-color: {tc.get('accent_primary')}; }} "
            "QPushButton:checked { "
            f"border-color: {tc.get('accent_primary')}; "
            f"background: {tc.get('bg_active')}; "
            "} "
            "QPushButton:focus { outline: none; }"
        )
