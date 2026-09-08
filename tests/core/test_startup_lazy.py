"""Startup cost guards found by the startup timer.

Two offenders accounted for most of a cold start: importing every AI
SDK at provider registration (over a second, even with no keys) and
the Arduino panel scanning boards while hidden.
"""

from __future__ import annotations

import sys


from polyglot_ai.core.bridge import EventBus
from polyglot_ai.startup.services import register_ai_providers

SDK_MODULES = (
    "polyglot_ai.core.ai.client",
    "polyglot_ai.core.ai.anthropic_client",
    "polyglot_ai.core.ai.google_client",
)


class _NoKeys:
    def get_key(self, provider):
        return None


class _Providers:
    def __init__(self):
        self.registered = {}

    def get_provider(self, name):
        return self.registered.get(name)

    def register(self, client):
        self.registered[getattr(client, "name", type(client).__name__)] = client

    def unregister(self, name):
        self.registered.pop(name, None)


def test_registration_without_keys_imports_no_sdk_client_modules(monkeypatch, tmp_path):
    for mod in SDK_MODULES:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    # Keep the OAuth clients from finding real tokens on this machine.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    register_ai_providers(_Providers(), _NoKeys(), EventBus())

    loaded = [mod for mod in SDK_MODULES if mod in sys.modules]
    assert loaded == [], f"SDK client modules imported without a key: {loaded}"


def test_registration_with_a_key_imports_that_sdk_only(monkeypatch, tmp_path):
    for mod in SDK_MODULES:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    class _OpenAIOnly:
        def get_key(self, provider):
            return "sk-test" if provider == "openai" else None

    providers = _Providers()
    register_ai_providers(providers, _OpenAIOnly(), EventBus())
    assert "polyglot_ai.core.ai.client" in sys.modules
    assert "polyglot_ai.core.ai.google_client" not in sys.modules
    assert any("openai" in name.lower() for name in providers.registered)


def test_preload_imports_only_modules_for_configured_keys(monkeypatch):
    from polyglot_ai.startup.services import preload_provider_sdks

    for mod in SDK_MODULES:
        monkeypatch.delitem(sys.modules, mod, raising=False)

    class _AnthropicOnly:
        def get_key(self, provider):
            return "sk-ant" if provider == "anthropic" else None

    wanted = preload_provider_sdks(_AnthropicOnly())
    assert wanted == ["polyglot_ai.core.ai.anthropic_client"]
    assert "polyglot_ai.core.ai.anthropic_client" in sys.modules
    assert "polyglot_ai.core.ai.google_client" not in sys.modules
    assert preload_provider_sdks(_NoKeys()) == []
