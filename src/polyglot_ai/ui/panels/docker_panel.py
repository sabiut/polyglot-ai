"""Docker panel — manage containers and images."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
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

    def showEvent(self, event) -> None:
        """Refresh data when the panel becomes visible."""
        super().showEvent(event)
        QTimer.singleShot(100, self._refresh_sync)

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("dockerHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#dockerHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
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

        refresh_btn = QPushButton("⟳ Refresh")
        refresh_btn.setObjectName("dockerRefresh")
        refresh_btn.setFixedHeight(22)
        refresh_btn.setToolTip("Refresh containers and images")
        refresh_btn.setStyleSheet(
            f"#dockerRefresh {{ background: {tc.get('accent_primary')}; "
            f"color: {tc.get('text_on_accent')}; border: none; border-radius: 3px; "
            f"padding: 0 8px; font-size: {tc.FONT_XS}px; font-weight: 600; }}"
            f"#dockerRefresh:hover {{ background: {tc.get('accent_primary_hover')}; }}"
        )
        refresh_btn.clicked.connect(self._refresh_sync)
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
        self._container_tree.currentItemChanged.connect(self._on_container_selected)
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
        self._image_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_tree.customContextMenuRequested.connect(self._show_image_menu)
        self._image_tree.itemClicked.connect(self._on_image_clicked)
        upper_layout.addWidget(self._image_tree)

        splitter.addWidget(upper)

        # Log viewer
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QWidget()
        log_header.setObjectName("dockerLogHeader")
        log_header.setFixedHeight(28)
        log_header.setStyleSheet(
            f"#dockerLogHeader {{ background: {tc.get('bg_surface')}; "
            f"border-top: 1px solid {tc.get('border_secondary')}; }}"
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

        expand_btn = QPushButton()
        expand_btn.setObjectName("dockerExpandLogs")
        expand_btn.setFixedSize(20, 20)
        expand_btn.setToolTip("Open logs in full window")
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Draw a white expand icon using a painted arrow
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(0, 0, 0, 0))
        p = QPainter(pixmap)
        pen = QPen(QColor("#aaaaaa"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        # Top-right arrow
        p.drawLine(9, 2, 14, 2)
        p.drawLine(14, 2, 14, 7)
        p.drawLine(14, 2, 8, 8)
        # Bottom-left arrow
        p.drawLine(2, 9, 2, 14)
        p.drawLine(2, 14, 7, 14)
        p.drawLine(2, 14, 8, 8)
        p.end()
        expand_btn.setIcon(QIcon(pixmap))
        expand_btn.setStyleSheet(
            "#dockerExpandLogs { background: transparent; border: none; }"
            "#dockerExpandLogs:hover { background: rgba(255,255,255,0.1); border-radius: 3px; }"
        )
        expand_btn.clicked.connect(self._open_log_window)
        lh_layout.addWidget(expand_btn)

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
            output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
            logger.debug(
                "docker %s → code=%d, %d bytes", " ".join(args), result.returncode, len(output)
            )
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1

    def _refresh(self) -> None:
        """Fetch docker data in background thread, update UI on main thread."""

        def do_fetch():
            try:
                c_out, c_code = self._run_docker(["ps", "-a", "--format", "{{json .}}"])
                i_out, i_code = self._run_docker(["images", "--format", "{{json .}}"])
                # Marshal back to main thread
                QTimer.singleShot(0, lambda: self._on_data_loaded(c_out, c_code, i_out, i_code))
            except Exception:
                logger.exception("Docker refresh failed")
                QTimer.singleShot(
                    0, lambda: self._status_label.setText("  Error refreshing Docker")
                )

        threading.Thread(target=do_fetch, daemon=True).start()

    def _refresh_sync(self) -> None:
        """Synchronous refresh — called from Refresh button click."""
        c_out, c_code = self._run_docker(["ps", "-a", "--format", "{{json .}}"])
        i_out, i_code = self._run_docker(["images", "--format", "{{json .}}"])
        self._on_data_loaded(c_out, c_code, i_out, i_code)

    def _on_data_loaded(
        self, containers_out: str, c_code: int, images_out: str, i_code: int
    ) -> None:
        logger.info(
            "Docker data loaded: containers(code=%d, %d bytes) images(code=%d, %d bytes)",
            c_code,
            len(containers_out),
            i_code,
            len(images_out),
        )
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
            item.setData(0, Qt.ItemDataRole.UserRole, image)

    def _on_container_selected(self, current, previous) -> None:
        """Auto-load logs when a container is clicked."""
        if not current:
            return
        container = current.data(0, Qt.ItemDataRole.UserRole)
        if not container:
            return
        name = container.get("Names", "")
        if name:
            self._view_logs(name)

    # ── Context Menus ───────────────────────────────────────────────

    def _on_image_clicked(self, item, column) -> None:
        """Show context menu on left-click too."""
        rect = self._image_tree.visualItemRect(item)
        pos = rect.bottomLeft()
        self._show_image_menu(pos)

    def _show_image_menu(self, pos) -> None:
        item = self._image_tree.itemAt(pos)
        if not item:
            return
        image = item.data(0, Qt.ItemDataRole.UserRole)
        if not image:
            return

        repo = image.get("Repository", "<none>")
        tag = image.get("Tag", "<none>")
        image_id = image.get("ID", "")
        image_ref = f"{repo}:{tag}" if repo != "<none>" else image_id

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {tc.get('bg_surface')}; color: {tc.get('text_primary')}; "
            f"border: 1px solid {tc.get('border_card')}; font-size: {tc.FONT_SM}px; }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background: {tc.get('bg_active')}; }}"
        )

        # Copy image name
        copy_action = menu.addAction("📋 Copy Image Name")
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(image_ref))

        # Inspect
        inspect_action = menu.addAction("🔍 Inspect")
        inspect_action.triggered.connect(lambda: self._inspect_image(image_ref))

        # Run container from image
        run_action = menu.addAction("▶ Run Container")
        run_action.triggered.connect(lambda: self._run_image(image_ref))

        menu.addSeparator()

        # Delete
        delete_action = menu.addAction("🗑 Delete Image")
        delete_action.triggered.connect(lambda: self._delete_image(image_ref))

        menu.exec(self._image_tree.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
            self._status_label.setText(f"  Copied: {text}")

    def _inspect_image(self, image_ref: str) -> None:
        output, code = self._run_docker(["image", "inspect", image_ref, "--format", "{{json .}}"])
        if code == 0:
            try:
                data = json.loads(output)
                formatted = json.dumps(data, indent=2)
            except json.JSONDecodeError:
                formatted = output
            self._log_title.setText(f"INSPECT — {image_ref}")
            self._log_viewer.setPlainText(formatted)
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _run_image(self, image_ref: str) -> None:
        reply = QMessageBox.question(
            self,
            "Run Container",
            f"Run a new container from '{image_ref}'?\n\nThis will run: docker run -d <image>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        output, code = self._run_docker(["run", "-d", image_ref])
        if code == 0:
            self._status_label.setText(f"  Started container from {image_ref}")
            self._refresh_sync()
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _delete_image(self, image_ref: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Image",
            f"Are you sure you want to delete image '{image_ref}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        output, code = self._run_docker(["rmi", image_ref])
        if code == 0:
            self._status_label.setText(f"  Deleted {image_ref}")
            self._refresh_sync()
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

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
        output, code = self._run_docker([action, name])
        self._on_action_done(action, name, output, code)

    def _on_action_done(self, action: str, name: str, output: str, code: int) -> None:
        if code == 0:
            self._status_label.setText(f"  {action.title()}ed {name}")
            self._refresh()  # Reload container list
        else:
            self._status_label.setText(f"  Error: {output[:60]}")

    def _view_logs(self, name: str) -> None:
        self._log_title.setText(f"LOGS — {name}")
        self._log_viewer.setPlainText("Loading logs...")
        # Docker logs writes to both stdout AND stderr — capture both
        output, code = self._run_docker_logs(name)
        self._on_logs_loaded(output, code)

    def _run_docker_logs(self, name: str) -> tuple[str, int]:
        """Fetch container logs — combines stdout + stderr since Docker
        separates container stdout/stderr into the two streams."""
        if not self._check_docker():
            return "Docker not found.", 1
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "200", name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Combine both streams — container logs go to stderr
            output = result.stdout + result.stderr
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1

    def _on_logs_loaded(self, output: str, code: int) -> None:
        if code != 0:
            self._log_viewer.setPlainText(f"Error fetching logs: {output[:500]}")
        else:
            if len(output) > 50_000:
                output = output[:50_000] + "\n\n... (log truncated)"
            self._log_viewer.setPlainText(output or "(no logs)")

    def _open_log_window(self) -> None:
        """Open logs in a resizable standalone window."""
        title = self._log_title.text()
        content = self._log_viewer.toPlainText()
        if not content or content == "Right-click a container → View Logs":
            return

        # Fetch more lines for the full window (1000 instead of 200)
        container_name = title.replace("LOGS — ", "") if "—" in title else ""
        if container_name:
            output, code = self._run_docker_logs_full(container_name)
            if code == 0 and output:
                content = output

        dialog = _LogViewerDialog(title, content, self)
        dialog.show()

    def _run_docker_logs_full(self, name: str) -> tuple[str, int]:
        """Fetch more container logs for the full log window."""
        if not self._check_docker():
            return "Docker not found.", 1
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "1000", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr
            return output.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", 1
        except Exception as exc:
            return f"Error: {exc}", 1


class _LogViewerDialog(QWidget):
    """Standalone resizable window for viewing container logs."""

    def __init__(self, title: str, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(900, 600)
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("logDialogHeader")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"#logDialogHeader {{ background: {tc.get('bg_surface')}; "
            f"border-bottom: 1px solid {tc.get('border_secondary')}; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: {tc.FONT_SM}px; font-weight: 600; "
            f"color: {tc.get('text_heading')}; background: transparent;"
        )
        h_layout.addWidget(title_label)
        h_layout.addStretch()

        # Line count
        line_count = content.count("\n") + 1
        count_label = QLabel(f"{line_count:,} lines")
        count_label.setStyleSheet(
            f"color: {tc.get('text_muted')}; font-size: {tc.FONT_XS}px; background: transparent;"
        )
        h_layout.addWidget(count_label)

        layout.addWidget(header)

        # Log content
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
        # Scroll to bottom
        viewer.moveCursor(viewer.textCursor().MoveOperation.End)
        layout.addWidget(viewer)

        self.setStyleSheet(f"background: {tc.get('bg_base')};")
