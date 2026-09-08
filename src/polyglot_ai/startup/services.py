"""Core service creation and AI provider registration."""

from __future__ import annotations

import logging

from polyglot_ai.constants import DB_PATH, LOG_DIR
from polyglot_ai.core.audit import AuditLogger
from polyglot_ai.core.bridge import EventBus
from polyglot_ai.core.database import Database
from polyglot_ai.core.keyring_store import KeyringStore
from polyglot_ai.core.settings import SettingsManager
from polyglot_ai.ui.bridge_qt import QtBridgeAdapter

logger = logging.getLogger(__name__)


def create_core_services():
    """Create core services: event bus, database, settings, keyring, audit."""
    event_bus = EventBus()
    db = Database(DB_PATH)
    settings = SettingsManager(db)
    keyring_store = KeyringStore()
    audit = AuditLogger(LOG_DIR)
    bridge = QtBridgeAdapter(event_bus)
    return event_bus, db, settings, keyring_store, audit, bridge


#: SDK-backed client modules, keyed by the keyring entries that need them.
_SDK_MODULES_BY_KEY = {
    "openai": "polyglot_ai.core.ai.client",
    "deepseek": "polyglot_ai.core.ai.client",
    "anthropic": "polyglot_ai.core.ai.anthropic_client",
    "google": "polyglot_ai.core.ai.google_client",
}


def preload_provider_sdks(keyring_store) -> list[str]:
    """Import the SDK modules the configured keys will need; return their names.

    Safe to run on a worker thread (no Qt): the openai / anthropic /
    google-genai imports cost 0.3–1.4 s each, and doing them here lets
    the window paint first while :func:`register_ai_providers` then
    finds them already in ``sys.modules``.
    """
    import importlib

    wanted: list[str] = []
    for key, module in _SDK_MODULES_BY_KEY.items():
        if module not in wanted and keyring_store.get_key(key):
            wanted.append(module)
    for module in wanted:
        try:
            importlib.import_module(module)
        except Exception:
            logger.exception("Could not preload %s", module)
    return wanted


def register_ai_providers(provider_manager, keyring_store, event_bus):
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

    # The SDK modules are imported inside the factories, only for
    # providers that actually have a key: importing openai, anthropic
    # and google-genai together costs over a second of startup, and
    # most users have configured one or two of them, not all.
    def _openai_client(key, **kwargs):
        from polyglot_ai.core.ai.client import OpenAIClient

        return OpenAIClient(key, event_bus, **kwargs)

    def _anthropic_client(key):
        from polyglot_ai.core.ai.anthropic_client import AnthropicClient

        return AnthropicClient(key, event_bus)

    def _google_client(key):
        from polyglot_ai.core.ai.google_client import GoogleClient

        return GoogleClient(key, event_bus)

    _sync_provider("openai", keyring_store.get_key("openai"), _openai_client)
    _sync_provider("anthropic", keyring_store.get_key("anthropic"), _anthropic_client)
    _sync_provider("google", keyring_store.get_key("google"), _google_client)

    # DeepSeek — OpenAI-compatible endpoint at api.deepseek.com.
    # V4 lineup is two models: ``deepseek-v4-pro`` (flagship) and
    # ``deepseek-v4-flash`` (fast/lightweight). Stream options are
    # off because the DeepSeek endpoint does not honour
    # ``include_usage`` in chunk metadata.
    _DEEPSEEK_MODELS = [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    _sync_provider(
        "deepseek",
        keyring_store.get_key("deepseek"),
        lambda k: _openai_client(
            k,
            base_url="https://api.deepseek.com/v1",
            provider_name="deepseek",
            provider_display_name="DeepSeek",
            default_models=_DEEPSEEK_MODELS,
            model_filter=("deepseek",),
            enable_stream_options=False,
            reasoning_prefixes=(),
        ),
    )

    # OpenAI OAuth (subscription login)
    from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient

    def _sync_oauth(name: str, fresh_client) -> None:
        """Register/unregister an OAuth provider, refreshing a live one.

        The settings dialog re-runs this after a login. If the provider
        was already registered, the *existing* instance kept the old
        (expired or missing) tokens and the user saw "signed in ✓" but
        every request still failed until restart. Reload its tokens
        from disk instead of leaving it stale.
        """
        existing = provider_manager.get_provider(name)
        if existing is not None and hasattr(existing, "reload_tokens"):
            existing.reload_tokens()
            if not existing.is_authenticated:
                provider_manager.unregister(name)
            return
        if fresh_client.is_authenticated:
            provider_manager.register(fresh_client)
            logger.info("%s registered (subscription auth detected)", name)
        else:
            provider_manager.unregister(name)

    _sync_oauth("openai_oauth", OpenAIOAuthClient(event_bus))

    # Claude OAuth (subscription login).
    #
    # Earlier versions had this registration commented out because
    # Anthropic's ``/v1/messages`` was rejecting OAuth bearer tokens
    # at the time, so even an authenticated user saw the API error
    # at first message. The cure was worse than the disease: with
    # registration disabled, the model dropdown still showed
    # ``claude-*`` (those names live in Anthropic's API client too),
    # the provider lookup returned None, and the user hit a
    # cryptic "No provider found for model: claude-opus-4-7" loop
    # with no clue what was wrong.
    #
    # Re-enabling the registration. If upstream still rejects the
    # OAuth token, the user now gets Anthropic's own error message
    # ("authentication is not supported" or similar) — at least
    # they can see *what* failed and where, rather than wondering
    # why the model dropdown's selection is unusable.
    from polyglot_ai.core.ai.claude_oauth import ClaudeOAuthClient

    _sync_oauth("claude_oauth", ClaudeOAuthClient(event_bus))
