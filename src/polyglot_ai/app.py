"""Application entry point — bootstrap Qt + asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QLockFile
from PyQt6.QtWidgets import QApplication

from polyglot_ai.constants import APP_NAME, APP_VERSION, DATA_DIR, DB_PATH, LOG_DIR
from polyglot_ai.core.audit import AuditLogger
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.database import Database
from polyglot_ai.core.keyring_store import KeyringStore
from polyglot_ai.core.settings import SettingsManager
from polyglot_ai.ui.bridge_qt import QtBridgeAdapter
from polyglot_ai.ui.main_window import MainWindow
from polyglot_ai.ui.theme import ThemeManager

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "polyglot-ai.log"),
        ],
    )


# ── Bootstrap helpers ────────────────────────────────────────────


def _create_core_services():
    """Create core services: event bus, database, settings, keyring, audit."""
    event_bus = EventBus()
    db = Database(DB_PATH)
    settings = SettingsManager(db)
    keyring_store = KeyringStore()
    audit = AuditLogger(LOG_DIR)
    bridge = QtBridgeAdapter(event_bus)
    return event_bus, db, settings, keyring_store, audit, bridge


def _register_ai_providers(provider_manager, keyring_store, event_bus):
    """Register/unregister AI providers based on current API keys."""

    def _sync_provider(name, key, factory):
        if key:
            existing = provider_manager.get_provider(name)
            if existing:
                existing.update_api_key(key)
            else:
                provider_manager.register(factory(key))
        else:
            provider_manager.unregister(name)

    from polyglot_ai.core.ai.client import OpenAIClient

    _sync_provider("openai", keyring_store.get_key("openai"), lambda k: OpenAIClient(k, event_bus))

    from polyglot_ai.core.ai.anthropic_client import AnthropicClient

    _sync_provider(
        "anthropic", keyring_store.get_key("anthropic"), lambda k: AnthropicClient(k, event_bus)
    )

    from polyglot_ai.core.ai.google_client import GoogleClient

    _sync_provider("google", keyring_store.get_key("google"), lambda k: GoogleClient(k, event_bus))

    from polyglot_ai.core.ai.xai_client import XAIClient

    _sync_provider("xai", keyring_store.get_key("xai"), lambda k: XAIClient(k, event_bus))

    # OpenAI OAuth (subscription login)
    from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient

    openai_oauth = OpenAIOAuthClient(event_bus)
    if openai_oauth.is_authenticated:
        if not provider_manager.get_provider("openai_oauth"):
            provider_manager.register(openai_oauth)
    else:
        provider_manager.unregister("openai_oauth")

    # Claude OAuth (subscription login)
    # NOTE: Disabled — Anthropic's API currently returns "OAuth authentication
    # is currently not supported" on /v1/messages. Claude Code uses an internal
    # routing layer not available to third-party apps. Re-enable when Anthropic
    # opens OAuth access on the public API.
    # from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient
    # claude_oauth = ClaudeOAuthClient(event_bus)
    # if claude_oauth.is_authenticated:
    #     if not provider_manager.get_provider("claude_oauth"):
    #         provider_manager.register(claude_oauth)
    # else:
    #     provider_manager.unregister("claude_oauth")


def _wire_chat_panel(window, db, context_builder, provider_manager, mcp_client):
    """Connect chat panel to AI services."""
    chat = window.chat_panel
    chat.set_database(db)
    chat.set_context_builder(context_builder)
    chat.set_provider_manager(provider_manager)
    chat.set_mcp_client(mcp_client)
    return chat


def _wire_review_panel(window, provider_manager):
    """Set up the review engine and connect it to the review panel."""
    from polyglot_ai.core.review.review_engine import ReviewEngine

    review_engine = ReviewEngine(provider_manager)
    review = window.review_panel
    review.set_review_engine(review_engine)
    review.set_provider_manager(provider_manager)
    return review


def _wire_plan_events(event_bus, plan_panel):
    """Subscribe Plan panel to plan lifecycle events."""

    def _on_plan_step_update(**kwargs):
        plan_panel.update_plan()

    event_bus.subscribe("plan:step_started", _on_plan_step_update)
    event_bus.subscribe("plan:step_completed", _on_plan_step_update)
    event_bus.subscribe("plan:step_failed", _on_plan_step_update)
    event_bus.subscribe("plan:done", _on_plan_step_update)


def _wire_changeset_events(event_bus, changeset):
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


def _wire_project_events(
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
    from polyglot_ai.core.sandbox import Sandbox
    from polyglot_ai.core.ai.tools import ToolRegistry

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
        asyncio.ensure_future(mcp_client.connect_all())

        # Update panels with project root
        review.set_project_root(path)
        window.changeset_panel.set_project_root(path)
        window.search_panel.set_project_root(project_path)
        window.git_panel.set_project_root(project_path)
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
            asyncio.ensure_future(_build_index())

        # Restart terminal in project dir
        terminal = window.terminal_panel
        terminal.stop_terminal()
        terminal.start_terminal(event_bus, shell=settings.get("terminal.shell"), cwd=project_path)
        logger.info("Tools enabled for project: %s", path)

    event_bus.subscribe("project:opened", _on_project_opened)


def _wire_settings_dialog(
    window, settings, keyring_store, mcp_client, provider_manager, chat, theme_manager, event_bus
):
    """Connect settings dialog and related menu actions."""
    from polyglot_ai.ui.dialogs.settings_dialog import SettingsDialog
    from polyglot_ai.ui.dialogs.about_dialog import AboutDialog

    def open_settings():
        dialog = SettingsDialog(settings, keyring_store, window)
        dialog.set_mcp_client(mcp_client)
        if dialog.exec():
            _register_ai_providers(provider_manager, keyring_store, event_bus)
            chat.set_provider_manager(provider_manager)
            asyncio.ensure_future(chat.populate_models())
            theme_manager.apply_theme(settings.get("theme"))

    window._action_settings.triggered.connect(open_settings)
    window._action_about.triggered.connect(lambda: AboutDialog(window).exec())
    window._action_toggle_theme.triggered.connect(lambda: theme_manager.toggle_theme())


def _wire_open_project(window, event_bus):
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


def _run_onboarding(window, settings, keyring_store, provider_manager, event_bus):
    """Show first-run onboarding wizard if needed."""
    if not settings.get("app.onboarding_done"):
        from polyglot_ai.ui.dialogs.onboarding_dialog import OnboardingDialog

        onboarding = OnboardingDialog(window)
        if onboarding.exec():
            if onboarding.api_key:
                keyring_store.store_key("openai", onboarding.api_key)
                _register_ai_providers(provider_manager, keyring_store, event_bus)
            asyncio.ensure_future(settings.set("app.onboarding_done", True))


# ── Main entry point ─────────────────────────────────────────────


def main() -> None:
    setup_logging()
    logger.info("Starting %s", APP_NAME)

    # Migrate legacy data from Codex Desktop if needed
    from polyglot_ai.migration import migrate_legacy_data

    migrate_legacy_data()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("polyglot-ai")

    # Set application icon
    from PyQt6.QtGui import QIcon

    icon_path = Path(__file__).parent / "resources" / "icons" / "polyglot-ai.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Single-instance lock
    lock = QLockFile(str(DATA_DIR / "polyglot-ai.lock"))
    if not lock.tryLock(100):
        logger.warning("Another instance is already running")
        sys.exit(1)

    # Theme (applied after settings load, below)
    theme_manager = ThemeManager(app)

    # Core services
    event_bus, db, settings, keyring_store, audit, bridge = _create_core_services()

    # Async event loop (qasync)
    import qasync

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def init_services() -> None:
        await db.init()
        await settings.load()
        logger.info("Core services initialized")

    loop.run_until_complete(init_services())

    # Load custom fonts if available
    from PyQt6.QtGui import QFontDatabase

    fonts_dir = Path(__file__).parent / "resources" / "fonts"
    if fonts_dir.is_dir():
        for font_file in fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_file))

    # Apply persisted theme (or default to dark)
    theme_manager.apply_theme(settings.get("theme"))

    # AI providers
    from polyglot_ai.core.ai.context import ContextBuilder
    from polyglot_ai.core.ai.provider_manager import ProviderManager
    from polyglot_ai.core.file_ops import FileOperations
    from polyglot_ai.core.indexer import ProjectIndexer
    from polyglot_ai.core.mcp_client import MCPClient, load_mcp_config

    provider_manager = ProviderManager()
    context_builder = ContextBuilder()
    indexer = ProjectIndexer()
    context_builder.set_indexer(indexer)
    _register_ai_providers(provider_manager, keyring_store, event_bus)

    # Main window
    window = MainWindow()
    window.event_bus = event_bus
    window.db = db
    window.settings = settings
    window.keyring_store = keyring_store
    window.audit = audit
    window.bridge = bridge
    window.theme_manager = theme_manager

    # MCP
    mcp_client = MCPClient()
    window._mcp_client = mcp_client
    window._settings = settings
    window._keyring = keyring_store
    for server_cfg in load_mcp_config():
        mcp_client.add_server(server_cfg)

    # File operations
    file_ops = FileOperations(event_bus)

    # Wire panels
    chat = _wire_chat_panel(window, db, context_builder, provider_manager, mcp_client)
    review = _wire_review_panel(window, provider_manager)

    # Provide minimal tools for standalone chat (no project needed)
    from polyglot_ai.core.ai.tools.definitions import TOOL_DEFINITIONS

    standalone_tools = [
        t for t in TOOL_DEFINITIONS if t["function"]["name"] in ("web_search", "create_plan")
    ]
    chat.set_tools(standalone_tools, registry=None)
    window.mcp_sidebar.set_mcp_client(mcp_client)
    window._file_explorer.set_event_bus(event_bus)
    window.git_panel.set_event_bus(event_bus)
    window.usage_panel.set_database(db)
    window.editor_panel.set_ai_services(provider_manager, settings)

    # Wire events
    _wire_plan_events(event_bus, window.plan_panel)
    _wire_changeset_events(event_bus, window.changeset_panel)
    _wire_project_events(
        event_bus,
        window,
        chat,
        review,
        file_ops,
        context_builder,
        mcp_client,
        audit,
        settings,
        indexer=indexer,
    )
    _wire_open_project(window, event_bus)
    _wire_settings_dialog(
        window,
        settings,
        keyring_store,
        mcp_client,
        provider_manager,
        chat,
        theme_manager,
        event_bus,
    )

    # Start terminal
    terminal = window.terminal_panel
    terminal.start_terminal(event_bus, shell=settings.get("terminal.shell"))

    # Show window
    window.show()
    audit.log("app_started")

    # Onboarding
    _run_onboarding(window, settings, keyring_store, provider_manager, event_bus)

    # Restore session
    session_data = {
        "session.open_tabs": settings.get("session.open_tabs"),
        "session.active_tab_index": settings.get("session.active_tab_index"),
        "session.splitter_sizes": settings.get("session.splitter_sizes"),
        "session.window_geometry": settings.get("session.window_geometry"),
    }
    window.restore_session(session_data)

    # Post-show initialization
    async def post_show_init():
        await chat.populate_conversations()
        await chat._init_builtin_templates()
        if provider_manager.has_providers:
            await chat.populate_models()

    asyncio.ensure_future(post_show_init())

    # Run event loop
    try:
        with loop:
            loop.run_forever()

            # Save session before cleanup
            try:
                session = window.save_session()

                async def _save_session():
                    for key, value in session.items():
                        await settings.set(key, value)

                loop.run_until_complete(_save_session())
            except Exception:
                logger.exception("Error saving session")

            # Cleanup while the loop is still active
            terminal.stop_terminal()
            try:
                loop.run_until_complete(mcp_client.disconnect_all())
                loop.run_until_complete(db.close())
            except Exception:
                logger.exception("Error during async cleanup")
    finally:
        lock.unlock()
        logger.info("Application closed")
