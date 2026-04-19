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
    """Set up the review engine and connect it to the review panel.

    The same engine is also wired into the git panel so the
    "Generate PR description" button can reuse it without standing
    up a second provider manager.
    """
    from polyglot_ai.core.review.review_engine import ReviewEngine

    review_engine = ReviewEngine(provider_manager)
    review = window.review_panel
    review.set_review_engine(review_engine)
    review.set_provider_manager(provider_manager)
    # Give the git panel access to the same engine for PR summary generation.
    if hasattr(window, "git_panel") and hasattr(window.git_panel, "set_review_engine"):
        window.git_panel.set_review_engine(review_engine)
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
    mcp_listener_registered = [False]  # register the listener exactly once

    def _on_project_opened(path: str = "", **kwargs):
        project_path = Path(path)
        file_ops.set_project_root(project_path)
        context_builder.set_project_root(project_path)
        sandbox = Sandbox(project_path)
        tool_registry = ToolRegistry(sandbox, file_ops)
        tool_registry.set_mcp_client(mcp_client)
        tool_registry_holder[0] = tool_registry

        # Combine built-in tools + MCP tools. At this point MCP is not
        # yet connected, so the returned mcp tool list is empty — the
        # connection-change listener below refreshes chat.refresh_mcp_tools
        # once connect_all() finishes, so sequentialthinking and other
        # MCP tools actually become available from the very next message.
        all_tools = tool_registry.get_tool_definitions() + mcp_client.get_tool_definitions()
        chat.set_tools(all_tools, registry=tool_registry)

        # Wire MCP → chat tool-list refresh and sidebar refresh. Register
        # the listener exactly once per app lifetime so reopening projects
        # doesn't stack duplicate callbacks.
        if not mcp_listener_registered[0]:
            from PyQt6.QtCore import QTimer

            def _do_mcp_refresh():
                chat.refresh_mcp_tools(mcp_client)
                try:
                    window.mcp_sidebar.refresh()
                except Exception:
                    logger.exception("Failed to refresh MCP sidebar on connection change")

            def _on_mcp_change():
                # The listener may fire on a non-Qt thread (e.g. a future
                # background reconnect task). QTimer.singleShot(0, ...) is
                # thread-safe and queues the call onto the GUI thread, so
                # the actual widget updates always happen on the right
                # thread regardless of where connect/disconnect ran.
                QTimer.singleShot(0, _do_mcp_refresh)

            mcp_client.add_connection_change_listener(_on_mcp_change)
            mcp_listener_registered[0] = True

        # Connect MCP servers
        safe_task(mcp_client.connect_all(), name="mcp_connect_all")

        # Update panels with project root
        review.set_project_root(path)
        window.changeset_panel.set_project_root(path)
        window.search_panel.set_project_root(project_path)
        window.git_panel.set_project_root(project_path)
        window.cicd_panel.set_project_root(project_path)
        window.test_panel.set_project_root(project_path)
        # Re-scope the task manager so the Tasks sidebar shows tasks
        # for this project (and auto-activates the most recent one).
        try:
            from polyglot_ai.core.task_manager import get_task_manager

            get_task_manager().set_project_root(project_path)
        except Exception:
            logger.exception("ui_wiring: could not switch task manager project root")
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

        # Move the terminal into the new project directory. If a shell
        # is already running, send ``cd`` instead of tearing it down —
        # preserves scrollback, history, and any running processes the
        # user had going. If no shell is running yet, start one fresh.
        terminal = window.terminal_panel
        if terminal._pty and terminal._pty.is_running:
            terminal.cd_to(project_path)
        else:
            terminal.start_terminal(
                event_bus, shell=settings.get("terminal.shell"), cwd=project_path
            )
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


def wire_open_project(window, event_bus, settings=None):
    """Override default open-project action to use ProjectManager.

    When ``settings`` is provided, the last opened project path is
    persisted to ``session.last_project`` so it can be restored on
    the next launch via :func:`restore_last_project`.
    """
    from polyglot_ai.core.project import ProjectManager

    project_manager = ProjectManager(event_bus)

    def _activate_project(path: Path) -> None:
        project_manager.open_project(path)
        window._file_explorer.set_root(path)
        window.setWindowTitle(f"{path.name} — {APP_NAME} v{APP_VERSION}")
        window.statusBar().showMessage(f"Project: {path}")
        if settings is not None:
            safe_task(
                settings.set("session.last_project", str(path)),
                name="save_last_project",
            )

    def _open_project_with_manager():
        from PyQt6.QtWidgets import QFileDialog

        directory = QFileDialog.getExistingDirectory(
            window, "Open Project", "", QFileDialog.Option.ShowDirsOnly
        )
        if directory:
            _activate_project(Path(directory))

    try:
        window._action_open_project.triggered.disconnect()
    except TypeError:
        pass
    window._action_open_project.triggered.connect(_open_project_with_manager)
    # Expose the activator so restore_last_project can reuse it.
    window._activate_project = _activate_project


def restore_last_project(window, settings) -> None:
    """If ``session.last_project`` is set and still exists, open it."""
    last = settings.get("session.last_project")
    if not last:
        return
    path = Path(last)
    if not path.is_dir():
        logger.info("Last project %s no longer exists, skipping restore", path)
        return
    activator = getattr(window, "_activate_project", None)
    if activator is None:
        logger.warning("restore_last_project: no activator wired")
        return
    logger.info("Restoring last project: %s", path)
    activator(path)


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
