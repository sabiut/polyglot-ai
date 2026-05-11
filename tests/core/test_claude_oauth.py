"""Tests for the Claude OAuth provider (ClaudeOAuthClient).

F14: Neither OAuth provider had test coverage. These tests cover the pure-function
helpers and the credential-parsing path using mocked filesystem access — no live
credentials or network calls are made.
"""

from __future__ import annotations

import json
import time


from polyglot_ai.core.ai.claude_oauth import (
    _is_oauth_unsupported_error,
    _is_rate_limit_error,
    _is_temperature_deprecated_error,
    ClaudeOAuthClient,
)
from polyglot_ai.core.bridge import EventBus


# ── Pure helper tests ──────────────────────────────────────────────────────────


class TestIsOAuthUnsupportedError:
    def test_exact_anthropic_message(self):
        assert _is_oauth_unsupported_error("OAuth authentication is currently not supported.")

    def test_case_insensitive(self):
        assert _is_oauth_unsupported_error("OAUTH AUTHENTICATION IS CURRENTLY NOT SUPPORTED.")

    def test_substring_match(self):
        assert _is_oauth_unsupported_error(
            "Error 401: oauth authentication is currently not supported — please use an API key"
        )

    def test_unrelated_error(self):
        assert not _is_oauth_unsupported_error("Invalid API key")

    def test_empty_string(self):
        assert not _is_oauth_unsupported_error("")

    def test_none_like_empty(self):
        assert not _is_oauth_unsupported_error("")


class TestIsTemperatureDeprecatedError:
    def test_exact_pattern(self):
        assert _is_temperature_deprecated_error(
            "invalid_request_error: `temperature` is deprecated for this model."
        )

    def test_case_insensitive(self):
        assert _is_temperature_deprecated_error("`TEMPERATURE` is DEPRECATED for this model")

    def test_no_match_without_deprecated(self):
        assert not _is_temperature_deprecated_error("`temperature` is unsupported")

    def test_no_match_without_temperature(self):
        assert not _is_temperature_deprecated_error("this parameter is deprecated")

    def test_empty_string(self):
        assert not _is_temperature_deprecated_error("")


class TestIsRateLimitError:
    def test_http_429(self):
        assert _is_rate_limit_error("Error code: 429 - rate limit exceeded")

    def test_rate_limit_error_text(self):
        assert _is_rate_limit_error("{'type': 'rate_limit_error', 'message': 'slow down'}")

    def test_too_many_requests(self):
        assert _is_rate_limit_error("Too many requests, please wait")

    def test_case_insensitive_too_many(self):
        assert _is_rate_limit_error("TOO MANY REQUESTS")

    def test_unrelated_error(self):
        assert not _is_rate_limit_error("Invalid model specified")

    def test_empty_string(self):
        assert not _is_rate_limit_error("")


# ── Credential loading tests ───────────────────────────────────────────────────


class TestClaudeOAuthClientCredentialLoading:
    def test_no_credentials_file_is_not_authenticated(self, tmp_path, monkeypatch):
        """With no credentials file the client must not claim to be authenticated."""
        monkeypatch.setattr(
            "polyglot_ai.core.ai.claude_oauth.CLAUDE_CREDENTIALS_FILE",
            tmp_path / "nonexistent.json",
        )
        client = ClaudeOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_valid_credentials_file_loads_token(self, tmp_path, monkeypatch):
        """A well-formed credentials file must populate the access token."""
        creds_file = tmp_path / ".credentials.json"
        future_expiry = int(time.time() * 1000) + 3_600_000  # 1 hour from now
        payload = {
            "claudeAiOauth": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresAt": future_expiry,
                "subscriptionType": "pro",
            }
        }
        creds_file.write_text(json.dumps(payload), encoding="utf-8")
        creds_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.claude_oauth.CLAUDE_CREDENTIALS_FILE",
            creds_file,
        )
        client = ClaudeOAuthClient(EventBus())
        assert client.is_authenticated

    def test_malformed_credentials_file_does_not_raise(self, tmp_path, monkeypatch):
        """A JSON parse error must be swallowed gracefully; the client stays unauthenticated."""
        creds_file = tmp_path / ".credentials.json"
        creds_file.write_text("not valid json{{{{", encoding="utf-8")
        creds_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.claude_oauth.CLAUDE_CREDENTIALS_FILE",
            creds_file,
        )
        client = ClaudeOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_missing_oauth_key_does_not_raise(self, tmp_path, monkeypatch):
        """A credentials file without claudeAiOauth leaves the client unauthenticated."""
        creds_file = tmp_path / ".credentials.json"
        creds_file.write_text(json.dumps({"other": "data"}), encoding="utf-8")
        creds_file.chmod(0o600)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.claude_oauth.CLAUDE_CREDENTIALS_FILE",
            creds_file,
        )
        client = ClaudeOAuthClient(EventBus())
        assert not client.is_authenticated

    def test_symlink_credentials_rejected(self, tmp_path, monkeypatch):
        """A symlink credentials file must be rejected for security."""
        real_file = tmp_path / "real.json"
        real_file.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "tok"}}), encoding="utf-8"
        )
        link = tmp_path / ".credentials.json"
        link.symlink_to(real_file)

        monkeypatch.setattr(
            "polyglot_ai.core.ai.claude_oauth.CLAUDE_CREDENTIALS_FILE",
            link,
        )
        client = ClaudeOAuthClient(EventBus())
        assert not client.is_authenticated
