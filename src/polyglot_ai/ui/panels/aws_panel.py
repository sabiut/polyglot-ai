"""AWS sidebar panel — the resource types in ``core.aws_resources``, via the ``aws`` CLI.

Same shape as the Kubernetes panel: profile/region selectors on top, a
resource tree, a details pane underneath, and everything that shells
out runs through the thread bridge so the window never freezes. No
auto-refresh timer — every call is a real API request that costs
latency (and sometimes money), so refreshes are on demand.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core import aws_cli
from polyglot_ai.core.aws_resources import BY_KEY, RESOURCE_TYPES, format_summary
from polyglot_ai.ui import theme
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels import shared_icons
from polyglot_ai.ui.thread_bridge import run_in_thread
from polyglot_ai.ui.widgets.icon_button import make_icon_button

logger = logging.getLogger(__name__)

#: ``us-east-1``, ``ap-southeast-2``, ``us-gov-west-1``… Checked before a
#: typed region is used: a typo would otherwise cost a dozen failing calls.
_REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d+")

#: Added to the details title when a refresh no longer lists the resource.
_GONE_SUFFIX = " · no longer listed"


def _pretty(output: str) -> str:
    """Pretty-print JSON output; pass anything else through."""
    try:
        return json.dumps(json.loads(output), indent=2)
    except (json.JSONDecodeError, TypeError):
        return output


def fetch_overview(profile: str, region: str) -> dict:
    """Identity, then every resource type in ``RESOURCE_TYPES``. Runs off-thread.

    Each section fails independently (one missing permission must not
    blank the panel); errors come back per section. The lists run in
    parallel — there are eleven of them now, and each is a separate CLI
    process that takes about a second.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    result: dict = {"identity": None, "errors": {}, "notes": {}}
    for rt in RESOURCE_TYPES:
        result[rt.key] = []
    lock = threading.Lock()

    def make_call(section: str):
        def note(text: str) -> None:
            with lock:
                result["notes"].setdefault(section, text)

        def fail(text: str) -> None:
            with lock:
                result["errors"].setdefault(section, text)

        def call(argv: list[str], *, global_call: bool = False, report=fail):
            out, code = aws_cli.run(argv, profile=profile, region="" if global_call else region)
            if code != 0:
                report(out.removeprefix("Error: "))
                return None
            try:
                data = json.loads(out) if out else {}
            except json.JSONDecodeError:
                report("Unexpected (non-JSON) output")
                return None
            # ``--max-items N`` stops the listing at N and leaves a
            # NextToken; without a note the section would look complete.
            if isinstance(data, dict) and data.get("NextToken") and "--max-items" in argv:
                limit = argv[argv.index("--max-items") + 1]
                note(f"Only the first {limit} are shown — there are more.")
            return data

        # Fetchers with their own caps (ECS) report them the same way.
        call.note = note
        # For the per-row lookups that only fill in Status/Info: the
        # listing itself worked, so their failures aren't worth a line.
        call.quiet = lambda argv, **kw: call(argv, report=lambda text: None, **kw)
        return call

    ident = make_call("identity")(["sts", "get-caller-identity"], global_call=True)
    if not ident:
        # Without an identity nothing else will work — don't make eleven
        # more calls that produce the same credentials error.
        return result
    result["identity"] = ident

    def run_type(rt):
        try:
            return rt.key, rt.fetch(make_call(rt.key))
        except Exception as exc:  # a parser surprise must not sink the rest
            logger.exception("AWS %s listing failed", rt.key)
            with lock:
                result["errors"].setdefault(rt.key, f"Couldn't read the listing: {exc}")
            return rt.key, []

    with ThreadPoolExecutor(max_workers=6) as pool:
        for key, rows in pool.map(run_type, RESOURCE_TYPES):
            for row in rows:
                row["type"] = key
            result[key] = rows
    return result


class AwsPanel(QWidget):
    """AWS resource browser backed by the user's ``aws`` CLI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = ""
        self._region = ""
        self._identity: dict | None = None
        self._overview: dict = self._empty_overview()
        self._details_request = 0
        #: Bumped per refresh: switching profile/region quickly could let an
        #: older fetch finish last and overwrite the newer result.
        self._overview_request = 0
        self._loaded_once = False
        self._current_details_text = ""
        #: The row whose details are showing (for Open in Console / Copy).
        self._details_row: dict | None = None
        #: True when the pane shows that row's describe output (re-run on
        #: refresh), False for an action result or a log tail (kept as is).
        self._details_is_describe = False
        #: (profile, region) the details were fetched with.
        self._details_context: tuple[str, str] = ("", "")
        #: One later refresh after an action, so the settled state shows.
        self._follow_up_timer = QTimer(self)
        self._follow_up_timer.setSingleShot(True)
        self._follow_up_timer.timeout.connect(self._follow_up_refresh)
        self._follow_up_request = 0
        #: Arrowing through the tree would start a describe (a CLI process)
        #: per row passed; wait until the selection settles.
        self._select_timer = QTimer(self)
        self._select_timer.setSingleShot(True)
        self._select_timer.timeout.connect(self._describe_current)
        #: Sections the user collapsed — kept across refreshes.
        self._collapsed: set[str] = set()

        self._setup_ui()
        self._apply_theme_styles()
        theme.connect_theme_changed(self._apply_theme_styles)

    #: Delay before the follow-up refresh after an action (0 disables it).
    _FOLLOW_UP_REFRESH_MS = 10_000
    #: How long the selection must rest before its details are fetched.
    _SELECT_DEBOUNCE_MS = 150

    @staticmethod
    def _empty_overview() -> dict:
        return {"errors": {}, "notes": {}, **{rt.key: [] for rt in RESOURCE_TYPES}}

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self._load_profiles()

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("awsHeader")
        self._header.setFixedHeight(36)
        h = QHBoxLayout(self._header)
        h.setContentsMargins(12, 0, 8, 0)
        self._title = QLabel("AWS")
        h.addWidget(self._title)
        h.addStretch()
        refresh_btn = make_icon_button(shared_icons.draw_refresh_icon(), "Refresh")
        refresh_btn.clicked.connect(self._refresh)
        h.addWidget(refresh_btn)
        layout.addWidget(self._header)

        self._selector_bar = QWidget()
        self._selector_bar.setObjectName("awsSelectorBar")
        sel = QVBoxLayout(self._selector_bar)
        sel.setContentsMargins(8, 4, 8, 4)
        sel.setSpacing(4)

        prof_row = QHBoxLayout()
        self._profile_label = QLabel("Profile:")
        self._profile_label.setFixedWidth(60)
        prof_row.addWidget(self._profile_label)
        self._profile_combo = QComboBox()
        self._profile_combo.currentTextChanged.connect(self._on_profile_changed)
        prof_row.addWidget(self._profile_combo, stretch=1)
        sel.addLayout(prof_row)

        region_row = QHBoxLayout()
        self._region_label = QLabel("Region:")
        self._region_label.setFixedWidth(60)
        region_row.addWidget(self._region_label)
        self._region_combo = QComboBox()
        self._region_combo.setEditable(True)
        self._region_combo.addItems(aws_cli.REGIONS)
        # Not ``currentTextChanged``: in an editable combo that fires on
        # every keystroke, so typing "eu-west-3" started nine refreshes
        # against half-typed regions. Act on a pick from the list, or on
        # Enter / leaving the field.
        self._region_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._region_combo.activated.connect(
            lambda _index: self._on_region_changed(self._region_combo.currentText())
        )
        self._region_combo.lineEdit().editingFinished.connect(
            lambda: self._on_region_changed(self._region_combo.currentText())
        )
        region_row.addWidget(self._region_combo, stretch=1)
        sel.addLayout(region_row)

        self._identity_label = QLabel("")
        self._identity_label.setWordWrap(True)
        sel.addWidget(self._identity_label)

        from PyQt6.QtWidgets import QLineEdit

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter resources…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        sel.addWidget(self._filter)
        layout.addWidget(self._selector_bar)

        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Resource", "Status", "Info"])
        self._tree.setColumnWidth(0, 200)
        self._tree.setColumnWidth(1, 110)  # fits "ap-southeast-2", "2 subscribers"
        # Section rows carry their own ▾/▸ in the text: the theme's branch
        # arrows are invisible on the dark background, so a collapsed
        # section looked empty with no way back. One click toggles it.
        self._tree.setRootIsDecorated(False)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setIndentation(14)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemExpanded.connect(lambda it: self._on_section_toggled(it, True))
        self._tree.itemCollapsed.connect(lambda it: self._on_section_toggled(it, False))

        # The services with nothing in them, under the tree. A label wraps;
        # as a tree row the list was cut off after the first few names.
        self._empty_note = QLabel("")
        self._empty_note.setWordWrap(True)
        self._empty_note.setVisible(False)
        listing = QWidget()
        listing_layout = QVBoxLayout(listing)
        listing_layout.setContentsMargins(0, 0, 0, 0)
        listing_layout.setSpacing(0)
        listing_layout.addWidget(self._tree, stretch=1)
        listing_layout.addWidget(self._empty_note)
        self._splitter.addWidget(listing)

        details = QWidget()
        d = QVBoxLayout(details)
        d.setContentsMargins(0, 0, 0, 0)
        d.setSpacing(0)
        self._details_header = QWidget()
        self._details_header.setObjectName("awsDetailsHeader")
        self._details_header.setFixedHeight(28)
        dh = QHBoxLayout(self._details_header)
        dh.setContentsMargins(8, 0, 4, 0)
        self._details_title = QLabel("DETAILS")
        dh.addWidget(self._details_title)
        dh.addStretch()
        from polyglot_ai.ui.panels import nav_icons

        self._console_btn = make_icon_button(
            shared_icons.draw_popout_icon(), "Open in the AWS Console"
        )
        self._console_btn.clicked.connect(lambda: self._open_console(self._details_row))
        self._console_btn.setEnabled(False)
        dh.addWidget(self._console_btn)
        self._ai_btn = make_icon_button(nav_icons.make_chat_icon(), "Send these details to the AI")
        self._ai_btn.clicked.connect(self._send_details_to_ai)
        dh.addWidget(self._ai_btn)
        expand_btn = make_icon_button(shared_icons.draw_expand_icon(), "Open in a full window")
        expand_btn.clicked.connect(self._open_details_window)
        dh.addWidget(expand_btn)
        d.addWidget(self._details_header)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        # The system fixed-width font: a bare "Monospace" family doesn't
        # resolve everywhere, and the summary's aligned columns need it.
        from PyQt6.QtGui import QFontDatabase

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(10)
        self._details.setFont(mono)
        self._details.setPlaceholderText("Click a resource to view details")
        d.addWidget(self._details)
        self._splitter.addWidget(details)
        self._splitter.setSizes([300, 180])
        layout.addWidget(self._splitter)

        self._status_label = QLabel("  AWS: checking...")
        self._status_label.setFixedHeight(24)
        layout.addWidget(self._status_label)

    def _apply_theme_styles(self) -> None:
        self._header.setStyleSheet(
            f"#awsHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        self._title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; color: {tc.get('text_tertiary')}; "
            "letter-spacing: 0.5px; background: transparent;"
        )
        self._selector_bar.setStyleSheet(f"#awsSelectorBar {{ background: {tc.get('bg_base')}; }}")
        combo = (
            f"QComboBox {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; "
            f"padding: 3px 8px; font-size: {tc.FONT_SM}px; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; "
            f"border-right: 4px solid transparent; border-top: 5px solid {tc.get('text_secondary')}; "
            f"margin-right: 6px; }}"
        )
        self._profile_combo.setStyleSheet(combo)
        self._region_combo.setStyleSheet(combo)
        label = f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;"
        self._profile_label.setStyleSheet(label)
        self._region_label.setStyleSheet(label)
        self._identity_label.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; padding: 2px 0;"
        )
        self._filter.setStyleSheet(
            f"QLineEdit {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; "
            f"padding: 3px 8px; font-size: {tc.FONT_SM}px; }}"
        )
        self._splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 2px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 3px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )
        self._empty_note.setStyleSheet(
            f"color: {tc.get('text_muted')}; background: {tc.get('bg_base')}; "
            f"font-size: {tc.FONT_XS}px; padding: 4px 8px;"
        )
        self._details_header.setStyleSheet(
            f"#awsDetailsHeader {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        self._details_title.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; background: transparent;"
        )
        # The font has to be in the stylesheet: the app theme's stylesheet
        # sets a proportional font on every widget, overriding setFont(),
        # and the summary's columns only line up in a fixed-width font.
        self._details.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 4px; "
            f"font-family: '{self._details.font().family()}', 'JetBrains Mono', "
            f"'DejaVu Sans Mono', 'Fira Code', monospace; font-size: {tc.FONT_SM}px; }}"
        )
        self._status_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px;"
        )

    # ── Profiles / regions ──────────────────────────────────────────

    def _load_profiles(self) -> None:
        if not aws_cli.aws_available():
            self._status_label.setText("  AWS CLI not installed")
            self._show_hint(
                "The AWS CLI (`aws`) isn't installed. Install it from "
                "https://aws.amazon.com/cli/ then run `aws configure`."
            )
            return

        def work():
            profiles = aws_cli.list_profiles()
            regions = {p: aws_cli.profile_region(p) for p in profiles}
            return profiles, regions

        def done(result):
            profiles, regions = result
            self._profile_combo.blockSignals(True)
            self._profile_combo.clear()
            self._profile_combo.addItems(profiles or ["default"])
            self._profile_combo.blockSignals(False)
            self._profile = self._profile_combo.currentText()
            region = regions.get(self._profile) or aws_cli.profile_region("") or "us-east-1"
            self._region_combo.blockSignals(True)
            self._region_combo.setCurrentText(region)
            self._region_combo.blockSignals(False)
            self._region = region
            self._publish_context()
            self._refresh()

        run_in_thread(work, done, owner=self, name="aws-profiles")

    def _on_profile_changed(self, text: str) -> None:
        if text and text != self._profile:
            self._profile = text
            region = aws_cli.profile_region(text)
            if region:
                self._region_combo.setCurrentText(region)
                self._region = region
            self._context_changed()

    def _on_region_changed(self, text: str) -> None:
        text = text.strip().lower()
        if not text or text == self._region:
            return
        if not _REGION_RE.fullmatch(text):
            self._status_label.setText(
                f"  “{text}” isn't an AWS region name — expected something like eu-west-1"
            )
            return
        self._region = text
        self._context_changed()

    def _publish_context(self) -> None:
        """Tell the AI's AWS tools which profile and region are on screen."""
        from polyglot_ai.core import panel_state

        panel_state.set_aws_context(self._profile, self._region)

    def _context_changed(self) -> None:
        """Profile or region switched: nothing on screen belongs to it.

        The old rows must go before the new listing arrives — a right-click
        on one would run its action against the new account — and so must
        the details, which the Console button and "Send to AI" would
        otherwise attribute to the new profile and region.
        """
        self._publish_context()
        self._details_request += 1  # drop a describe that is still in flight
        self._follow_up_timer.stop()
        self._select_timer.stop()
        self._details_row = None
        self._details_is_describe = False
        self._console_btn.setEnabled(False)
        self._details_title.setText("DETAILS")
        self._details.setPlainText("")
        self._current_details_text = ""
        self._overview = self._empty_overview()
        self._identity = None
        self._identity_label.setText("")
        self._show_hint("Loading…")
        self._refresh()

    # ── Overview ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if not aws_cli.aws_available():
            self._status_label.setText("  AWS CLI not installed")
            return
        profile, region = self._profile, self._region
        self._overview_request += 1
        request = self._overview_request
        self._status_label.setText(f"  {profile or 'default'} @ {region} — loading...")

        def done(data: dict) -> None:
            if request == self._overview_request:
                self._on_overview(data)

        def failed(exc: Exception) -> None:
            if request == self._overview_request:
                self._status_label.setText(f"  Error: {exc}")
                if self._identity is None:  # nothing listed yet: don't sit on "Loading…"
                    self._show_hint(f"Error: {exc}")

        run_in_thread(
            lambda: fetch_overview(profile, region),
            done,
            failed,
            owner=self,
            name="aws-overview",
        )

    def _on_overview(self, data: dict) -> None:
        self._overview = data
        self._identity = data.get("identity")
        errors: dict = data.get("errors", {})
        if self._identity:
            account = self._identity.get("Account", "?")
            arn = self._identity.get("Arn", "")
            who = arn.rsplit("/", 1)[-1] if "/" in arn else arn
            self._identity_label.setText(f"Account {account} · {who}")
        else:
            self._identity_label.setText("")
        self._populate_tree()

        if "identity" in errors:
            self._status_label.setText(f"  {errors['identity']}")
            self._show_hint(errors["identity"])
            return
        found = [(rt.title, len(data.get(rt.key, []))) for rt in RESOURCE_TYPES]
        total = sum(n for _, n in found)
        services = sum(1 for _, n in found if n)
        counts = f"{total} resources in {services} services" if total else "no resources"
        problems = [f"{k}: {v}" for k, v in errors.items()]
        suffix = f"  ({'; '.join(problems)})" if problems else ""
        self._status_label.setText(
            f"  {self._profile or 'default'} @ {self._region} — {counts}{suffix}"
        )

    def _show_hint(self, text: str) -> None:
        self._tree.clear()
        self._empty_note.setText("")
        self._empty_note.setVisible(False)
        hint = QTreeWidgetItem(self._tree)
        hint.setText(0, text)
        hint.setForeground(0, QColor(tc.get("text_muted")))
        hint.setFlags(Qt.ItemFlag.NoItemFlags)
        hint.setFirstColumnSpanned(True)

    @staticmethod
    def _section_text(title: str, count: int, expanded: bool, more: bool = False) -> str:
        return f"{'▾' if expanded else '▸'}  {title} ({count}{'+' if more else ''})"

    def _populate_tree(self) -> None:
        self._tree.clear()
        data = self._overview
        errors: dict = data.get("errors", {})
        notes: dict = data.get("notes", {})
        bold = QFont()
        bold.setBold(True)
        empty: list[str] = []

        for rt in RESOURCE_TYPES:
            rows = data.get(rt.key, [])
            if not rows and rt.key not in errors:
                empty.append(rt.title)
                continue
            expanded = rt.key not in self._collapsed
            more = rt.key in notes
            root = QTreeWidgetItem(self._tree)
            root.setText(0, self._section_text(rt.title, len(rows), expanded, more))
            root.setFont(0, bold)
            root.setFirstColumnSpanned(True)
            root.setData(
                0, Qt.ItemDataRole.UserRole, {"section": rt.key, "title": rt.title, "more": more}
            )
            if rt.key in errors:
                err = QTreeWidgetItem(root)
                err.setText(0, errors[rt.key])
                err.setForeground(0, QColor(tc.get("accent_warning")))
                err.setFlags(Qt.ItemFlag.NoItemFlags)
                err.setFirstColumnSpanned(True)
            for row in rows:
                item = QTreeWidgetItem(root)
                item.setText(0, row.get("label", row.get("name", "")))
                item.setText(1, str(row.get("status") or ""))
                item.setText(2, str(row.get("info") or ""))
                item.setToolTip(0, row.get("label", ""))
                colour = rt.status_colour(row)
                if colour:
                    item.setForeground(1, QColor(tc.get(colour)))
                item.setData(0, Qt.ItemDataRole.UserRole, {"type": rt.key, **row})
            if more:
                # The listing was cut short: say so, or it reads as complete.
                cut = QTreeWidgetItem(root)
                cut.setText(0, notes[rt.key])
                cut.setForeground(0, QColor(tc.get("text_muted")))
                cut.setFlags(Qt.ItemFlag.NoItemFlags)
                cut.setFirstColumnSpanned(True)
            # Expand only after the children exist.
            root.setExpanded(expanded)

        self._empty_note.setText(
            "None here: " + ", ".join(empty) if empty and self._tree.topLevelItemCount() else ""
        )
        if not self._tree.topLevelItemCount():
            hint = QTreeWidgetItem(self._tree)
            hint.setText(0, "Nothing found in this region for this profile.")
            hint.setForeground(0, QColor(tc.get("text_muted")))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setFirstColumnSpanned(True)
        self._apply_filter(self._filter.text())
        self._restore_details_row()

    def _find_item(self, kind: str, ident: str):
        for i in range(self._tree.topLevelItemCount()):
            section = self._tree.topLevelItem(i)
            for j in range(section.childCount()):
                data = section.child(j).data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("type") == kind and data.get("id") == ident:
                    return section.child(j)
        return None

    def _restore_details_row(self) -> None:
        """After a refresh: re-select the row the details are about, or say it has gone.

        Rebuilding the tree drops the selection, and the details pane used
        to keep showing a resource that was no longer in the list (a
        database that had finished deleting) as if it were current.
        """
        row = self._details_row
        if row is None:
            return
        item = self._find_item(row.get("type", ""), row.get("id", ""))
        if item is None:
            self._details_row = None
            self._details_is_describe = False
            self._console_btn.setEnabled(False)
            title = self._details_title.text()
            if not title.endswith(_GONE_SUFFIX):
                self._details_title.setText(title + _GONE_SUFFIX)
            return
        self._tree.blockSignals(True)  # selecting must not count as a click
        self._tree.setCurrentItem(item)
        self._tree.blockSignals(False)
        self._details_row = item.data(0, Qt.ItemDataRole.UserRole)
        if self._details_is_describe:
            # Bring the details up to date with the refreshed row; an
            # action result or a log tail is left alone.
            self._on_item_selected(item, None, keep_text=True)

    def _on_item_clicked(self, item, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and "section" in data:
            item.setExpanded(not item.isExpanded())

    def _on_section_toggled(self, item, expanded: bool) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not (isinstance(data, dict) and "section" in data):
            return
        key = data["section"]
        if expanded:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        count = sum(
            1
            for i in range(item.childCount())
            if isinstance(item.child(i).data(0, Qt.ItemDataRole.UserRole), dict)
        )
        item.setText(0, self._section_text(data["title"], count, expanded, data.get("more", False)))

    def _apply_filter(self, text: str) -> None:
        """Hide resources whose name/info don't contain ``text``."""
        needle = text.strip().lower()
        # Not while filtering: "None here" is about the account, not the filter.
        self._empty_note.setVisible(bool(self._empty_note.text()) and not needle)
        for i in range(self._tree.topLevelItemCount()):
            section = self._tree.topLevelItem(i)
            meta = section.data(0, Qt.ItemDataRole.UserRole)
            if not (isinstance(meta, dict) and "section" in meta):
                section.setHidden(bool(needle))
                continue
            visible = 0
            for j in range(section.childCount()):
                child = section.child(j)
                hay = f"{child.text(0)} {child.text(1)} {child.text(2)}".lower()
                show = not needle or needle in hay
                child.setHidden(not show)
                visible += show
            section.setHidden(bool(needle) and visible == 0)
            if needle and visible:
                section.setExpanded(True)

    # ── Details ─────────────────────────────────────────────────────

    def _show_in_details(
        self,
        title: str,
        argv: list[str],
        *,
        json_output: bool = True,
        summarize=None,
        describe: bool = False,
        keep_text: bool = False,
        after=None,
        facts=None,
        output_label: str = "Full output",
    ) -> None:
        """Run ``argv`` and show its output in the details pane.

        ``describe`` marks the output as the selected row's own details
        (re-fetched on refresh). ``keep_text`` leaves the current text up
        while reloading. ``after`` runs once the command has succeeded.
        ``facts(run)`` makes further calls for the summary; it runs
        alongside ``argv`` and its rows come first.
        """
        self._details_title.setText(title)
        if not keep_text:
            self._details.setPlainText("Loading...")
        self._details_is_describe = describe
        self._details_request += 1
        request = self._details_request
        profile, region = self._profile, self._region
        self._details_context = (profile, region)

        def run(extra_argv: list[str]) -> tuple[str, int]:
            return aws_cli.run(extra_argv, profile=profile, region=region, timeout=30)

        def work():
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as pool:
                extra = pool.submit(facts, run) if facts is not None else None
                main = aws_cli.run(
                    argv, profile=profile, region=region, timeout=60, json_output=json_output
                )
                try:
                    pairs = extra.result() if extra is not None else []
                except Exception:  # the summary is a bonus; never lose the output to it
                    logger.exception("AWS details: extra facts failed")
                    pairs = []
            return main, pairs

        def done(result):
            (output, code), pairs = result
            if code == 0 and after is not None:
                # The action happened even if the pane has since moved on
                # to another resource, so this must not depend on it.
                after()
            if request != self._details_request:
                return
            text = _pretty(output) if code == 0 else output
            if code == 0 and summarize is not None:
                try:
                    pairs = [*pairs, *summarize(json.loads(output))]
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            # The facts stand on their own calls, so they show even when
            # the main one failed (a bucket you can't list still has them).
            summary = format_summary(pairs)
            if summary:
                text = f"{summary}\n\n── {output_label} ──\n{text}"
            self._current_details_text = text
            self._details.setPlainText(text or "(no output)")

        def failed(exc: Exception) -> None:
            if request == self._details_request:
                self._details.setPlainText(f"Error: {exc}")

        run_in_thread(work, done, failed, owner=self, name="aws-details")

    def _on_current_item_changed(self, current, previous) -> None:
        """The selection moved: fetch its details once it has settled."""
        if current is None:
            self._select_timer.stop()
        elif self._SELECT_DEBOUNCE_MS:
            self._select_timer.start(self._SELECT_DEBOUNCE_MS)
        else:
            self._on_item_selected(current, previous)

    def _describe_current(self) -> None:
        # Whatever is current now — the item that started the timer may
        # have been replaced by a refresh since.
        self._on_item_selected(self._tree.currentItem(), None)

    def _on_item_selected(self, current, previous, *, keep_text: bool = False) -> None:
        if not current:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or "type" not in data:
            return
        rt = BY_KEY.get(data["type"])
        if rt is None or rt.describe is None:
            return
        self._details_row = data
        self._console_btn.setEnabled(rt.console_url is not None)
        self._show_in_details(
            f"{rt.title.split()[0].upper()} — {data.get('name') or data.get('id', '')}",
            rt.describe(data),
            json_output=not rt.describe_is_text,
            summarize=rt.summarize,
            describe=True,
            keep_text=keep_text,
            facts=(lambda run: rt.facts(data, run)) if rt.facts is not None else None,
            output_label=rt.output_label,
        )

    def _console_url(self, row: dict | None) -> str:
        if not row:
            return ""
        rt = BY_KEY.get(row.get("type", ""))
        if rt is None or rt.console_url is None:
            return ""
        return rt.console_url(row, self._region or "us-east-1")

    def _open_console(self, row: dict | None) -> None:
        url = self._console_url(row)
        if not url:
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))
        self._status_label.setText(
            f"  Opened {row.get('name') or row.get('id')} in the AWS Console"
        )

    # ── Connect to instances (terminal) ─────────────────────────────

    @staticmethod
    def ssh_target_for(instance: dict):
        """Seed for the SSH dialog: the instance's address, default EC2 user."""
        from polyglot_ai.core.ssh import SshTarget

        return SshTarget(host=instance.get("ip", ""), user="ec2-user")

    def ssm_command_for(self, instance_id: str) -> str:
        from polyglot_ai.core.ssh import ssm_command

        return ssm_command(instance_id, profile=self._profile, region=self._region)

    def _connect_ssh(self, instance: dict) -> None:
        window = self.window()
        opener = getattr(window, "open_ssh_session", None)
        if opener is not None:
            opener(self.ssh_target_for(instance))

    def _connect_ssm(self, instance_id: str) -> None:
        window = self.window()
        runner = getattr(window, "run_in_terminal", None)
        if runner is not None:
            runner(self.ssm_command_for(instance_id))

    # ── Context menu ────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or "type" not in data:
            return  # section headers and notes have no menu
        kind = data["type"]

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; font-size: {tc.FONT_SM}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        if kind == "lambda":
            name = data["name"]
            menu.addAction("Tail logs (last hour)").triggered.connect(
                lambda: self._tail_lambda_logs(data)
            )
            menu.addAction("Configuration").triggered.connect(
                lambda: self._on_item_selected(item, None)
            )
            menu.addSeparator()
            menu.addAction("Invoke…").triggered.connect(lambda: self._invoke_lambda(name))
        elif kind == "ec2":
            iid = data["id"]
            menu.addAction("Describe").triggered.connect(lambda: self._on_item_selected(item, None))
            menu.addSeparator()
            if data["state"] == "running":
                if data.get("ip"):
                    menu.addAction("Connect via SSH…").triggered.connect(
                        lambda: self._connect_ssh(data)
                    )
                menu.addAction("Connect via Session Manager (SSM)").triggered.connect(
                    lambda: self._connect_ssm(iid)
                )
                menu.addSeparator()
                menu.addAction("Stop instance…").triggered.connect(
                    lambda: self._ec2_action("stop-instances", iid, "Stop")
                )
                menu.addAction("Reboot instance…").triggered.connect(
                    lambda: self._ec2_action("reboot-instances", iid, "Reboot")
                )
            elif data["state"] == "stopped":
                menu.addAction("Start instance…").triggered.connect(
                    lambda: self._ec2_action("start-instances", iid, "Start")
                )
        elif kind == "ecs":
            cluster, name = data["cluster"], data["name"]
            menu.addAction("Describe").triggered.connect(lambda: self._on_item_selected(item, None))
            menu.addAction("Recent events").triggered.connect(
                lambda: self._show_in_details(
                    f"EVENTS — {name}",
                    [
                        "ecs",
                        "describe-services",
                        "--cluster",
                        cluster,
                        "--services",
                        name,
                        "--query",
                        "services[0].events[:20]",
                    ],
                )
            )
            menu.addAction("Running tasks").triggered.connect(
                lambda: self._show_in_details(
                    f"TASKS — {name}",
                    [
                        "ecs",
                        "list-tasks",
                        "--cluster",
                        cluster,
                        "--service-name",
                        name,
                        "--desired-status",
                        "RUNNING",
                    ],
                )
            )
            menu.addSeparator()
            menu.addAction("Force new deployment…").triggered.connect(
                lambda: self._ecs_redeploy(cluster, name)
            )
        elif kind == "eks":
            cluster = data["name"]
            menu.addAction("Describe").triggered.connect(lambda: self._on_item_selected(item, None))
            menu.addAction("Node groups").triggered.connect(
                lambda: self._show_in_details(
                    f"NODE GROUPS — {cluster}",
                    ["eks", "list-nodegroups", "--cluster-name", cluster],
                )
            )
            menu.addSeparator()
            menu.addAction("Add to kubeconfig…").triggered.connect(
                lambda: self._eks_update_kubeconfig(cluster)
            )
        elif kind == "s3":
            bucket = data["name"]
            menu.addAction("List top-level objects").triggered.connect(
                lambda: self._on_item_selected(item, None)
            )
            menu.addAction("Copy s3:// URI").triggered.connect(
                lambda: self._copy_text(f"s3://{bucket}")
            )

        menu.addSeparator()
        if self._console_url(data):
            menu.addAction("Open in AWS Console").triggered.connect(
                lambda: self._open_console(data)
            )
        ident = data.get("arn") or data.get("url") or data.get("id") or data.get("name", "")
        if ident:
            what = "ARN" if data.get("arn") else "URL" if data.get("url") else "ID"
            menu.addAction(f"Copy {what}").triggered.connect(lambda: self._copy_text(ident))
        menu.addAction("Send details to AI").triggered.connect(self._send_details_to_ai)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Actions ─────────────────────────────────────────────────────

    def _confirm(self, title: str, text: str) -> bool:
        reply = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _where(self) -> str:
        """``eu-west-1 (profile work)`` — for confirmation dialogs."""
        return f"{self._region} (profile {self._profile or 'default'})"

    def _refresh_after_action(self) -> None:
        """An action changed something: the tree shows the state from before it."""
        self._refresh()
        if self._FOLLOW_UP_REFRESH_MS:
            # Stopping, starting and redeploying take a while. One later
            # look shows the settled state without making the panel poll.
            self._follow_up_request = self._overview_request
            self._follow_up_timer.start(self._FOLLOW_UP_REFRESH_MS)

    def _follow_up_refresh(self) -> None:
        # Skip it if anything refreshed (or the profile/region changed) since.
        if self._follow_up_request == self._overview_request:
            self._refresh()

    def _ec2_action(self, verb: str, instance_id: str, label: str) -> None:
        if not self._confirm(
            f"{label} instance",
            f"{label} EC2 instance {instance_id} in {self._where()}?\n\n"
            f"This runs: aws ec2 {verb} --instance-ids {instance_id}",
        ):
            return
        self._show_in_details(
            f"{label.upper()} — {instance_id}",
            ["ec2", verb, "--instance-ids", instance_id],
            after=self._refresh_after_action,
        )

    def _ecs_redeploy(self, cluster: str, service: str) -> None:
        if not self._confirm(
            "Force new deployment",
            f"Force a new deployment of {service} in cluster {cluster}, {self._where()}?\n\n"
            "Running tasks are replaced with fresh ones using the current task definition.",
        ):
            return
        self._show_in_details(
            f"REDEPLOY — {service}",
            [
                "ecs",
                "update-service",
                "--cluster",
                cluster,
                "--service",
                service,
                "--force-new-deployment",
                "--query",
                "service.deployments",
            ],
            after=self._refresh_after_action,
        )

    def _eks_update_kubeconfig(self, cluster: str) -> None:
        """Write the cluster into ~/.kube/config so the Kubernetes panel can use it.

        Local change only (no AWS resources are modified), but it edits the
        user's kubeconfig and switches the current context, so ask first.
        """
        if not self._confirm(
            "Add to kubeconfig",
            f"Add EKS cluster {cluster} ({self._region}) to ~/.kube/config and make it "
            f"the current context?\n\nThis runs: aws eks update-kubeconfig --name {cluster}",
        ):
            return
        self._show_in_details(
            f"KUBECONFIG — {cluster}",
            ["eks", "update-kubeconfig", "--name", cluster],
            json_output=False,
        )
        self._status_label.setText(
            f"  {cluster} added to kubeconfig — open the Kubernetes panel (Ctrl+Shift+8) to browse it"
        )

    def _tail_lambda_logs(self, function: dict) -> None:
        name = function["name"]
        # The function's configured group; /aws/lambda/<name> is only the default.
        log_group = function.get("log_group") or f"/aws/lambda/{name}"
        self._show_in_details(
            f"LOGS — {name}",
            ["logs", "tail", log_group, "--since", "1h", "--format", "short"],
            json_output=False,
        )

    def _invoke_lambda(self, name: str) -> None:
        payload, ok = QInputDialog.getMultiLineText(
            self, "Invoke Lambda", f"JSON payload for {name}:", "{}"
        )
        if not ok:
            return
        payload = payload.strip() or "{}"
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Invalid JSON", f"The payload isn't valid JSON:\n{exc}")
            return
        if not self._confirm(
            "Invoke Lambda", f"Invoke {name} in {self._where()} with that payload?"
        ):
            return
        self._details_title.setText(f"INVOKE — {name}")
        self._details.setPlainText("Invoking...")
        self._details_is_describe = False
        self._details_request += 1
        request = self._details_request
        profile, region = self._profile, self._region
        self._details_context = (profile, region)

        def work():
            with tempfile.TemporaryDirectory() as tmp:
                payload_path = Path(tmp) / "payload.json"
                out_path = Path(tmp) / "response.json"
                payload_path.write_text(payload, encoding="utf-8")
                meta, code = aws_cli.run(
                    [
                        "lambda",
                        "invoke",
                        "--function-name",
                        name,
                        "--cli-binary-format",
                        "raw-in-base64-out",
                        "--payload",
                        f"file://{payload_path}",
                        str(out_path),
                    ],
                    profile=profile,
                    region=region,
                    timeout=120,
                )
                body = (
                    out_path.read_text(encoding="utf-8", errors="replace")
                    if out_path.exists()
                    else ""
                )
            return meta, code, body

        def done(result):
            if request != self._details_request:
                return
            meta, code, body = result
            if code != 0:
                self._details.setPlainText(meta)
                return
            text = f"Response:\n{_pretty(body)}\n\nInvocation metadata:\n{_pretty(meta)}"
            self._current_details_text = text
            self._details.setPlainText(text)

        run_in_thread(
            work, done, lambda exc: self._details.setPlainText(f"Error: {exc}"), owner=self
        )

    def _copy_text(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
            self._status_label.setText(f"  Copied: {text}")

    def _send_details_to_ai(self) -> None:
        """Prefill the chat with the current details pane for explanation."""
        text = self._details.toPlainText().strip()
        if not text or text in ("Loading...", "Invoking..."):
            return
        window = self.window()
        chat = getattr(window, "chat_panel", None)
        if chat is None or not hasattr(chat, "prefill_input"):
            return
        title = self._details_title.text()
        # The details can include secrets (Lambda environment values,
        # queue policies, stack outputs); they're shown locally as the
        # Console would, but never go to the AI provider unredacted.
        from polyglot_ai.core.security import redact_secrets_in_content

        text = redact_secrets_in_content(text)
        if len(text) > 12_000:
            text = text[:12_000] + "\n... (truncated)"
        profile, region = self._details_context
        chat.prefill_input(
            f"Here is AWS output ({title}, profile {profile or 'default'}, "
            f"region {region}). Explain what it shows, flag anything wrong, "
            f"and tell me what to do next:\n\n```\n{text}\n```"
        )
        right_tabs = getattr(window, "_right_tabs", None)
        if right_tabs is not None:
            idx = right_tabs.indexOf(chat)
            if idx >= 0:
                right_tabs.setCurrentIndex(idx)
            toggle = getattr(window, "_action_toggle_chat", None)
            if toggle is not None and not toggle.isChecked():
                toggle.setChecked(True)

    def _open_details_window(self) -> None:
        content = self._details.toPlainText()
        if not content or content == "Click a resource to view details":
            return
        from polyglot_ai.ui.panels.k8s_panel import _K8sDetailsWindow

        _K8sDetailsWindow(self._details_title.text(), content, self).show()
