"""Docker panel — manage containers and images."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from polyglot_ai.ui import theme_colors as tc

logger = logging.getLogger(__name__)

_CONTAINER_STATUS = {
    "running": ("🟢", "#4ec9b0"),
    "exited": ("🔴", "#f44747"),
    "created": ("⚪", "#6a6a6a"),
    "paused": ("🟡", "#cca700"),
    "restarting": ("🟡", "#cca700"),
    "removing": ("🔴", "#f44747"),
    "dead": ("🔴", "#f44747"),
}


class DockerPanel(QWidget):
    """Docker container and image management sidebar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._docker_available: bool | None = None
        self._containers: list[dict] = []
        self._images: list[dict] = []

        self._setup_ui()

        # Auto-refresh every 10 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(10_000)

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            f"background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')};"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("DOCKER")
        title.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_tertiary')}; letter-spacing: 0.5px; "
            "background: transparent;"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()

        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(24, 24)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {tc.get('border_card')}; "
            f"border-radius: 3px; color: {tc.get('text_primary')}; font-size: 14px; }}"
            f"QPushButton:hover {{ background: {tc.get('bg_hover')}; }}"
        )
        refresh_btn.clicked.connect(self._refresh)
        h_layout.addWidget(refresh_btn)

        layout.addWidget(header)

        # Main splitter: containers + images | logs
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {tc.get('border_secondary')}; height: 2px; }}"
        )

        # Upper area: containers + images
        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(0)

        tree_style = (
            f"QTreeWidget {{ background: {tc.get('bg_base')}; color: {tc.get('text_primary')}; "
            f"border: none; font-size: {tc.FONT_SM}px; }}"
            f"QTreeWidget::item {{ padding: 2px; }}"
            f"QTreeWidget::item:selected {{ background: {tc.get('bg_active')}; }}"
            f"QHeaderView::section {{ background: {tc.get('bg_surface')}; "
            f"color: {tc.get('text_heading')}; border: 1px solid {tc.get('border_secondary')}; "
            f"padding: 3px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
        )

        # Containers section header
        cont_header = QLabel("  CONTAINERS")
        cont_header.setFixedHeight(22)
        cont_header.setStyleSheet(
            f"background: {tc.get('bg_surface')}; color: {tc.get('text_tertiary')}; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; letter-spacing: 0.5px;"
        )
        upper_layout.addWidget(cont_header)

        self._container_tree = QTreeWidget()
        self._container_tree.setHeaderLabels(["", "Name", "Image", "Status", "Ports"])
        self._container_tree.setColumnWidth(0, 26)
        self._container_tree.setColumnWidth(1, 120)
        self._container_tree.setColumnWidth(2, 140)
        self._container_tree.setColumnWidth(3, 80)
        self._container_tree.setStyleSheet(tree_style)
        self._container_tree.setRootIsDecorated(False)
        self._container_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._container_tree.customContextMenuRequested.connect(self._show_container_menu)
        upper_layout.addWidget(self._container_tree)

        # Images section header
        img_header = QLabel("  IMAGES")
        img_header.setFixedHeight(22)
        img_header.setStyleSheet(
            f"background: {tc.get('bg_surface')}; color: {tc.get('text_tertiary')}; "
            f"font-size: {tc.FONT_XS}px; font-weight: 600; letter-spacing: 0.5px; "
            f"border-top: 1px solid {tc.get('border_secondary')};"
        )
        upper_layout.addWidget(img_header)

        self._image_tree = QTreeWidget()
        self._image_tree.setHeaderLabels(["Repository", "Tag", "Size"])
        self._image_tree.setColumnWidth(0, 180)
        self._image_tree.setColumnWidth(1, 80)
        self._image_tree.setStyleSheet(tree_style)
        self._image_tree.setRootIsDecorated(False)
        upper_layout.addWidget(self._image_tree)

        splitter.addWidget(upper)

        # Log viewer
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QWidget()
        log_header.setFixedHeight(26)
        log_header.setStyleSheet(
            f"background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')};"
        )
        lh_layout = QHBoxLayout(log_header)
        lh_layout.setContentsMargins(8, 0, 8, 0)

        self._log_title = QLabel("LOGS")
        self._log_title.setStyleSheet(
            f"color: {tc.get('text_tertiary')}; font-size: {tc.FONT_XS}px; "
            "font-weight: 600; background: transparent;"
        )
        lh_layout.addWidget(self._log_title)
        lh_layout.addStretch()

        log_layout.addWidget(log_header)

        self._log_viewer = QPlainTextEdit()
        self._log_viewer.setReadOnly(True)
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log_viewer.setFont(mono)
        self._log_viewer.setStyleSheet(
            f"QPlainTextEdit {{ background: {tc.get('bg_base')}; "
            f"color: {tc.get('text_primary')}; border: none; padding: 4px; }}"
        )
        self._log_viewer.setPlaceholderText("Right-click a container → View Logs")
        log_layout.addWidget(self._log_viewer)

        splitter.addWidget(log_widget)
        splitter.setSizes([300, 150])

        layout.addWidget(splitter)

        # Status bar
        self._status_label = QLabel("  Docker: checking...")
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet(
            f"font-size: {tc.FONT_XS}px; color: {tc.get('text_muted')}; "
            f"background: {tc.get('bg_surface')}; padding-left: 8px;"
        )
        layout.addWidget(self._status_label)

    # ── Data Fetching ───────────────────────────────────────────────

    def _check_docker(self) -> bool:
        if self._docker_available is None:
            self._docker_available = shutil.which("docker") is not None
        return self._docker_available

    def _run_docker(self, args: list[str]) -> tuple[str, int]:
        if not self._check_docker():
            return "Docker not found. Install from https://docs.docker.com/get-docker/", 1
        try:
            result = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1

    def _refresh(self) -> None:
        def do_fetch():
            containers_out, c_code = self._run_docker(["ps", "-a", "--format", "{{json .}}"])
            images_out, i_code = self._run_docker(["images", "--format", "{{json .}}"])
            QTimer.singleShot(
                0, lambda: self._on_data_loaded(containers_out, c_code, images_out, i_code)
            )

        threading.Thread(target=do_fetch, daemon=True).start()

    def _on_data_loaded(
        self, containers_out: str, c_code: int, images_out: str, i_code: int
    ) -> None:
        if c_code != 0 and not self._check_docker():
            self._status_label.setText("  Docker not available")
            return

        # Parse containers
        self._containers = []
        if c_code == 0:
            for line in containers_out.splitlines():
                line = line.strip()
                if line:
                    try:
                        self._containers.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Parse images
        self._images = []
        if i_code == 0:
            for line in images_out.splitlines():
                line = line.strip()
                if line:
                    try:
                        self._images.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        self._populate_containers()
        self._populate_images()

        running = sum(1 for c in self._containers if "Up" in c.get("Status", ""))
        total = len(self._containers)
        self._status_label.setText(
            f"  Docker: {running} running / {total} total | {len(self._images)} images"
        )

    def _populate_containers(self) -> None:
        self._container_tree.clear()
        for container in self._containers:
            state = container.get("State", "unknown")
            icon, color = _CONTAINER_STATUS.get(state, ("⚪", "#6a6a6a"))

            item = QTreeWidgetItem(self._container_tree)
            item.setText(0, icon)
            item.setText(1, container.get("Names", ""))
            item.setText(2, container.get("Image", ""))
            item.setText(3, container.get("Status", ""))
            item.setText(4, container.get("Ports", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, container)

    def _populate_images(self) -> None:
        self._image_tree.clear()
        for image in self._images:
            item = QTreeWidgetItem(self._image_tree)
            item.setText(0, image.get("Repository", "<none>"))
            item.setText(1, image.get("Tag", "<none>"))
            item.setText(2, image.get("Size", ""))

    # ── Context Menu ────────────────────────────────────────────────

    def _show_container_menu(self, pos) -> None:
        item = self._container_tree.itemAt(pos)
        if not item:
            return

        container = item.data(0, Qt.ItemDataRole.UserRole)
        if not container:
            return

        name = container.get("Names", "")
        state = container.get("State", "")

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; font-size: {tc.FONT_SM}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        if state == "running":
            stop_action = menu.addAction("■ Stop")
            stop_action.triggered.connect(lambda: self._container_action("stop", name))
            restart_action = menu.addAction("⟳ Restart")
            restart_action.triggered.connect(lambda: self._container_action("restart", name))
        else:
            start_action = menu.addAction("▶ Start")
            start_action.triggered.connect(lambda: self._container_action("start", name))

        menu.addSeparator()
        logs_action = menu.addAction("📋 View Logs")
        logs_action.triggered.connect(lambda: self._view_logs(name))

        menu.exec(self._container_tree.viewport().mapToGlobal(pos))

    def _container_action(self, action: str, name: str) -> None:
        reply = QMessageBox.question(
            self,
            f"Docker {action.title()}",
            f"Are you sure you want to {action} container '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._status_label.setText(f"  {action.title()}ing {name}...")

        def do_action():
            output, code = self._run_docker([action, name])
            QTimer.singleShot(0, lambda: self._on_action_done(action, name, output, code))

        threading.Thread(target=do_action, daemon=True).start()

    def _on_action_done(self, action: str, name: str, output: str, code: int) -> None:
        if code == 0:
            self._status_label.setText(f"  {action.title()}ed {name}")
            self._refresh()  # Reload container list
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _view_logs(self, name: str) -> None:
        self._log_title.setText(f"LOGS — {name}")
        self._log_viewer.setPlainText("Loading logs...")

        def do_fetch():
            output, code = self._run_docker(["logs", "--tail", "200", name])
            QTimer.singleShot(0, lambda: self._on_logs_loaded(output, code))

        threading.Thread(target=do_fetch, daemon=True).start()

    def _on_logs_loaded(self, output: str, code: int) -> None:
        if code != 0:
            self._log_viewer.setPlainText(f"Error fetching logs: {output[:500]}")
        else:
            if len(output) > 50_000:
                output = output[:50_000] + "\n\n... (log truncated)"
            self._log_viewer.setPlainText(output or "(no logs)")
