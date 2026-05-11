"""Claude OAuth provider — uses Claude subscription via Claude Code credentials.

Reads tokens from ~/.claude/.credentials.json (created by `claude login`).
Uses the standard Anthropic Messages API with auth_token authentication.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator

from anthropic import AsyncAnthropic, BadRequestError

from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_STREAM_CHUNK,
    EVT_AI_STREAM_DONE,
)
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

CLAUDE_CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
_MODEL_CACHE_TTL = 300  # seconds — cache model list for 5 minutes

DEFAULT_MODELS = [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]


def _is_oauth_unsupported_error(error_text: str) -> bool:
    """Detect Anthropic's specific 'OAuth not supported' rejection.

    The exact text Anthropic returns is
    ``"OAuth authentication is currently not supported."`` inside an
    HTTP 401 ``authentication_error``. We match on a substring so a
    minor wording change (capitalisation, trailing punctuation,
    nested in a wrapper exception's ``str``) doesn't make us miss
    it. Case-insensitive for the same reason.
    """
    if not error_text:
        return False
    needle = "oauth authentication is currently not supported"
    return needle in error_text.lower()


def _is_temperature_deprecated_error(error_text: str) -> bool:
    """Detect the 400 newer Claude models return for ``temperature``.

    Anthropic's response is::

        invalid_request_error: `temperature` is deprecated for this
        model.

    Claude Sonnet 4.5+ / Opus 4.7+ raise it; older models still
    accept the parameter. Substring + case-insensitive match for
    the same robustness reason as the OAuth check above.
    """
    if not error_text:
        return False
    lower = error_text.lower()
    return "`temperature`" in lower and "deprecated" in lower


def _is_rate_limit_error(error_text: str) -> bool:
    """Detect Anthropic's 429 ``rate_limit_error``.

    The body is typically::

        Error code: 429 - {'type': 'error', 'error':
        {'type': 'rate_limit_error', 'message': 'Error'}, ...}

    Anthropic's subscription OAuth tier has tighter per-minute caps
    than API-key access (subscription traffic on claude.ai already
    counts against the same budget), so users hit this far more
    quickly through this provider. We render it specially so users
    see "wait and retry" instead of a generic ``Error`` dump.
    """
    if not error_text:
        return False
    lower = error_text.lower()
    return "429" in lower or "rate_limit_error" in lower or "too many requests" in lower


class ClaudeOAuthClient(AIProvider):
    """Claude provider using subscription via OAuth tokens from Claude Code."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: int | None = None
        self._subscription_type: str | None = None
        self._client: AsyncAnthropic | None = None
        # Model list cache — avoids repeated API calls
        self._cached_models: list[str] | None = None
        self._models_cached_at: float = 0.0
        self._load_tokens()

    @property
    def name(self) -> str:
        return "claude_oauth"

    @property
    def display_name(self) -> str:
        return "Claude (Subscription)"

    def update_api_key(self, api_key: str) -> None:
        pass  # OAuth — no API key

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    def _load_tokens(self) -> None:
        """Load OAuth tokens from Claude Code's credentials file."""
        if not CLAUDE_CREDENTIALS_FILE.exists():
            return

        # Security: reject symlinks and files not owned by current user
        from polyglot_ai.core.security import check_secure_file

        secure, reason = check_secure_file(CLAUDE_CREDENTIALS_FILE)
        if not secure:
            logger.warning("Insecure credentials file: %s — %s", CLAUDE_CREDENTIALS_FILE, reason)

            # Reject symlinks and wrong-owner outright — never read these
            if CLAUDE_CREDENTIALS_FILE.is_symlink() or "not owned" in reason:
                logger.error("Refusing to read credential file: %s", reason)
                return

            # Only auto-fix permission issues on regular files we own
            try:
                CLAUDE_CREDENTIALS_FILE.chmod(0o600)
                logger.info("Fixed permissions on %s", CLAUDE_CREDENTIALS_FILE)
            except OSError:
                logger.error("Cannot fix permissions on %s — skipping", CLAUDE_CREDENTIALS_FILE)
                return

            # Re-validate after chmod
            secure, reason = check_secure_file(CLAUDE_CREDENTIALS_FILE)
            if not secure:
                logger.error(
                    "Credentials file still insecure after chmod: %s — refusing to read", reason
                )
                return

        try:
            data = json.loads(CLAUDE_CREDENTIALS_FILE.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth", {})
            if isinstance(oauth, dict):
                self._access_token = oauth.get("accessToken")
                self._refresh_token = oauth.get("refreshToken")
                self._expires_at = oauth.get("expiresAt")
                self._subscription_type = oauth.get("subscriptionType")
            if self._access_token:
                self._client = AsyncAnthropic(auth_token=self._access_token)
                logger.info(
                    "Loaded Claude auth from ~/.claude/.credentials.json (subscription: %s)",
                    self._subscription_type or "unknown",
                )
        except Exception:
            logger.exception("Failed to load Claude credentials")

    def _is_token_expired(self) -> bool:
        """Check if the access token has expired (with 60s buffer)."""
        if self._expires_at is None:
            return False
        now_ms = int(time.time() * 1000)
        return now_ms >= (self._expires_at - 60_000)

    def _try_refresh_token(self) -> bool:
        """Attempt to refresh by reloading tokens from disk.

        Claude Code CLI manages token refresh automatically, so we simply
        reload from the credentials file to pick up any updated tokens.
        """
        old_token = self._access_token
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._client = None
        self._load_tokens()

        if self._access_token and self._access_token != old_token:
            logger.info("Claude token refreshed from disk")
            return True
        if self._access_token and not self._is_token_expired():
            return True
        return False

    def reload_tokens(self) -> None:
        """Reload tokens from disk."""
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._client = None
        self._load_tokens()

    @staticmethod
    def run_claude_login() -> bool:
        """Run `claude auth login` to authenticate via browser OAuth flow."""
        claude_path = shutil.which("claude")
        if not claude_path:
            logger.error("Claude Code CLI not found. Install from https://claude.ai/download")
            return False
        try:
            result = subprocess.run(
                [claude_path, "auth", "login", "--claudeai"],
                timeout=180,
                capture_output=False,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            logger.exception("Claude login failed")
            return False

    @staticmethod
    def is_claude_available() -> bool:
        """Check if Claude Code CLI is installed."""
        return shutil.which("claude") is not None

    async def list_models(self) -> list[str]:
        """List available Claude models (cached for 5 minutes)."""
        now = time.time()
        if self._cached_models and (now - self._models_cached_at) < _MODEL_CACHE_TTL:
            return list(self._cached_models)

        if not self._client:
            return list(DEFAULT_MODELS)
        try:
            response = await self._client.models.list(limit=100)
            models = [m.id for m in response.data if m.id.startswith("claude")]
            result = sorted(models) if models else list(DEFAULT_MODELS)
            self._cached_models = result
            self._models_cached_at = now
            return list(result)
        except Exception:
            logger.exception("Failed to list Claude models via subscription")
            return list(DEFAULT_MODELS)

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "claude-sonnet-4-6",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        if not self._access_token or not self._client:
            yield StreamChunk(
                delta_content="\n\n**Error:** Not logged in to Claude. "
                "Run 'Sign in with Claude' in Settings."
            )
            return

        # Check token expiration, attempt refresh
        if self._is_token_expired():
            if not self._try_refresh_token():
                yield StreamChunk(
                    delta_content="\n\n**Error:** Claude session expired. "
                    "Please sign in again via Settings."
                )
                return

        # Rebuild client with fresh token before each request
        self._client = AsyncAnthropic(auth_token=self._access_token)

        try:
            # Convert messages: Anthropic doesn't use 'system' role in messages
            anthropic_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                if role == "system":
                    if not system_prompt:
                        system_prompt = msg.get("content", "")
                    continue
                if role in ("user", "assistant"):
                    anthropic_messages.append(
                        {
                            "role": role,
                            "content": msg.get("content", ""),
                        }
                    )

            if not anthropic_messages:
                anthropic_messages = [{"role": "user", "content": "Hello"}]

            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            if temperature is not None:
                kwargs["temperature"] = temperature

            # Convert OpenAI-style tools to Anthropic format
            if tools:
                anthropic_tools = []
                for tool in tools:
                    func = tool.get("function", {})
                    anthropic_tools.append(
                        {
                            "name": func.get("name", ""),
                            "description": func.get("description", ""),
                            "input_schema": func.get("parameters", {}),
                        }
                    )
                kwargs["tools"] = anthropic_tools

            block_to_tool_idx: dict[int, int] = {}
            next_tool_idx = 0

            # Newer Claude models (Sonnet 4.5+, Opus 4.7+) reject the
            # ``temperature`` parameter outright with a 400. The
            # parameter is set on ``__aenter__`` of the stream context
            # manager — i.e. before any chunk is yielded — so a clean
            # one-shot retry without ``temperature`` is safe and
            # transparent to the caller. Older models still accept it,
            # so we try with it first.
            stream_cm = self._client.messages.stream(**kwargs)
            try:
                stream = await stream_cm.__aenter__()
            except BadRequestError as e:
                from polyglot_ai.core.security import sanitize_error

                if (
                    _is_temperature_deprecated_error(sanitize_error(str(e)))
                    and "temperature" in kwargs
                ):
                    logger.info("Model rejected temperature parameter; retrying without it")
                    kwargs.pop("temperature", None)
                    stream_cm = self._client.messages.stream(**kwargs)
                    stream = await stream_cm.__aenter__()
                else:
                    raise

            try:
                async for event in stream:
                    if not hasattr(event, "type"):
                        continue

                    event_type = event.type

                    if event_type == "content_block_start" and hasattr(event, "index"):
                        if hasattr(event.content_block, "type"):
                            if event.content_block.type == "tool_use":
                                tidx = next_tool_idx
                                block_to_tool_idx[event.index] = tidx
                                next_tool_idx += 1
                                yield StreamChunk(
                                    tool_calls=[
                                        {
                                            "index": tidx,
                                            "id": event.content_block.id,
                                            "function": {
                                                "name": event.content_block.name,
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                )

                    elif event_type == "content_block_delta" and hasattr(event, "index"):
                        delta = event.delta
                        if hasattr(delta, "text"):
                            self._event_bus.emit(EVT_AI_STREAM_CHUNK, content=delta.text)
                            yield StreamChunk(delta_content=delta.text)
                        elif hasattr(delta, "partial_json"):
                            tidx = block_to_tool_idx.get(event.index, 0)
                            yield StreamChunk(
                                tool_calls=[
                                    {
                                        "index": tidx,
                                        "id": None,
                                        "function": {
                                            "name": None,
                                            "arguments": delta.partial_json,
                                        },
                                    }
                                ]
                            )

                    elif event_type == "message_delta":
                        if hasattr(event, "usage") and event.usage:
                            reason = event.delta.stop_reason
                            if reason == "tool_use":
                                reason = "tool_calls"
                            yield StreamChunk(
                                finish_reason=reason,
                                usage={
                                    "prompt_tokens": 0,
                                    "completion_tokens": event.usage.output_tokens,
                                    "total_tokens": event.usage.output_tokens,
                                },
                            )

                # Get final usage
                final_message = await stream.get_final_message()
                if final_message and final_message.usage:
                    yield StreamChunk(
                        usage={
                            "prompt_tokens": final_message.usage.input_tokens,
                            "completion_tokens": final_message.usage.output_tokens,
                            "total_tokens": (
                                final_message.usage.input_tokens + final_message.usage.output_tokens
                            ),
                        }
                    )
            finally:
                # Mirror what ``async with`` would have done — close
                # the stream and any open HTTP connections regardless
                # of whether iteration succeeded, raised, or was
                # cancelled (e.g. user cancelled mid-stream).
                await stream_cm.__aexit__(None, None, None)

            self._event_bus.emit(EVT_AI_STREAM_DONE)

        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            error_msg = sanitize_error(str(e))
            logger.exception("Claude subscription API error")
            self._event_bus.emit(EVT_AI_ERROR, error=error_msg)

            # Anthropic's public /v1/messages API rejects OAuth
            # bearer tokens with HTTP 401 + "OAuth authentication is
            # currently not supported". The Claude Code CLI uses an
            # internal Anthropic routing layer that third-party apps
            # don't have access to. Users see this when they sign
            # in via the OAuth flow successfully and then send a
            # message; the credentials are real but the API won't
            # accept them. Replace the cryptic JSON dump with a
            # friendly explanation + the workaround.
            if _is_oauth_unsupported_error(error_msg):
                yield StreamChunk(
                    delta_content=(
                        "\n\n**Claude subscription chat isn't supported on the "
                        "public Anthropic API yet.**\n\n"
                        "Your OAuth login (Claude Pro / Max / Team) succeeded — "
                        "the token in `~/.claude/.credentials.json` is real. "
                        "But Anthropic's public ``/v1/messages`` endpoint "
                        "doesn't currently accept OAuth bearer tokens; only "
                        "Claude Code's internal routing layer does, and that "
                        "isn't available to third-party apps.\n\n"
                        "**To keep using Claude:**\n\n"
                        "**Add an Anthropic API key.** Open **Settings → AI "
                        "Providers → Anthropic** and paste a key from "
                        "https://console.anthropic.com/settings/keys. Pick a "
                        "Claude model from the dropdown again and all the "
                        "IDE features (tool calls, MCP, workflows) keep "
                        "working. API-key access uses a different billing "
                        "channel than your Pro / Max / Team subscription, "
                        "but the friendliest Claude tier (`claude-haiku`) "
                        "is inexpensive for typical IDE usage."
                    )
                )
                return
            if _is_rate_limit_error(error_msg):
                yield StreamChunk(
                    delta_content=(
                        "\n\n**Anthropic rate-limited this request (HTTP 429).**\n\n"
                        "Subscription OAuth has tighter per-minute caps than "
                        "API-key access — subscription usage on claude.ai counts "
                        "against the same budget, so heavy chat in either place "
                        "trips the limit fast.\n\n"
                        "**Options:**\n\n"
                        "1. **Wait ~1 minute and retry.** The cap resets on a "
                        "rolling window.\n"
                        "2. **Switch to an API key** — Settings → AI Providers "
                        "→ Anthropic, paste a key from "
                        "https://console.anthropic.com/settings/keys. API-key "
                        "rate limits are much higher than subscription OAuth."
                    )
                )
                return
            yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg[:200]}")

    async def test_connection(self) -> tuple[bool, str]:
        if not self._access_token or not self._client:
            return False, "Not logged in"
        try:
            await self._client.models.list(limit=1)
            sub = f" ({self._subscription_type})" if self._subscription_type else ""
            return True, f"Connected via Claude subscription{sub}"
        except Exception as e:
            from polyglot_ai.core.security import sanitize_error

            return False, sanitize_error(str(e))

    def logout(self, clear_disk: bool = True) -> str:
        """Clear tokens from memory and optionally from disk.

        NOTE: This is a local-only sign-out. Tokens are not revoked with
        Anthropic. If tokens were copied elsewhere, they may remain valid
        until they expire. Users should rotate credentials if compromise
        is suspected.

        The credentials file is kept (for Claude Code CLI) but the
        claudeAiOauth tokens are nulled out.

        Returns:
            A status message indicating the logout scope.
        """
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._client = None

        if clear_disk and CLAUDE_CREDENTIALS_FILE.exists():
            try:
                from polyglot_ai.core.security import secure_write

                data = json.loads(CLAUDE_CREDENTIALS_FILE.read_text(encoding="utf-8"))
                if "claudeAiOauth" in data and isinstance(data["claudeAiOauth"], dict):
                    data["claudeAiOauth"]["accessToken"] = None
                    data["claudeAiOauth"]["refreshToken"] = None
                    secure_write(CLAUDE_CREDENTIALS_FILE, json.dumps(data, indent=2))
            except Exception:
                logger.exception("Failed to clear Claude tokens from disk")

        logger.info("Claude OAuth logged out (local tokens cleared, not revoked remotely)")
        return "Signed out locally. Tokens were not revoked with Anthropic."
