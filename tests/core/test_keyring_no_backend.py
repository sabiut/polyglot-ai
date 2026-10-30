"""A desktop with no keyring service must still start up.

Without a Secret Service / KWallet, ``keyring`` raises NoKeyringError on
every call. get_key used to let that escape into provider registration,
which aborted startup (the AppImage smoke test caught it).
"""

from __future__ import annotations

import keyring
import keyring.errors

from polyglot_ai.core.keyring_store import KeyringStore


class _NoBackend(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, service, username, password):
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service, username):
        raise keyring.errors.NoKeyringError("no backend")


def test_reads_and_deletes_degrade_without_a_backend(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: _NoBackend())
    monkeypatch.setattr(keyring.core, "get_keyring", lambda: _NoBackend())
    store = KeyringStore()
    assert store.get_key("openai") is None
    store.delete_key("openai")  # must not raise (Settings → Save path)


def test_register_providers_survives_no_backend(monkeypatch, tmp_path):
    from polyglot_ai.core.bridge import EventBus
    from polyglot_ai.startup.services import register_ai_providers

    monkeypatch.setattr(keyring.core, "get_keyring", lambda: _NoBackend())
    monkeypatch.setenv("HOME", str(tmp_path))

    class _PM:
        def get_provider(self, name):
            return None

        def register(self, client):
            pass

        def unregister(self, name):
            pass

    register_ai_providers(_PM(), KeyringStore(), EventBus())  # must not raise
