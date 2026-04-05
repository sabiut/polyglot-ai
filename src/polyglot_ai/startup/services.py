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

    _XAI_MODELS = [
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4-1-fast-reasoning",
        "grok-4-1-fast-non-reasoning",
    ]
    _sync_provider(
        "xai",
        keyring_store.get_key("xai"),
        lambda k: OpenAIClient(
            k,
            event_bus,
            base_url="https://api.x.ai/v1",
            provider_name="xai",
            provider_display_name="xAI (Grok)",
            default_models=_XAI_MODELS,
            model_filter=("grok",),
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
