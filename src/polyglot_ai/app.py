"""Application entry point — bootstrap Qt + asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QLockFile
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from polyglot_ai.constants import APP_NAME, DATA_DIR, LOG_DIR
from polyglot_ai.ui.main_window import MainWindow
from polyglot_ai.ui.theme import ThemeManager

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "polyglot-ai.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path),
        ],
    )
    # Restrict log file permissions — logs may contain sensitive paths/errors
    try:
        log_path.chmod(0o600)
    except OSError:
        pass
    _install_excepthook(log_path)


# Single-instance helpers live in startup/single_instance.py so the
# unit tests can exercise them without dragging the full UI tree
# into their import chain. ``app.main`` calls them via thin wrappers.


def _install_excepthook(log_path: "Path") -> None:
    """Route unhandled exceptions to the log + stderr.

    Without this, a crash in a Qt event handler or a background
    task vanishes into Python's default ``sys.excepthook`` and the
    user is left with no clue what went wrong. By writing every
    unhandled exception to the same log file the rest of the app
    uses, bug reports become "attach this file" rather than "try
    to reproduce in a terminal".
    """

    def _log_unhandled(exc_type, exc_value, exc_tb) -> None:
        # KeyboardInterrupt is the user — let it terminate normally.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Unhandled exception — see %s", log_path, exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_unhandled


def main() -> None:
    setup_logging()
    logger.info("Starting %s", APP_NAME)

    # Pre-flight: verify Qt is loadable and a display is reachable
    # *before* QApplication is created. Without this, a missing
    # platform plugin or a headless session produces an unhelpful
    # ``qFatal`` dump instead of an actionable error message.
    from polyglot_ai.startup.preflight import run_preflight

    run_preflight()

    # Migrate legacy data from Codex Desktop if needed
    from polyglot_ai.migration import migrate_legacy_data

    migrate_legacy_data()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Platform integration (desktop files, icons, Wayland)
    from polyglot_ai.startup.platform import setup_platform

    icon_path = setup_platform()

    # Set the desktop file name *before* QApplication is constructed.
    # On Wayland the QApplication ctor registers an XDG portal app
    # ID immediately, and once that's registered with a default ID
    # ("python3" / argv[0]), Qt logs:
    #
    #     Failed to register with host portal — Connection already
    #     associated with an application ID
    #
    # …and the launcher / dock can't match the running window back
    # to ``polyglot-ai.desktop``. ``QGuiApplication.setDesktopFileName``
    # is a static method that latches the name into the registration
    # used by the upcoming QApplication. Set it here.
    QGuiApplication.setDesktopFileName("polyglot-ai")

    # Required for ``QtWebEngineWidgets`` (used by the Claude
    # subscription web panel). Qt enforces that this attribute is
    # set *before* ``QApplication`` is constructed; importing
    # ``QWebEngineView`` later otherwise raises::
    #
    #     QtWebEngineWidgets must be imported or
    #     Qt.AA_ShareOpenGLContexts must be set before a
    #     QCoreApplication instance is created
    #
    # The attribute is harmless when QtWebEngine isn't installed —
    # it just configures GL context sharing for any widgets that
    # request it — so we set it unconditionally rather than
    # gating on whether the optional dependency is available.
    from PyQt6.QtCore import Qt

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Qt application
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if icon_path:
        from PyQt6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon_path)))

    # Single-instance lock.
    #
    # ``QLockFile`` writes the running app's PID into the lock file
    # and removes it on clean exit. If the previous run *crashed*
    # (segfault, kill -9, power loss), the file is left behind and
    # — by default — Qt refuses to acquire the lock again forever.
    # The user sees the app silently fail to launch with no window
    # and no actionable error, until they discover the stale file
    # under ``~/.local/share/polyglot-ai/polyglot-ai.lock`` and
    # delete it manually.
    #
    # ``setStaleLockTime(0)`` tells Qt to verify the PID in the lock
    # file is still alive before refusing; if the process is dead,
    # the stale lock is removed and we proceed normally. This is the
    # single-line fix for "the app won't launch after a crash".
    lock_path = str(DATA_DIR / "polyglot-ai.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        # Qt's ``setStaleLockTime(0)`` only checks "is this PID
        # alive". It can't tell whether the live PID belongs to
        # *us* or to some unrelated long-lived process (sshd,
        # systemd unit, terminal multiplexer) that Linux happened
        # to recycle the PID for. The result, hit by real users:
        # the lock file claims PID 12345 is "Polyglot AI", PID
        # 12345 is actually nginx, and the user is permanently
        # locked out.
        #
        # Manual fallback: read the recorded PID, verify its
        # cmdline mentions polyglot-ai, otherwise treat the lock
        # as stale-with-PID-reuse and retry once.
        from polyglot_ai.startup.single_instance import (
            lock_owner_is_unrelated,
            notify_already_running,
        )

        if lock_owner_is_unrelated(lock):
            logger.info("Stale lock at %s belongs to an unrelated PID — clearing", lock_path)
            lock.removeStaleLockFile()
            if lock.tryLock(100):
                logger.info("Lock acquired after clearing stale PID-reuse lock")
            else:
                logger.warning("Another instance is already running (lock at %s)", lock_path)
                notify_already_running(app, lock_path)
                sys.exit(1)
        else:
            logger.warning("Another instance is already running (lock at %s)", lock_path)
            notify_already_running(app, lock_path)
            sys.exit(1)

    theme_manager = ThemeManager(app)

    # Core services
    from polyglot_ai.startup.services import create_core_services, register_ai_providers

    event_bus, db, settings, keyring_store, audit, bridge = create_core_services()

    # Async event loop (qasync)
    import qasync

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Silence a known-harmless shutdown race between anyio and qasync.
    # When MCP stdio servers disconnect, anyio schedules CancelScope
    # ._deliver_cancellation callbacks on the next loop tick. If the
    # loop closes before they run we get:
    #   RuntimeError: no running event loop
    # …from inside _deliver_cancellation. The process is already
    # exiting, nothing leaks — the traceback is just noise on stderr.
    # We install a targeted exception handler that swallows *only*
    # that specific combination; any other loop exception still goes
    # to the default handler.
    def _quiet_anyio_shutdown_noise(loop_obj, context):
        exc = context.get("exception")
        msg = context.get("message", "")
        if (
            isinstance(exc, RuntimeError)
            and "no running event loop" in str(exc)
            and "_deliver_cancellation" in msg
        ):
            logger.debug("Suppressed harmless anyio shutdown race: %s", msg)
            return
        loop_obj.default_exception_handler(context)

    loop.set_exception_handler(_quiet_anyio_shutdown_noise)

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
    register_ai_providers(provider_manager, keyring_store, event_bus)

    # Main window
    window = MainWindow()
    window.event_bus = event_bus
    window.db = db
    window.settings = settings
    window.keyring_store = keyring_store
    window.audit = audit
    window.bridge = bridge
    window.theme_manager = theme_manager

    # Notifications: wire here so the Notifier exists before any panel
    # has a chance to emit. ``install_notifications`` attaches the
    # toast manager + tray hook to ``window`` and connects the
    # delivery callback. Failure here must not block app startup —
    # the worst case is "no notifications", not a crashed window.
    try:
        from polyglot_ai.startup.notifications_setup import install_notifications

        install_notifications(window, event_bus, settings)
    except Exception:  # pragma: no cover — best-effort wiring
        import logging

        logging.getLogger(__name__).exception(
            "Notification system failed to install — continuing without it"
        )

    # MCP
    mcp_client = MCPClient()
    window._mcp_client = mcp_client
    window._settings = settings
    window._keyring = keyring_store
    for server_cfg in load_mcp_config():
        mcp_client.add_server(server_cfg)

    file_ops = FileOperations(event_bus)

    # Wire panels and events
    from polyglot_ai.startup.ui_wiring import (
        restore_last_project,
        run_onboarding,
        wire_changeset_events,
        wire_chat_panel,
        wire_open_project,
        wire_plan_events,
        wire_project_events,
        wire_review_panel,
        wire_settings_dialog,
    )

    chat = wire_chat_panel(window, db, context_builder, provider_manager, mcp_client)
    review = wire_review_panel(window, provider_manager)

    # Minimal tools for standalone chat (no project needed)
    from polyglot_ai.core.ai.tools import ToolRegistry
    from polyglot_ai.core.ai.tools.definitions import TOOL_DEFINITIONS

    # Tools available without a project open: web/plan + docker/k8s/db
    _STANDALONE_NAMES = {
        "web_search",
        "create_plan",
        # Docker read-only
        "docker_list_containers",
        "docker_list_images",
        "docker_container_logs",
        "docker_inspect",
        # Docker mutating (require approval)
        "docker_restart",
        "docker_stop",
        "docker_start",
        "docker_remove",
        # Kubernetes read-only
        "k8s_current_context",
        "k8s_list_pods",
        "k8s_list_deployments",
        "k8s_list_services",
        "k8s_pod_logs",
        "k8s_describe",
        # Kubernetes mutating (require approval)
        "k8s_delete_pod",
        "k8s_restart_deployment",
        "k8s_scale_deployment",
        "k8s_apply",
        # Database
        "db_list_connections",
        "db_get_schema",
        "db_query",
        "db_execute",
    }
    standalone_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in _STANDALONE_NAMES]
    standalone_registry = ToolRegistry()
    chat.set_tools(standalone_tools, registry=standalone_registry)
    window.mcp_sidebar.set_mcp_client(mcp_client)
    window.database_panel.set_mcp_client(mcp_client)
    window._file_explorer.set_event_bus(event_bus)

    # Initialise the TaskManager singleton with the shared event bus
    # BEFORE any panel calls set_event_bus(). Panels like git_panel and
    # test_panel fetch the singleton during their set_event_bus() to
    # record activity on the active task — if init_task_manager hasn't
    # run yet they'd grab a manager with no event bus wired and every
    # write would silently fail to propagate.
    from polyglot_ai.core.plan_generator import PlanGenerator
    from polyglot_ai.core.task_manager import init_task_manager

    task_manager = init_task_manager(event_bus)
    # Inject the AI plan generator so the task detail dialog can ask
    # the configured provider to draft a checklist for FEATURE tasks.
    # The generator is loose-typed on the manager so this module is
    # the only place that knows about the AI layer.
    task_manager.set_plan_generator(PlanGenerator(provider_manager))

    window.git_panel.set_event_bus(event_bus)
    window.test_panel.set_event_bus(event_bus)

    window.tasks_panel.set_task_manager(task_manager)
    window.tasks_panel.set_event_bus(event_bus)
    # Today landing page — same wiring pattern. It reads the task
    # manager for active tasks and listens for project:opened to
    # kick off the gh-based attention fetch.
    window.today_panel.set_task_manager(task_manager)
    window.today_panel.set_event_bus(event_bus)
    # Chat panel re-scopes per task: switches conversations and
    # injects task context into the system prompt.
    chat.set_event_bus(event_bus)
    # Review panel: defaults to Branch vs Main when the active task
    # has a branch, and records each review on the task timeline.
    try:
        window.review_panel.set_event_bus(event_bus)
    except Exception:
        logger.exception("app: could not wire review panel to event bus")
    # CI/CD panel: filters runs by the active task's branch and
    # records the latest CI status onto the task.
    try:
        window.cicd_panel.set_event_bus(event_bus)
    except Exception:
        logger.exception("app: could not wire CI/CD panel to event bus")
    window.usage_panel.set_database(db)
    window.editor_panel.set_ai_services(provider_manager, settings)

    wire_plan_events(event_bus, window.plan_panel)
    wire_changeset_events(event_bus, window.changeset_panel)
    wire_project_events(
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
    wire_open_project(window, event_bus, settings=settings)
    wire_settings_dialog(
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

    # Missing-dependency check — warn the user once about runtimes
    # (Node.js, uv, docker, kubectl, gh) that are needed for optional
    # features. Respects a "dependency_check.dismissed" setting so
    # experienced users aren't pestered on every launch. A failure
    # here must never prevent the app from starting.
    try:
        from polyglot_ai.core.async_utils import safe_task as _safe_task
        from polyglot_ai.core.dependency_check import missing_dependencies
        from polyglot_ai.ui.dialogs.dependency_dialog import DependencyDialog

        if not settings.get("dependency_check.dismissed"):
            missing = missing_dependencies()
            if missing:
                dlg = DependencyDialog(missing, parent=window)
                dlg.exec()
                if dlg.dont_show_again:
                    _safe_task(
                        settings.set("dependency_check.dismissed", True),
                        name="save_dep_dismissed",
                    )
    except ImportError:
        logger.exception("Dependency check module not available — skipping")
    except Exception:
        logger.exception("Dependency check failed — continuing without it")
        # Best-effort status bar hint so the user knows something went
        # wrong without blocking startup.
        try:
            window.statusBar().showMessage("Dependency check failed — see logs for details", 8000)
        except Exception:
            pass

    # Onboarding
    run_onboarding(window, settings, keyring_store, provider_manager, event_bus)

    # Restore session
    session_data = {
        "session.open_tabs": settings.get("session.open_tabs"),
        "session.active_tab_index": settings.get("session.active_tab_index"),
        "session.splitter_sizes": settings.get("session.splitter_sizes"),
        "session.window_geometry": settings.get("session.window_geometry"),
    }
    window.restore_session(session_data)

    # Re-open the project that was active at last shutdown so the
    # git/CI/Docker/Database panels and the file explorer come back
    # populated. No-op if the path was never saved or no longer exists.
    try:
        restore_last_project(window, settings)
    except Exception:
        logger.exception("restore_last_project failed")

    # Post-show initialization
    from polyglot_ai.core.async_utils import safe_task

    async def post_show_init():
        await chat.populate_conversations()
        await chat._init_builtin_templates()
        if provider_manager.has_providers:
            await chat.populate_models()

    safe_task(post_show_init(), name="post_show_init")

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
                # Yield once so anyio task-group cancel scopes can finish
                # unwinding before loop close; otherwise stdio_client cleanup
                # raises 'no running event loop' on the final tick. 0.15s
                # chosen empirically — long enough to drain, short enough to
                # not delay shutdown noticeably.
                import asyncio as _asyncio

                loop.run_until_complete(_asyncio.sleep(0.15))
            except Exception:
                logger.exception("Error during async cleanup")
    finally:
        lock.unlock()
        logger.info("Application closed")
