"""Tests for the OpenAI OAuth provider (OpenAIOAuthClient).

F14: Neither OAuth provider had test coverage. These tests cover credential
file parsing and the is_authenticated property using mocked filesystem access —
no live credentials or network calls are made.
"""

from __future__ import annotations

import json


from polyglot_ai.core.ai.openai_oauth import OpenAIOAuthClient
from polyglot_ai.core.bridge import EventBus


class TestOpenAIOAuthClientCredentialLoading:
    def test_no_auth_file_is_not_authenticated(self, tmp_path, monkeypatch):
        """With no auth file the client must not claim to be authenticated."""
        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            tmp_path / "nonexistent.json",
        )
        client = OpenAIOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_valid_auth_file_loads_token(self, tmp_path, monkeypatch):
        """A well-formed auth.json must populate the access token."""
        auth_file = tmp_path / "auth.json"
        payload = {
            "tokens": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
            }
        }
        auth_file.write_text(json.dumps(payload), encoding="utf-8")
        auth_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            auth_file,
        )
        client = OpenAIOAuthClient(EventBus())
        assert client.is_authenticated

    def test_missing_tokens_key_is_not_authenticated(self, tmp_path, monkeypatch):
        """An auth file without a 'tokens' key leaves the client unauthenticated."""
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"other": "data"}), encoding="utf-8")
        auth_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            auth_file,
        )
        client = OpenAIOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_malformed_json_does_not_raise(self, tmp_path, monkeypatch):
        """A JSON parse error must be swallowed; the client stays unauthenticated."""
        auth_file = tmp_path / "auth.json"
        auth_file.write_text("{invalid json", encoding="utf-8")
        auth_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            auth_file,
        )
        client = OpenAIOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_symlink_auth_file_rejected(self, tmp_path, monkeypatch):
        """A symlink auth file must be rejected for security."""
        real_file = tmp_path / "real.json"
        real_file.write_text(json.dumps({"tokens": {"access_token": "tok"}}), encoding="utf-8")
        link = tmp_path / "auth.json"
        link.symlink_to(real_file)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            link,
        )
        client = OpenAIOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_display_name(self, tmp_path, monkeypatch):
        """Provider display name must be stable — callers use it in the UI model selector."""
        monkeypatch.setattr(
            "polyglot_ai.core.ai.openai_oauth.CODEX_AUTH_FILE",
            tmp_path / "nonexistent.json",
        )
        client = OpenAIOAuthClient(EventBus())
        assert client.display_name == "OpenAI (Subscription)"
        assert client.name == "openai_oauth"
