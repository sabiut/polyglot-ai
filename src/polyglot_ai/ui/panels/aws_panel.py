"""AWS sidebar panel — Lambda, EC2, ECS and S3 at a glance, via the ``aws`` CLI.

Same shape as the Kubernetes panel: profile/region selectors on top, a
resource tree, a details pane underneath, and everything that shells
out runs through the thread bridge so the window never freezes. No
auto-refresh timer — every call is a real API request that costs
latency (and sometimes money), so refreshes are on demand.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
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

    result: dict = {"identity": None, "errors": {}}
    for rt in RESOURCE_TYPES:
        result[rt.key] = []
    lock = threading.Lock()

    def make_call(section: str):
        def call(argv: list[str], *, global_call: bool = False):
            out, code = aws_cli.run(argv, profile=profile, region="" if global_call else region)
            if code != 0:
                with lock:
                    result["errors"].setdefault(section, out.removeprefix("Error: "))
                return None
            try:
                return json.loads(out) if out else {}
            except json.JSONDecodeError:
                with lock:
                    result["errors"].setdefault(section, "Unexpected (non-JSON) output")
                return None

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
        self._overview: dict = {"lambda": [], "ec2": [], "ecs": [], "s3": [], "errors": {}}
        self._details_request = 0
        #: Bumped per refresh: switching profile/region quickly could let an
        #: older fetch finish last and overwrite the newer result.
        self._overview_request = 0
        self._loaded_once = False
        self._current_details_text = ""
        #: The row whose details are showing (for Open in Console / Copy).
        self._details_row: dict | None = None
        #: Sections the user collapsed — kept across refreshes.
        self._collapsed: set[str] = set()

        self._setup_ui()
        self._apply_theme_styles()
        theme.connect_theme_changed(self._apply_theme_styles)

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
        self._region_combo.currentTextChanged.connect(self._on_region_changed)
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
        self._tree.setColumnWidth(1, 80)
        # Section rows carry their own ▾/▸ in the text: the theme's branch
        # arrows are invisible on the dark background, so a collapsed
        # section looked empty with no way back. One click toggles it.
        self._tree.setRootIsDecorated(False)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setIndentation(14)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_item_selected)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemExpanded.connect(lambda it: self._on_section_toggled(it, True))
        self._tree.itemCollapsed.connect(lambda it: self._on_section_toggled(it, False))
        self._splitter.addWidget(self._tree)

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
            self._refresh()

        run_in_thread(work, done, owner=self, name="aws-profiles")

    def _on_profile_changed(self, text: str) -> None:
        if text and text != self._profile:
            self._profile = text
            region = aws_cli.profile_region(text)
            if region:
                self._region_combo.setCurrentText(region)
                self._region = region
            self._refresh()

    def _on_region_changed(self, text: str) -> None:
        text = text.strip()
        if text and text != self._region:
            self._region = text
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
        hint = QTreeWidgetItem(self._tree)
        hint.setText(0, text)
        hint.setForeground(0, QColor(tc.get("text_muted")))
        hint.setFlags(Qt.ItemFlag.NoItemFlags)
        hint.setFirstColumnSpanned(True)

    @staticmethod
    def _section_text(title: str, count: int, expanded: bool) -> str:
        return f"{'▾' if expanded else '▸'}  {title} ({count})"

    def _populate_tree(self) -> None:
        self._tree.clear()
        data = self._overview
        errors: dict = data.get("errors", {})
        bold = QFont()
        bold.setBold(True)
        empty: list[str] = []

        for rt in RESOURCE_TYPES:
            rows = data.get(rt.key, [])
            if not rows and rt.key not in errors:
                empty.append(rt.title)
                continue
            expanded = rt.key not in self._collapsed
            root = QTreeWidgetItem(self._tree)
            root.setText(0, self._section_text(rt.title, len(rows), expanded))
            root.setFont(0, bold)
            root.setFirstColumnSpanned(True)
            root.setData(0, Qt.ItemDataRole.UserRole, {"section": rt.key, "title": rt.title})
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
            # Expand only after the children exist.
            root.setExpanded(expanded)

        if empty and self._tree.topLevelItemCount():
            note = QTreeWidgetItem(self._tree)
            note.setText(0, "None here: " + ", ".join(empty))
            note.setToolTip(0, "No resources found for: " + ", ".join(empty))
            note.setForeground(0, QColor(tc.get("text_muted")))
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setFirstColumnSpanned(True)
        elif not self._tree.topLevelItemCount():
            hint = QTreeWidgetItem(self._tree)
            hint.setText(0, "Nothing found in this region for this profile.")
            hint.setForeground(0, QColor(tc.get("text_muted")))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setFirstColumnSpanned(True)
        self._apply_filter(self._filter.text())

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
        item.setText(0, self._section_text(data["title"], count, expanded))

    def _apply_filter(self, text: str) -> None:
        """Hide resources whose name/info don't contain ``text``."""
        needle = text.strip().lower()
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
    ) -> None:
        self._details_title.setText(title)
        self._details.setPlainText("Loading...")
        self._details_request += 1
        request = self._details_request
        profile, region = self._profile, self._region

        def work():
            return aws_cli.run(
                argv, profile=profile, region=region, timeout=60, json_output=json_output
            )

        def done(result):
            if request != self._details_request:
                return
            output, code = result
            text = _pretty(output) if code == 0 else output
            if code == 0 and summarize is not None:
                try:
                    summary = format_summary(summarize(json.loads(output)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    summary = ""
                if summary:
                    text = f"{summary}\n\n── Full output ──\n{text}"
            self._current_details_text = text
            self._details.setPlainText(text or "(no output)")

        def failed(exc: Exception) -> None:
            if request == self._details_request:
                self._details.setPlainText(f"Error: {exc}")

        run_in_thread(work, done, failed, owner=self, name="aws-details")

    def _on_item_selected(self, current, previous) -> None:
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
                lambda: self._show_in_details(
                    f"LOGS — {name}",
                    ["logs", "tail", f"/aws/lambda/{name}", "--since", "1h", "--format", "short"],
                    json_output=False,
                )
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

    def _ec2_action(self, verb: str, instance_id: str, label: str) -> None:
        if not self._confirm(
            f"{label} instance",
            f"{label} EC2 instance {instance_id} in {self._region}?\n\n"
            f"This runs: aws ec2 {verb} --instance-ids {instance_id}",
        ):
            return
        self._show_in_details(
            f"{label.upper()} — {instance_id}", ["ec2", verb, "--instance-ids", instance_id]
        )

    def _ecs_redeploy(self, cluster: str, service: str) -> None:
        if not self._confirm(
            "Force new deployment",
            f"Force a new deployment of {service} in cluster {cluster}?\n\n"
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
            "Invoke Lambda", f"Invoke {name} in {self._region} with that payload?"
        ):
            return
        self._details_title.setText(f"INVOKE — {name}")
        self._details.setPlainText("Invoking...")
        self._details_request += 1
        request = self._details_request
        profile, region = self._profile, self._region

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
        chat.prefill_input(
            f"Here is AWS output ({title}, profile {self._profile or 'default'}, "
            f"region {self._region}). Explain what it shows, flag anything wrong, "
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
