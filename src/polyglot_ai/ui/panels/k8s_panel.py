"""Kubernetes explorer panel — browse pods, deployments, services, and logs."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

_POD_STATUS = {
    "Running": ("🟢", "#4ec9b0"),
    "Succeeded": ("🟢", "#4ec9b0"),
    "Completed": ("🟢", "#4ec9b0"),
    "Pending": ("🟡", "#cca700"),
    "ContainerCreating": ("🟡", "#cca700"),
    "Init": ("🟡", "#cca700"),
    "Terminating": ("🟡", "#cca700"),
    "Failed": ("🔴", "#f44747"),
    "CrashLoopBackOff": ("🔴", "#f44747"),
    "Error": ("🔴", "#f44747"),
    "ImagePullBackOff": ("🔴", "#f44747"),
    "ErrImagePull": ("🔴", "#f44747"),
    "OOMKilled": ("🔴", "#f44747"),
    "Unknown": ("⚪", "#6a6a6a"),
}


class K8sPanel(QWidget):
    """Kubernetes explorer sidebar — browse pods, deployments, services."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kubectl_available: bool | None = None
        self._pods: list[dict] = []
        self._deployments: list[dict] = []
        self._services: list[dict] = []
        self._current_context: str = ""
        self._current_namespace: str = ""

        self._setup_ui()

        # Auto-refresh every 10 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(10_000)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(200, self._refresh_direct)

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("k8sHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#k8sHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("KUBERNETES")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_tertiary')}; letter-spacing: 0.5px; "
            "background: transparent;"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()

        # Refresh button (painted icon)
        refresh_btn = QPushButton()
        refresh_btn.setObjectName("k8sRefresh")
        refresh_btn.setFixedSize(22, 22)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        r_pixmap = QPixmap(16, 16)
        r_pixmap.fill(QColor(0, 0, 0, 0))
        rp = QPainter(r_pixmap)
        rp_pen = QPen(QColor("#aaaaaa"))
        rp_pen.setWidthF(1.5)
        rp.setPen(rp_pen)
        rp.drawArc(3, 3, 10, 10, 30 * 16, 300 * 16)
        # Arrow head
        rp.drawLine(11, 3, 13, 5)
        rp.drawLine(11, 3, 9, 5)
        rp.end()
        refresh_btn.setIcon(QIcon(r_pixmap))
        refresh_btn.setStyleSheet(
            "#k8sRefresh { background: transparent; border: none; }"
            "#k8sRefresh:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        refresh_btn.clicked.connect(self._refresh_direct)
        h_layout.addWidget(refresh_btn)

        layout.addWidget(header)

        # Context + namespace selectors
        selector_bar = QWidget()
        selector_bar.setObjectName("k8sSelectorBar")
        selector_bar.setStyleSheet(f"#k8sSelectorBar {{ background: {tc.get('bg_base')}; }}")
        sel_layout = QVBoxLayout(selector_bar)
        sel_layout.setContentsMargins(8, 4, 8, 4)
        sel_layout.setSpacing(4)

        combo_style = (
            f"QComboBox {{ background: {tc.get('bg_input')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; border-radius: 3px; "
            f"padding: 3px 8px; font-size: {tc.FONT_SM}px; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; "
            f"border-right: 4px solid transparent; border-top: 5px solid {tc.get('text_secondary')}; "
            f"margin-right: 6px; }}"
        )

        # Context selector
        ctx_row = QHBoxLayout()
        ctx_label = QLabel("Cluster:")
        ctx_label.setFixedWidth(60)
        ctx_label.setStyleSheet(f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;")
        ctx_row.addWidget(ctx_label)
        self._ctx_combo = QComboBox()
        self._ctx_combo.setStyleSheet(combo_style)
        self._ctx_combo.currentTextChanged.connect(self._on_context_changed)
        ctx_row.addWidget(self._ctx_combo, stretch=1)
        sel_layout.addLayout(ctx_row)

        # Namespace selector
        ns_row = QHBoxLayout()
        ns_label = QLabel("Namespace:")
        ns_label.setFixedWidth(60)
        ns_label.setStyleSheet(f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px;")
        ns_row.addWidget(ns_label)
        self._ns_combo = QComboBox()
        self._ns_combo.setStyleSheet(combo_style)
        self._ns_combo.addItem("All Namespaces")
        self._ns_combo.currentTextChanged.connect(self._on_namespace_changed)
        ns_row.addWidget(self._ns_combo, stretch=1)
        sel_layout.addLayout(ns_row)

        layout.addWidget(selector_bar)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )

        tree_style = (
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 2px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 3px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )

        # Resource tree
        self._resource_tree = QTreeWidget()
        self._resource_tree.setHeaderLabels(["Resource", "Status", "Info"])
        self._resource_tree.setColumnWidth(0, 180)
        self._resource_tree.setColumnWidth(1, 80)
        self._resource_tree.setStyleSheet(tree_style)
        self._resource_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._resource_tree.customContextMenuRequested.connect(self._show_context_menu)
        self._resource_tree.currentItemChanged.connect(self._on_item_selected)
        splitter.addWidget(self._resource_tree)

        # Details/logs viewer
        details_widget = QWidget()
        d_layout = QVBoxLayout(details_widget)
        d_layout.setContentsMargins(0, 0, 0, 0)
        d_layout.setSpacing(0)

        details_header = QWidget()
        details_header.setObjectName("k8sDetailsHeader")
        details_header.setFixedHeight(28)
        details_header.setStyleSheet(
            f"#k8sDetailsHeader {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
        )
        dh_layout = QHBoxLayout(details_header)
        dh_layout.setContentsMargins(8, 0, 8, 0)

        self._details_title = QLabel("DETAILS")
        self._details_title.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; background: transparent;"
        )
        dh_layout.addWidget(self._details_title)
        dh_layout.addStretch()

        # Expand button
        expand_btn = QPushButton()
        expand_btn.setObjectName("k8sExpand")
        expand_btn.setFixedSize(20, 20)
        expand_btn.setToolTip("Open in full window")
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exp_pixmap = QPixmap(16, 16)
        exp_pixmap.fill(QColor(0, 0, 0, 0))
        ep = QPainter(exp_pixmap)
        ep_pen = QPen(QColor("#aaaaaa"))
        ep_pen.setWidthF(1.5)
        ep.setPen(ep_pen)
        ep.drawLine(9, 2, 14, 2)
        ep.drawLine(14, 2, 14, 7)
        ep.drawLine(14, 2, 8, 8)
        ep.drawLine(2, 9, 2, 14)
        ep.drawLine(2, 14, 7, 14)
        ep.drawLine(2, 14, 8, 8)
        ep.end()
        expand_btn.setIcon(QIcon(exp_pixmap))
        expand_btn.setStyleSheet(
            "#k8sExpand { background: transparent; border: none; }"
            "#k8sExpand:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        expand_btn.clicked.connect(self._open_details_window)
        dh_layout.addWidget(expand_btn)

        d_layout.addWidget(details_header)

        self._details_viewer = QPlainTextEdit()
        self._details_viewer.setReadOnly(True)
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._details_viewer.setFont(mono)
        self._details_viewer.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 4px; }}"
        )
        self._details_viewer.setPlaceholderText("Click a resource to view details")
        d_layout.addWidget(self._details_viewer)

        splitter.addWidget(details_widget)
        splitter.setSizes([300, 150])

        layout.addWidget(splitter)

        # Status bar
        self._status_label = QLabel("  Kubernetes: checking...")
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px;"
        )
        layout.addWidget(self._status_label)

    # ── kubectl execution ───────────────────────────────────────────

    def _check_kubectl(self) -> bool:
        if self._kubectl_available is None:
            self._kubectl_available = shutil.which("kubectl") is not None
        return self._kubectl_available

    def _run_kubectl(self, args: list[str]) -> tuple[str, int]:
        if not self._check_kubectl():
            return "kubectl not found. Install from https://kubernetes.io/docs/tasks/tools/", 1
        try:
            cmd = ["kubectl"]
            if self._current_context:
                cmd.extend(["--context", self._current_context])
            cmd.extend(args)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1

    # ── Data fetching ───────────────────────────────────────────────

    def _refresh_direct(self) -> None:
        """Direct synchronous refresh — for button click and showEvent.

        Loads contexts (fast, local config only) synchronously,
        then fetches resources in a background thread.
        """
        if not self._check_kubectl():
            self._status_label.setText("  kubectl not found")
            return

        # Contexts — reads local ~/.kube/config, instant
        ctx_output, ctx_code = self._run_kubectl(["config", "get-contexts", "-o", "name"])
        contexts = (
            [c.strip() for c in ctx_output.splitlines() if c.strip()] if ctx_code == 0 else []
        )
        current_ctx, _ = self._run_kubectl(["config", "current-context"])
        current_ctx = current_ctx.strip()

        self._ctx_combo.blockSignals(True)
        self._ctx_combo.clear()
        for ctx in contexts:
            self._ctx_combo.addItem(ctx)
        if current_ctx in contexts:
            self._ctx_combo.setCurrentText(current_ctx)
            self._current_context = current_ctx
        self._ctx_combo.blockSignals(False)

        self._status_label.setText(f"  {current_ctx} | loading resources...")

        # Fetch resources in background (these hit the cluster API)
        self._refresh()

    def _refresh(self) -> None:
        """Background refresh — fetches resources in a thread."""
        import threading

        if not self._check_kubectl():
            return

        def do_fetch():
            try:
                self._fetch_data()
                QTimer.singleShot(0, self._populate_tree)
            except Exception:
                logger.exception("K8s refresh failed")

        threading.Thread(target=do_fetch, daemon=True).start()

    def _fetch_data(self) -> None:
        ns_args = ["-A"] if not self._current_namespace else ["-n", self._current_namespace]

        # Pods
        output, code = self._run_kubectl(["get", "pods", *ns_args, "-o", "json"])
        self._pods = self._parse_items(output) if code == 0 else []

        # Deployments
        output, code = self._run_kubectl(["get", "deployments", *ns_args, "-o", "json"])
        self._deployments = self._parse_items(output) if code == 0 else []

        # Services
        output, code = self._run_kubectl(["get", "services", *ns_args, "-o", "json"])
        self._services = self._parse_items(output) if code == 0 else []

    @staticmethod
    def _parse_items(output: str) -> list[dict]:
        try:
            data = json.loads(output)
            return data.get("items", [])
        except json.JSONDecodeError:
            return []

    def _populate_tree(self) -> None:
        self._resource_tree.clear()

        # Pods section
        pods_root = QTreeWidgetItem(self._resource_tree)
        pods_root.setText(0, f"📦 Pods ({len(self._pods)})")
        pods_root.setExpanded(True)

        for pod in self._pods:
            meta = pod.get("metadata", {})
            status = pod.get("status", {})
            phase = status.get("phase", "Unknown")

            # Get container status for more detail
            container_statuses = status.get("containerStatuses", [])
            restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)
            display_status = phase
            for cs in container_statuses:
                waiting = cs.get("waiting", {})
                if waiting.get("reason"):
                    display_status = waiting["reason"]
                    break

            icon, color = _POD_STATUS.get(display_status, _POD_STATUS.get(phase, ("⚪", "#6a6a6a")))

            item = QTreeWidgetItem(pods_root)
            name = meta.get("name", "")
            ns = meta.get("namespace", "")
            item.setText(0, f"{icon} {name}")
            item.setText(1, display_status)
            item.setText(2, f"ns:{ns} restarts:{restarts}")
            item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "pod", "name": name, "namespace": ns}
            )

        # Deployments section
        deps_root = QTreeWidgetItem(self._resource_tree)
        deps_root.setText(0, f"🚀 Deployments ({len(self._deployments)})")
        deps_root.setExpanded(True)

        for dep in self._deployments:
            meta = dep.get("metadata", {})
            spec = dep.get("spec", {})
            status = dep.get("status", {})
            ready = status.get("readyReplicas", 0)
            desired = spec.get("replicas", 0)

            icon = "🟢" if ready == desired and desired > 0 else "🟡"

            item = QTreeWidgetItem(deps_root)
            name = meta.get("name", "")
            ns = meta.get("namespace", "")
            item.setText(0, f"{icon} {name}")
            item.setText(1, f"{ready}/{desired} ready")
            item.setText(2, f"ns:{ns}")
            item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "deployment", "name": name, "namespace": ns}
            )

        # Services section
        svc_root = QTreeWidgetItem(self._resource_tree)
        svc_root.setText(0, f"🌐 Services ({len(self._services)})")
        svc_root.setExpanded(True)

        for svc in self._services:
            meta = svc.get("metadata", {})
            spec = svc.get("spec", {})
            svc_type = spec.get("type", "ClusterIP")
            ports = spec.get("ports", [])
            port_str = ", ".join(f"{p.get('port')}" for p in ports[:3])

            item = QTreeWidgetItem(svc_root)
            name = meta.get("name", "")
            ns = meta.get("namespace", "")
            item.setText(0, f"  {name}")
            item.setText(1, svc_type)
            item.setText(2, f"ns:{ns} ports:{port_str}")
            item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "service", "name": name, "namespace": ns}
            )

        # Update status bar
        ctx = self._current_context or "none"
        self._status_label.setText(
            f"  {ctx} | {len(self._pods)} pods | "
            f"{len(self._deployments)} deployments | {len(self._services)} services"
        )

    # ── Event handlers ──────────────────────────────────────────────

    def _on_context_changed(self, text: str) -> None:
        if text and text != self._current_context:
            self._current_context = text
            self._refresh()

    def _on_namespace_changed(self, text: str) -> None:
        new_ns = "" if text == "All Namespaces" else text
        if new_ns != self._current_namespace:
            self._current_namespace = new_ns
            self._refresh()

    def _on_item_selected(self, current, previous) -> None:
        if not current:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data or not isinstance(data, dict):
            return

        res_type = data["type"]
        name = data["name"]
        ns = data["namespace"]

        if res_type == "pod":
            self._details_title.setText(f"LOGS — {name}")
            output, code = self._run_kubectl(
                ["logs", name, "-n", ns, "--tail=200", "--all-containers"]
            )
            if code == 0:
                self._details_viewer.setPlainText(output or "(no logs)")
            else:
                self._details_viewer.setPlainText(f"Error: {output[:500]}")
        else:
            self._details_title.setText(f"DESCRIBE — {name}")
            output, code = self._run_kubectl(["describe", res_type, name, "-n", ns])
            if code == 0:
                self._details_viewer.setPlainText(output)
            else:
                self._details_viewer.setPlainText(f"Error: {output[:500]}")

    # ── Context menu ────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        item = self._resource_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or not isinstance(data, dict):
            return

        res_type = data["type"]
        name = data["name"]
        ns = data["namespace"]

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; font-size: {tc.FONT_SM}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        if res_type == "pod":
            logs_action = menu.addAction("📋 View Logs")
            logs_action.triggered.connect(lambda: self._view_resource(data))
            describe_action = menu.addAction("🔍 Describe")
            describe_action.triggered.connect(lambda: self._describe_resource(res_type, name, ns))
            menu.addSeparator()
            delete_action = menu.addAction("🗑 Delete Pod")
            delete_action.triggered.connect(lambda: self._delete_resource(res_type, name, ns))

        elif res_type == "deployment":
            describe_action = menu.addAction("🔍 Describe")
            describe_action.triggered.connect(lambda: self._describe_resource(res_type, name, ns))
            restart_action = menu.addAction("⟳ Restart Rollout")
            restart_action.triggered.connect(lambda: self._restart_deployment(name, ns))
            menu.addSeparator()
            scale_action = menu.addAction("📊 Scale")
            scale_action.triggered.connect(lambda: self._scale_deployment(name, ns))

        elif res_type == "service":
            describe_action = menu.addAction("🔍 Describe")
            describe_action.triggered.connect(lambda: self._describe_resource(res_type, name, ns))

        menu.exec(self._resource_tree.viewport().mapToGlobal(pos))

    def _view_resource(self, data: dict) -> None:
        self._on_item_selected(self._resource_tree.currentItem(), None)

    def _describe_resource(self, res_type: str, name: str, ns: str) -> None:
        self._details_title.setText(f"DESCRIBE — {name}")
        output, code = self._run_kubectl(["describe", res_type, name, "-n", ns])
        self._details_viewer.setPlainText(output if code == 0 else f"Error: {output[:500]}")

    def _delete_resource(self, res_type: str, name: str, ns: str) -> None:
        reply = QMessageBox.question(
            self,
            f"Delete {res_type.title()}",
            f"Are you sure you want to delete {res_type} '{name}' in namespace '{ns}'?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        output, code = self._run_kubectl(["delete", res_type, name, "-n", ns])
        if code == 0:
            self._status_label.setText(f"  Deleted {res_type} {name}")
            self._refresh()
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _restart_deployment(self, name: str, ns: str) -> None:
        reply = QMessageBox.question(
            self,
            "Restart Deployment",
            f"Restart rollout for deployment '{name}' in namespace '{ns}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        output, code = self._run_kubectl(["rollout", "restart", f"deployment/{name}", "-n", ns])
        if code == 0:
            self._status_label.setText(f"  Restarted {name}")
            self._refresh()
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _scale_deployment(self, name: str, ns: str) -> None:
        from PyQt6.QtWidgets import QInputDialog

        replicas, ok = QInputDialog.getInt(
            self, "Scale Deployment", f"Number of replicas for '{name}':", 1, 0, 100
        )
        if not ok:
            return
        reply = QMessageBox.question(
            self,
            "Scale Deployment",
            f"Scale deployment '{name}' to {replicas} replicas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        output, code = self._run_kubectl(
            ["scale", f"deployment/{name}", f"--replicas={replicas}", "-n", ns]
        )
        if code == 0:
            self._status_label.setText(f"  Scaled {name} to {replicas}")
            self._refresh()
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    # ── Expand to full window ───────────────────────────────────────

    def _open_details_window(self) -> None:
        title = self._details_title.text()
        content = self._details_viewer.toPlainText()
        if not content or content == "Click a resource to view details":
            return
        dialog = _K8sDetailsWindow(title, content, self)
        dialog.show()


class _K8sDetailsWindow(QWidget):
    """Standalone resizable window for viewing K8s logs/describe output."""

    def __init__(self, title: str, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(900, 600)
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("k8sWinHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#k8sWinHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 12, 0)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(title_label)
        h_layout.addStretch()

        line_count = content.count("\n") + 1
        count_label = QLabel(f"{line_count:,} lines")
        count_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        h_layout.addWidget(count_label)

        layout.addWidget(header)

        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        mono = QFont("Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        viewer.setFont(mono)
        viewer.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 8px; }}"
        )
        viewer.setPlainText(content)
        viewer.moveCursor(viewer.textCursor().MoveOperation.End)
        layout.addWidget(viewer)

        self.setStyleSheet(f"background: {tc.get('bg_base')};")
