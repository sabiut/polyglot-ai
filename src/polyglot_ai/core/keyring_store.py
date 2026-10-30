"""Secure API key storage via system keyring."""

from __future__ import annotations

import logging

import keyring

from polyglot_ai.constants import KEYRING_SERVICE

logger = logging.getLogger(__name__)


class KeyringStore:
    """CRUD for API keys stored in the system keyring (GNOME Keyring / KWallet)."""

    def __init__(self) -> None:
        # Probe the keyring backend on construction so a headless
        # install (no Secret Service / KWallet running) shows a
        # warning *before* the user types their first API key and
        # finds it disappeared on the next launch.
        self._backend_ok = self._probe_backend()

    @property
    def backend_ok(self) -> bool:
        """True iff the configured keyring backend can persist secrets.

        Read by the settings UI so it can show a "your API keys
        won't be saved between sessions" banner when a Linux install
        lacks Secret Service / pass / KWallet.
        """
        return self._backend_ok

    def store_key(self, provider: str, key: str) -> None:
        # Raises on a backend-less system on purpose: the settings dialog
        # turns that into a visible "couldn't save" error.
        keyring.set_password(KEYRING_SERVICE, provider, key)

    def get_key(self, provider: str) -> str | None:
        """The stored key, or None if there isn't one *or no keyring works*.

        With no Secret Service / KWallet, ``keyring`` raises on every
        read. That used to abort provider registration during startup —
        on such a desktop the app never finished starting up: saved
        subscription logins weren't registered and onboarding never ran.
        """
        try:
            return keyring.get_password(KEYRING_SERVICE, provider)
        except keyring.errors.KeyringError as exc:
            logger.warning("keyring: can't read %s key (%s)", provider, exc.__class__.__name__)
            return None

    def delete_key(self, provider: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as exc:
            # Nothing can be stored without a backend, so nothing to delete;
            # don't let Settings → Save fail over it.
            logger.warning("keyring: can't delete %s key (%s)", provider, exc.__class__.__name__)

    @staticmethod
    def _probe_backend() -> bool:
        """Return True iff the active keyring backend can persist data.

        ``keyring`` falls back to ``fail.Keyring`` (every call raises)
        or ``null.Keyring`` (every call no-ops) on systems with no
        usable backend. Both are useless for storing secrets — flag
        them so the UI can warn instead of silently dropping keys.
        """
        try:
            backend = keyring.get_keyring()
        except Exception:
            logger.warning("keyring: backend probe failed", exc_info=True)
            return False
        backend_name = type(backend).__name__.lower()
        if "fail" in backend_name or "null" in backend_name:
            logger.warning(
                "keyring: backend is %s — API keys will not persist. "
                "Install gnome-keyring / KWallet / pass and ensure the "
                "Secret Service is running.",
                type(backend).__module__ + "." + type(backend).__name__,
            )
            return False
        logger.info(
            "keyring: using %s",
            type(backend).__module__ + "." + type(backend).__name__,
        )
        return True
