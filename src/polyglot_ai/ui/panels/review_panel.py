"""Code Review panel — shows diff review findings in a structured UI."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polyglot_ai.core.review.models import ReviewFinding, ReviewResult

logger = logging.getLogger(__name__)

_SEVERITY_COLORS = {
    "critical": "#f44747",
    "high": "#e5a00d",
    "medium": "#569cd6",
    "low": "#4ec9b0",
    "info": "#888888",
}

_SEVERITY_LABELS = {
    "critical": "🔴 Critical",
    "high": "🟠 High",
    "medium": "🔵 Medium",
    "low": "🟢 Low",
    "info": "ℹ️ Info",
}

_CATEGORY_ICONS = {
    "bug": "🐛",
    "security": "🔒",
    "performance": "⚡",
    "maintainability": "🔧",
    "style": "🎨",
    "tests": "🧪",
    "logic": "🧠",
    "error_handling": "⚠️",
    "other": "📝",
}


class ReviewPanel(QWidget):
    """Panel showing code review results with structured findings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._review_engine = None
        self._provider_manager = None
        self._project_root: str = ""
        self._current_result: ReviewResult | None = None
        # Set later via set_event_bus() once init_task_manager has run.
        self._task_manager = None

        self.setStyleSheet("background-color: #1e1e1e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header bar ──
        header = QWidget()
        header.setFixedHeight(44)
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #333;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("CODE REVIEW")
        title.setStyleSheet("font-size: 11px; font-weight: bold; color: #888; letter-spacing: 1px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Review mode selector — styled dropdown
        from polyglot_ai.ui.widgets.styled_combo import StyledComboBox

        self._mode_combo = StyledComboBox()
        self._mode_combo.addItemWithDesc("Working Changes", "Review unstaged modifications")
        self._mode_combo.addItemWithDesc("Staged Changes", "Review what will be committed")
        self._mode_combo.addItemWithDesc("Branch vs Main", "Review all commits on this branch")
        self._mode_combo.addItemWithDesc(
            "🔍 Terraform Security", "Scan .tf files for cloud security issues"
        )
        self._mode_combo.addItemWithDesc(
            "🔍 Kubernetes Security", "Scan K8s manifests for pod security issues"
        )
        self._mode_combo.addItemWithDesc(
            "🔍 Dockerfile Security", "Scan Dockerfiles for container security issues"
        )
        self._mode_combo.addItemWithDesc(
            "🔍 Docker Compose Security",
            "Scan docker-compose files for misconfig and secrets",
        )
        self._mode_combo.addItemWithDesc(
            "🔍 Helm Chart Security", "Scan Helm templates and values for security issues"
        )
        self._mode_combo.addItemWithDesc(
            "🎨 Frontend Design Audit",
            "Audit UI components, styles, and tokens for hierarchy, accessibility, and polish",
        )
        self._mode_combo.setFixedWidth(220)
        self._mode_combo.setFixedHeight(30)
        header_layout.addWidget(self._mode_combo)

        # Run review button
        self._run_btn = QPushButton("▶ Run Review")
        self._run_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4; color: white; font-weight: 600;
                padding: 5px 14px; border: none; border-radius: 5px; font-size: 12px;
            }
            QPushButton:hover { background-color: #1a8ae8; }
            QPushButton:disabled { background-color: #3e3e40; color: #666; }
        """)
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run_review)
        header_layout.addWidget(self._run_btn)

        layout.addWidget(header)

        # ── Content area (scrollable) ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background-color: #1e1e1e; }
            QScrollBar:vertical {
                background: #1e1e1e; width: 8px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #444; min-height: 30px; border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self._content = QWidget()
        self._content.setStyleSheet("background-color: #1e1e1e;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(20, 20, 20, 20)
        self._content_layout.setSpacing(10)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Welcome state
        welcome_card = QWidget()
        welcome_card.setStyleSheet("""
            QWidget {
                background-color: #252526; border: 1px solid #333;
                border-radius: 8px;
            }
        """)
        wc_layout = QVBoxLayout(welcome_card)
        wc_layout.setContentsMargins(20, 20, 20, 20)

        welcome_title = QLabel("🔍 Code Review")
        welcome_title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #e0e0e0; "
            "background: transparent; border: none;"
        )
        wc_layout.addWidget(welcome_title)

        welcome_desc = QLabel(
            "Select a review mode above and click Run Review to analyze your code changes."
        )
        welcome_desc.setStyleSheet(
            "font-size: 13px; color: #aaa; margin-top: 4px; background: transparent; border: none;"
        )
        welcome_desc.setWordWrap(True)
        wc_layout.addWidget(welcome_desc)

        # Mode descriptions
        modes_text = QLabel(
            "<div style='margin-top: 12px;'>"
            "<div style='color: #d4d4d4; margin: 6px 0;'>"
            "  <b style='color: #569cd6;'>Working Changes</b> — review unstaged modifications</div>"
            "<div style='color: #d4d4d4; margin: 6px 0;'>"
            "  <b style='color: #569cd6;'>Staged Changes</b> — review what will be committed</div>"
            "<div style='color: #d4d4d4; margin: 6px 0;'>"
            "  <b style='color: #569cd6;'>Branch vs Main</b> — review all commits on this branch</div>"
            "</div>"
        )
        modes_text.setStyleSheet("font-size: 13px; background: transparent; border: none;")
        modes_text.setTextFormat(Qt.TextFormat.RichText)
        modes_text.setWordWrap(True)
        wc_layout.addWidget(modes_text)

        self._content_layout.addWidget(welcome_card)
        self._content_layout.addStretch()

        scroll.setWidget(self._content)
        layout.addWidget(scroll)

    def set_review_engine(self, engine) -> None:
        self._review_engine = engine

    def set_provider_manager(self, pm) -> None:
        self._provider_manager = pm

    def set_project_root(self, path: str) -> None:
        self._project_root = path

    def set_event_bus(self, event_bus) -> None:
        """Subscribe to task lifecycle events.

        When the active task changes and has a branch, default the
        review mode dropdown to "Branch vs Main" so the natural
        workflow ("review what I've done on this task") is one click.
        """
        from polyglot_ai.core.task_manager import EVT_TASK_CHANGED, get_task_manager

        self._task_manager = get_task_manager()

        def _on_task_changed(task=None, **_):
            if task is not None and getattr(task, "branch", None):
                # 2 = "Branch vs Main" — keep in sync with the
                # mode_combo.addItemWithDesc() ordering above.
                try:
                    self._mode_combo.setCurrentIndex(2)
                except Exception:
                    logger.debug("review_panel: could not preset mode for task", exc_info=True)

        event_bus.subscribe(EVT_TASK_CHANGED, _on_task_changed)

    def _on_run_review(self) -> None:
        if not self._project_root:
            self._show_message("Open a project first.", "#f44747")
            return
        if not self._review_engine:
            self._show_message("Review engine not available.", "#f44747")
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText("Reviewing...")
        from polyglot_ai.core.async_utils import safe_task

        safe_task(self._run_review(), name="run_review")

    async def _run_review(self) -> None:
        from polyglot_ai.core.review.review_engine import collect_iac_files, get_git_diff

        idx = self._mode_combo.currentIndex()
        diff_modes = {0: "working", 1: "staged", 2: "branch"}
        iac_modes = {
            3: "terraform",
            4: "kubernetes",
            5: "dockerfile",
            6: "docker_compose",
            7: "helm",
            8: "frontend_design",
        }

        # Get current model from parent chat panel if possible. If this
        # fails we fall back to the provider manager's default — log so
        # the user isn't silently switched to a different model.
        model_id = ""
        window = self.window()
        if hasattr(window, "chat_panel"):
            try:
                model_id, _ = window.chat_panel._get_selected_model()
            except Exception:
                logger.warning(
                    "Review: failed to read selected model from chat panel; "
                    "falling back to provider default",
                    exc_info=True,
                )

        # Shared across branches so we can publish one snapshot after
        # the result lands, regardless of diff vs IaC path.
        review_mode_label: str = "unknown"
        files_scanned: list[str] = []

        if idx in diff_modes:
            # Existing diff review path
            mode = diff_modes[idx]
            self._clear_results()
            self._show_message("Getting git diff...", "#888")

            diff_text = await get_git_diff(self._project_root, mode)
            if not diff_text.strip():
                self._clear_results()
                self._show_message("No changes found. Your working tree is clean.", "#4ec9b0")
                self._run_btn.setEnabled(True)
                self._run_btn.setText("▶ Run Review")
                return

            self._clear_results()
            self._show_message("Analyzing changes with AI...", "#569cd6")
            result = await self._review_engine.review_diff(diff_text, model_id=model_id)
            review_mode_label = mode
            # Diff modes embed file info in the diff itself — listing
            # files_scanned separately would be noisy and redundant.
            files_scanned = []
        else:
            # IaC / frontend review path. ``iac_mode`` is the engine
            # key (e.g. ``docker_compose``); ``mode_pretty`` is the
            # user-facing string we drop into status messages so the
            # underscore-y enum doesn't leak into the UI.
            iac_mode = iac_modes.get(idx, "terraform")
            mode_pretty = {
                "terraform": "Terraform",
                "kubernetes": "Kubernetes",
                "dockerfile": "Dockerfile",
                "docker_compose": "Docker Compose",
                "helm": "Helm chart",
                "frontend_design": "frontend",
            }.get(iac_mode, iac_mode)
            self._clear_results()
            self._show_message(f"Scanning for {mode_pretty} files...", "#888")

            files = collect_iac_files(self._project_root, iac_mode)
            if not files:
                self._clear_results()
                self._show_message(
                    f"No {mode_pretty} files found in this project.", "#cca700"
                )
                self._run_btn.setEnabled(True)
                self._run_btn.setText("▶ Run Review")
                return

            self._clear_results()
            self._show_message(
                f"Analyzing {len(files)} {mode_pretty} file(s) with AI...", "#569cd6"
            )
            result = await self._review_engine.review_content(files, iac_mode, model_id=model_id)
            review_mode_label = iac_mode
            files_scanned = sorted(files.keys())

        self._current_result = result

        self._clear_results()
        self._display_results(result)

        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶ Run Review")

        # Record on the active task so the timeline reflects the review
        # and the AI's system prompt knows the latest review outcome.
        self._record_review_on_task(result)

        # Publish the review snapshot to panel_state so the AI chat
        # can see it — both as a compact block in the system prompt
        # and via the get_review_findings tool for drill-down. See
        # polyglot_ai/core/panel_state.py for the contract.
        try:
            from polyglot_ai.core import panel_state
            from polyglot_ai.core.review.snapshot import build_review_snapshot

            snapshot = build_review_snapshot(result, review_mode_label, files_scanned)
            panel_state.set_last_review(snapshot)
        except Exception:
            logger.exception("review_panel: failed to publish review snapshot")
            self._show_message(
                "Review complete, but AI integration failed. "
                "The chat may not see these findings.",
                "#cca700",
            )

    def _record_review_on_task(self, result) -> None:
        if self._task_manager is None or self._task_manager.active is None:
            return
        try:
            findings = list(getattr(result, "findings", []) or [])
            critical = sum(
                1
                for f in findings
                if str(getattr(getattr(f, "severity", ""), "value", "")) == "critical"
            )
            high = sum(
                1
                for f in findings
                if str(getattr(getattr(f, "severity", ""), "value", "")) == "high"
            )
            status = getattr(result, "status", "ok")
            if status == "failed":
                text = "Review failed"
                kind = "review_failed"
            elif not findings:
                text = "Review clean — no findings"
                kind = "review_clean"
            else:
                text = f"Review: {len(findings)} finding(s)"
                if critical:
                    text += f" · {critical} critical"
                if high:
                    text += f" · {high} high"
                kind = "review_findings"
            self._task_manager.add_note(
                kind,
                text,
                data={
                    "findings": len(findings),
                    "critical": critical,
                    "high": high,
                    "status": status,
                },
            )
        except Exception:
            logger.exception("review_panel: could not record review on task")

    def _clear_results(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_message(self, text: str, color: str) -> None:
        self._clear_results()
        label = QLabel(text)
        label.setStyleSheet(f"color: {color}; font-size: 13px; padding: 20px;")
        label.setWordWrap(True)
        self._content_layout.addWidget(label)

    def _display_results(self, result: ReviewResult) -> None:
        """Render the review results as cards."""
        # If the review failed, render a red error card instead of the
        # normal summary — failure must never look like a clean scan.
        if result.status == "failed":
            self._display_error(result)
            return

        # ── Summary card ──
        summary_card = QWidget()
        border = "#f44747" if result.status == "failed" else "#333"
        summary_card.setStyleSheet(
            "QWidget {"
            " background-color: #252526;"
            f" border: 1px solid {border};"
            " border-radius: 8px;"
            "}"
        )
        sc_layout = QVBoxLayout(summary_card)
        sc_layout.setContentsMargins(14, 12, 14, 12)

        # Stats row
        stats = QHBoxLayout()
        stats.addWidget(self._stat_badge(f"{result.files_reviewed} files", "#888"))
        stats.addWidget(self._stat_badge(f"+{result.total_additions}", "#4ec9b0"))
        stats.addWidget(self._stat_badge(f"-{result.total_deletions}", "#f44747"))
        stats.addWidget(self._stat_badge(f"{len(result.findings)} findings", "#569cd6"))

        if result.critical_count:
            stats.addWidget(self._stat_badge(f"{result.critical_count} critical", "#f44747"))
        if result.high_count:
            stats.addWidget(self._stat_badge(f"{result.high_count} high", "#e5a00d"))

        stats.addStretch()
        if result.model:
            model_lbl = QLabel(result.model)
            model_lbl.setStyleSheet("font-size: 10px; color: #666;")
            stats.addWidget(model_lbl)
        sc_layout.addLayout(stats)

        # Summary text
        summary_text = QLabel(result.summary)
        summary_text.setWordWrap(True)
        summary_text.setStyleSheet("color: #d4d4d4; font-size: 13px; margin-top: 6px;")
        sc_layout.addWidget(summary_text)

        self._content_layout.addWidget(summary_card)

        # Truncation warning
        if getattr(result, "truncated_files", None):
            warn = QLabel(
                f"⚠ {len(result.truncated_files)} file(s) were truncated or "
                "skipped because the review hit size limits. Consider splitting "
                "or narrowing the scan."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "color: #e5a00d; font-size: 12px; padding: 8px 12px; "
                "background: #2a2416; border: 1px solid #4a3a1a; border-radius: 4px;"
            )
            self._content_layout.addWidget(warn)

        if not result.findings:
            ok_label = QLabel("✅ No issues found — the changes look good!")
            ok_label.setStyleSheet("color: #4ec9b0; font-size: 14px; padding: 16px;")
            self._content_layout.addWidget(ok_label)
            return

        # ── Finding cards ──
        for finding in result.findings:
            self._content_layout.addWidget(self._create_finding_card(finding))

    def _display_error(self, result: ReviewResult) -> None:
        """Render a review failure as a distinct red error card."""
        card = QWidget()
        card.setStyleSheet(
            "QWidget { background-color: #2a1717; border: 1px solid #f44747; border-radius: 8px;}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)

        title = QLabel("🔴 Review failed")
        title.setStyleSheet(
            "color: #f44747; font-size: 14px; font-weight: bold; "
            "background: transparent; border: none;"
        )
        layout.addWidget(title)

        msg = QLabel(result.summary or "The review did not complete.")
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #e0d0d0; font-size: 12px; background: transparent; border: none;")
        layout.addWidget(msg)

        if result.error:
            detail = QLabel(result.error)
            detail.setWordWrap(True)
            detail.setStyleSheet(
                "color: #b08080; font-size: 11px; font-family: monospace; "
                "background: transparent; border: none; margin-top: 4px;"
            )
            layout.addWidget(detail)

        self._content_layout.addWidget(card)

        self._content_layout.addStretch()

    def _stat_badge(self, text: str, color: str) -> QLabel:
        badge = QLabel(text)
        badge.setStyleSheet(
            f"background: {color}22; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 3px 8px; border-radius: 4px; "
            f"border: 1px solid {color}44;"
        )
        return badge

    def _create_finding_card(self, finding: ReviewFinding) -> QWidget:
        """Create a card for a single review finding."""
        card = QWidget()
        severity_color = _SEVERITY_COLORS.get(finding.severity.value, "#888")
        card.setStyleSheet(f"""
            QWidget {{
                background-color: #252526;
                border: 1px solid #333;
                border-left: 3px solid {severity_color};
                border-radius: 6px;
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Top row: severity badge + category + title
        top = QHBoxLayout()
        top.setSpacing(6)

        sev_label = QLabel(_SEVERITY_LABELS.get(finding.severity.value, finding.severity.value))
        sev_label.setStyleSheet(
            f"font-size: 11px; font-weight: bold; color: {severity_color}; "
            "background: transparent; border: none;"
        )
        top.addWidget(sev_label)

        cat_icon = _CATEGORY_ICONS.get(finding.category.value, "📝")
        cat_label = QLabel(f"{cat_icon} {finding.category.value}")
        cat_label.setStyleSheet(
            "font-size: 11px; color: #888; background: transparent; border: none;"
        )
        top.addWidget(cat_label)

        top.addStretch()

        file_label = QLabel(f"📄 {finding.file}:{finding.line}")
        file_label.setStyleSheet(
            "font-size: 11px; color: #569cd6; background: transparent; border: none;"
        )
        file_label.setCursor(Qt.CursorShape.PointingHandCursor)
        top.addWidget(file_label)

        layout.addLayout(top)

        # Title
        title = QLabel(finding.title)
        title.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #e0e0e0; "
            "background: transparent; border: none;"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        # Body
        body = QLabel(finding.body)
        body.setStyleSheet(
            "font-size: 12px; color: #b0b0b0; background: transparent; border: none;"
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        # Suggestion
        if finding.suggestion:
            suggestion = QLabel(f"💡 Suggestion:\n{finding.suggestion}")
            suggestion.setStyleSheet(
                "font-size: 12px; color: #4ec9b0; background: #1a2e2a; "
                "border: 1px solid #2a4a3a; border-radius: 4px; "
                "padding: 6px 8px; font-family: monospace; "
            )
            suggestion.setWordWrap(True)
            layout.addWidget(suggestion)

        return card
