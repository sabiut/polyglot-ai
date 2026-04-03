"""Secure API key storage via system keyring."""

from __future__ import annotations

import keyring

from polyglot_ai.constants import KEYRING_SERVICE


class KeyringStore:
    """CRUD for API keys stored in the system keyring (GNOME Keyring / KWallet)."""

    def store_key(self, provider: str, key: str) -> None:
        keyring.set_password(KEYRING_SERVICE, provider, key)

    def get_key(self, provider: str) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, provider)

    def delete_key(self, provider: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except keyring.errors.PasswordDeleteError:
            pass
