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

    from polyglot_ai.core.ai.client import OpenAIClient

    _sync_provider("openai", keyring_store.get_key("openai"), lambda k: OpenAIClient(k, event_bus))

    from polyglot_ai.core.ai.anthropic_client import AnthropicClient

    _sync_provider(
        "anthropic", keyring_store.get_key("anthropic"), lambda k: AnthropicClient(k, event_bus)
    )

    from polyglot_ai.core.ai.google_client import GoogleClient

    _sync_provider("google", keyring_store.get_key("google"), lambda k: GoogleClient(k, event_bus))

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
        lambda k: OpenAIClient(
            k,
            event_bus,
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

    openai_oauth = OpenAIOAuthClient(event_bus)
    if openai_oauth.is_authenticated:
        if not provider_manager.get_provider("openai_oauth"):
            provider_manager.register(openai_oauth)
    else:
        provider_manager.unregister("openai_oauth")

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

    claude_oauth = ClaudeOAuthClient(event_bus)
    if claude_oauth.is_authenticated:
        if not provider_manager.get_provider("claude_oauth"):
            provider_manager.register(claude_oauth)
            logger.info("Claude OAuth registered (subscription auth detected)")
    else:
        provider_manager.unregister("claude_oauth")
        logger.debug("Claude OAuth not authenticated — skipping registration")
