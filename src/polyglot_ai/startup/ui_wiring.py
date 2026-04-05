"""UI wiring — connect panels to services and subscribe to events."""

from __future__ import annotations

import logging
from pathlib import Path

from polyglot_ai.constants import APP_NAME, APP_VERSION
from polyglot_ai.core.async_utils import safe_task
from polyglot_ai.startup.services import register_ai_providers

logger = logging.getLogger(__name__)


def wire_chat_panel(window, db, context_builder, provider_manager, mcp_client):
    """Connect chat panel to AI services."""
    chat = window.chat_panel
    chat.set_database(db)
    chat.set_context_builder(context_builder)
    chat.set_provider_manager(provider_manager)
    chat.set_mcp_client(mcp_client)
    return chat


def wire_review_panel(window, provider_manager):
    """Set up the review engine and connect it to the review panel."""
    from polyglot_ai.core.review.review_engine import ReviewEngine

    review_engine = ReviewEngine(provider_manager)
    review = window.review_panel
    review.set_review_engine(review_engine)
    review.set_provider_manager(provider_manager)
    return review


def wire_plan_events(event_bus, plan_panel):
    """Subscribe Plan panel to plan lifecycle events."""

    def _on_plan_step_update(**kwargs):
        plan_panel.update_plan()

    event_bus.subscribe("plan:step_started", _on_plan_step_update)
    event_bus.subscribe("plan:step_completed", _on_plan_step_update)
    event_bus.subscribe("plan:step_failed", _on_plan_step_update)
    event_bus.subscribe("plan:done", _on_plan_step_update)


def wire_changeset_events(event_bus, changeset):
    """Subscribe Changes panel to file change/create events."""

    def _on_file_changed(path: str = "", **kwargs):
        if not changeset.project_root or not path:
            return
        try:
            rel = str(Path(path).relative_to(changeset.project_root))
            current = (
                Path(path).read_text(encoding="utf-8", errors="replace")
                if Path(path).exists()
                else ""
            )
            changeset.update_change(rel, current)
        except (ValueError, OSError):
            pass

    event_bus.subscribe("file:changed", _on_file_changed)
    event_bus.subscribe("file:created", _on_file_changed)


def wire_project_events(
    event_bus,
    window,
    chat,
    review,
    file_ops,
    context_builder,
    mcp_client,
    audit,
    settings,
    indexer=None,
):
    """Wire up everything that happens when a project is opened."""
    from polyglot_ai.core.ai.tools import ToolRegistry
    from polyglot_ai.core.sandbox import Sandbox

    tool_registry_holder = [None]  # mutable container for nonlocal

    def _on_project_opened(path: str = "", **kwargs):
        project_path = Path(path)
        file_ops.set_project_root(project_path)
        context_builder.set_project_root(project_path)
        sandbox = Sandbox(project_path)
        tool_registry = ToolRegistry(sandbox, file_ops)
        tool_registry.set_mcp_client(mcp_client)
        tool_registry_holder[0] = tool_registry

        # Combine built-in tools + MCP tools
        all_tools = tool_registry.get_tool_definitions() + mcp_client.get_tool_definitions()
        chat.set_tools(all_tools, registry=tool_registry)

        # Connect MCP servers
        safe_task(mcp_client.connect_all(), name="mcp_connect_all")

        # Update panels with project root
        review.set_project_root(path)
        window.changeset_panel.set_project_root(path)
        window.search_panel.set_project_root(project_path)
        window.git_panel.set_project_root(project_path)
        window.cicd_panel.set_project_root(project_path)
        window.mcp_sidebar.refresh()

        audit.log("project_opened", {"path": path})

        # Build search index
        if indexer:

            async def _build_index():
                try:
                    await indexer.build_index(project_path)
                    window.statusBar().showMessage(f"Project indexed: {path}")
                    event_bus.emit("index:ready")
                except Exception as ex:
                    logger.warning("Indexing failed: %s", ex)

            window.statusBar().showMessage("Indexing project...")
            safe_task(_build_index(), name="build_index")

        # Restart terminal in project dir
        terminal = window.terminal_panel
        terminal.stop_terminal()
        terminal.start_terminal(event_bus, shell=settings.get("terminal.shell"), cwd=project_path)
        logger.info("Tools enabled for project: %s", path)

    event_bus.subscribe("project:opened", _on_project_opened)


def wire_settings_dialog(
    window, settings, keyring_store, mcp_client, provider_manager, chat, theme_manager, event_bus
):
    """Connect settings dialog and related menu actions."""
    from polyglot_ai.ui.dialogs.about_dialog import AboutDialog
    from polyglot_ai.ui.dialogs.settings_dialog import SettingsDialog

    def open_settings():
        dialog = SettingsDialog(settings, keyring_store, window)
        dialog.set_mcp_client(mcp_client)
        if dialog.exec():
            register_ai_providers(provider_manager, keyring_store, event_bus)
            chat.set_provider_manager(provider_manager)
            safe_task(chat.populate_models(), name="populate_models")
            theme_manager.apply_theme(settings.get("theme"))

    window._action_settings.triggered.connect(open_settings)
    window._action_about.triggered.connect(lambda: AboutDialog(window).exec())
    window._action_toggle_theme.triggered.connect(lambda: theme_manager.toggle_theme())


def wire_open_project(window, event_bus):
    """Override default open-project action to use ProjectManager."""
    from polyglot_ai.core.project import ProjectManager

    project_manager = ProjectManager(event_bus)

    def _open_project_with_manager():
        from PyQt6.QtWidgets import QFileDialog

        directory = QFileDialog.getExistingDirectory(
            window, "Open Project", "", QFileDialog.Option.ShowDirsOnly
        )
        if directory:
            path = Path(directory)
            project_manager.open_project(path)
            window._file_explorer.set_root(path)
            window.setWindowTitle(f"{path.name} — {APP_NAME} v{APP_VERSION}")
            window.statusBar().showMessage(f"Project: {path}")

    try:
        window._action_open_project.triggered.disconnect()
    except TypeError:
        pass
    window._action_open_project.triggered.connect(_open_project_with_manager)


def run_onboarding(window, settings, keyring_store, provider_manager, event_bus):
    """Show first-run onboarding wizard if needed."""
    if not settings.get("app.onboarding_done"):
        from polyglot_ai.ui.dialogs.onboarding_dialog import OnboardingDialog

        onboarding = OnboardingDialog(window)
        if onboarding.exec():
            if onboarding.api_key:
                keyring_store.store_key("openai", onboarding.api_key)
                register_ai_providers(provider_manager, keyring_store, event_bus)
            safe_task(settings.set("app.onboarding_done", True), name="save_onboarding")
