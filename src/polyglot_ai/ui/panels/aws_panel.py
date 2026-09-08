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
from polyglot_ai.ui import theme
from polyglot_ai.ui import theme_colors as tc
from polyglot_ai.ui.panels import shared_icons
from polyglot_ai.ui.thread_bridge import run_in_thread
from polyglot_ai.ui.widgets.icon_button import make_icon_button

logger = logging.getLogger(__name__)

_EC2_STATE = {
    "running": "accent_success_muted",
    "pending": "accent_warning",
    "stopping": "accent_warning",
    "stopped": "text_muted",
    "shutting-down": "accent_error",
    "terminated": "accent_error",
}


def _pretty(output: str) -> str:
    """Pretty-print JSON output; pass anything else through."""
    try:
        return json.dumps(json.loads(output), indent=2)
    except (json.JSONDecodeError, TypeError):
        return output


def fetch_overview(profile: str, region: str) -> dict:
    """Collect identity + Lambda/EC2/ECS/S3 summaries. Runs off-thread.

    Each section fails independently: one missing permission must not
    blank the whole panel. Errors are returned per section as hints.
    """
    result: dict = {"identity": None, "lambda": [], "ec2": [], "ecs": [], "s3": [], "errors": {}}

    def call(section: str, argv: list[str], *, global_call: bool = False):
        out, code = aws_cli.run(argv, profile=profile, region="" if global_call else region)
        if code != 0:
            result["errors"][section] = out.removeprefix("Error: ")
            return None
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            result["errors"][section] = "Unexpected (non-JSON) output"
            return None

    ident = call("identity", ["sts", "get-caller-identity"], global_call=True)
    if ident:
        result["identity"] = ident
    else:
        # Without an identity nothing else will work — don't make four
        # more calls that produce the same credentials error.
        return result

    functions = call("lambda", ["lambda", "list-functions", "--max-items", "200"])
    if functions:
        for fn in functions.get("Functions", []):
            result["lambda"].append(
                {
                    "name": fn.get("FunctionName", ""),
                    "runtime": fn.get("Runtime", fn.get("PackageType", "")),
                    "modified": (fn.get("LastModified") or "")[:16].replace("T", " "),
                    "memory": fn.get("MemorySize"),
                }
            )

    ec2 = call("ec2", ["ec2", "describe-instances", "--max-items", "200"])
    if ec2:
        for reservation in ec2.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {t.get("Key"): t.get("Value") for t in inst.get("Tags", [])}
                result["ec2"].append(
                    {
                        "id": inst.get("InstanceId", ""),
                        "name": tags.get("Name", ""),
                        "state": (inst.get("State") or {}).get("Name", "unknown"),
                        "type": inst.get("InstanceType", ""),
                        "ip": inst.get("PublicIpAddress") or inst.get("PrivateIpAddress") or "",
                    }
                )

    clusters = call("ecs", ["ecs", "list-clusters"])
    if clusters:
        for cluster_arn in clusters.get("clusterArns", [])[:10]:
            cluster = cluster_arn.rsplit("/", 1)[-1]
            services = call("ecs", ["ecs", "list-services", "--cluster", cluster])
            arns = (services or {}).get("serviceArns", [])[:10]
            if not arns:
                continue
            described = call(
                "ecs", ["ecs", "describe-services", "--cluster", cluster, "--services", *arns]
            )
            for svc in (described or {}).get("services", []):
                result["ecs"].append(
                    {
                        "cluster": cluster,
                        "name": svc.get("serviceName", ""),
                        "running": svc.get("runningCount", 0),
                        "desired": svc.get("desiredCount", 0),
                        "status": svc.get("status", ""),
                    }
                )

    buckets = call("s3", ["s3api", "list-buckets"], global_call=True)
    if buckets:
        for b in buckets.get("Buckets", []):
            result["s3"].append(
                {"name": b.get("Name", ""), "created": (b.get("CreationDate") or "")[:10]}
            )
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
        self._loaded_once = False
        self._current_details_text = ""

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
        layout.addWidget(self._selector_bar)

        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Resource", "Status", "Info"])
        self._tree.setColumnWidth(0, 200)
        self._tree.setColumnWidth(1, 80)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_item_selected)
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

        self._ai_btn = make_icon_button(nav_icons.make_chat_icon(), "Send these details to the AI")
        self._ai_btn.clicked.connect(self._send_details_to_ai)
        dh.addWidget(self._ai_btn)
        expand_btn = make_icon_button(shared_icons.draw_expand_icon(), "Open in a full window")
        expand_btn.clicked.connect(self._open_details_window)
        dh.addWidget(expand_btn)
        d.addWidget(self._details_header)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
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
        self._details.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 4px; }}"
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
        self._status_label.setText(f"  {profile or 'default'} @ {region} — loading...")

        run_in_thread(
            lambda: fetch_overview(profile, region),
            self._on_overview,
            lambda exc: self._status_label.setText(f"  Error: {exc}"),
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
        counts = (
            f"{len(data['lambda'])} functions | {len(data['ec2'])} instances | "
            f"{len(data['ecs'])} services | {len(data['s3'])} buckets"
        )
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

    def _populate_tree(self) -> None:
        self._tree.clear()
        data = self._overview
        errors: dict = data.get("errors", {})

        def section(title: str, key: str) -> QTreeWidgetItem:
            root = QTreeWidgetItem(self._tree)
            root.setText(0, f"{title} ({len(data.get(key, []))})")
            root.setExpanded(True)
            if key in errors:
                err = QTreeWidgetItem(root)
                err.setText(0, errors[key])
                err.setForeground(0, QColor(tc.get("accent_warning")))
                err.setFlags(Qt.ItemFlag.NoItemFlags)
            return root

        lam = section("Lambda", "lambda")
        for fn in data.get("lambda", []):
            item = QTreeWidgetItem(lam)
            item.setText(0, fn["name"])
            item.setText(1, str(fn.get("runtime") or ""))
            item.setText(2, fn.get("modified", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "lambda", **fn})

        ec2 = section("EC2", "ec2")
        for inst in data.get("ec2", []):
            item = QTreeWidgetItem(ec2)
            label = inst["name"] or inst["id"]
            item.setText(0, label if not inst["name"] else f"{inst['name']}  ({inst['id']})")
            item.setText(1, inst["state"])
            item.setForeground(1, QColor(tc.get(_EC2_STATE.get(inst["state"], "text_muted"))))
            item.setText(2, f"{inst['type']}  {inst['ip']}".strip())
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "ec2", **inst})

        ecs = section("ECS services", "ecs")
        for svc in data.get("ecs", []):
            item = QTreeWidgetItem(ecs)
            item.setText(0, f"{svc['cluster']} / {svc['name']}")
            item.setText(1, f"{svc['running']}/{svc['desired']}")
            healthy = svc["running"] >= svc["desired"] and svc["desired"] > 0
            item.setForeground(
                1, QColor(tc.get("accent_success_muted" if healthy else "accent_warning"))
            )
            item.setText(2, svc["status"])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "ecs", **svc})

        s3 = section("S3 buckets", "s3")
        for b in data.get("s3", []):
            item = QTreeWidgetItem(s3)
            item.setText(0, b["name"])
            item.setText(2, b["created"])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "s3", **b})

        if not any(data.get(k) for k in ("lambda", "ec2", "ecs", "s3")) and not errors:
            hint = QTreeWidgetItem(self._tree)
            hint.setText(0, "Nothing found in this region for this profile.")
            hint.setForeground(0, QColor(tc.get("text_muted")))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)

    # ── Details ─────────────────────────────────────────────────────

    def _show_in_details(self, title: str, argv: list[str], *, json_output: bool = True) -> None:
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
            self._current_details_text = text
            self._details.setPlainText(text or "(no output)")

        run_in_thread(
            work,
            done,
            lambda exc: self._details.setPlainText(f"Error: {exc}"),
            owner=self,
            name="aws-details",
        )

    def _on_item_selected(self, current, previous) -> None:
        if not current:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data["type"]
        if kind == "lambda":
            self._show_in_details(
                f"LAMBDA — {data['name']}",
                ["lambda", "get-function-configuration", "--function-name", data["name"]],
            )
        elif kind == "ec2":
            self._show_in_details(
                f"EC2 — {data['id']}", ["ec2", "describe-instances", "--instance-ids", data["id"]]
            )
        elif kind == "ecs":
            self._show_in_details(
                f"ECS — {data['name']}",
                [
                    "ecs",
                    "describe-services",
                    "--cluster",
                    data["cluster"],
                    "--services",
                    data["name"],
                ],
            )
        elif kind == "s3":
            self._show_in_details(
                f"S3 — {data['name']}",
                ["s3", "ls", f"s3://{data['name']}", "--human-readable", "--summarize"],
                json_output=False,
            )

    # ── Context menu ────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
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
            menu.addSeparator()
            menu.addAction("Force new deployment…").triggered.connect(
                lambda: self._ecs_redeploy(cluster, name)
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
