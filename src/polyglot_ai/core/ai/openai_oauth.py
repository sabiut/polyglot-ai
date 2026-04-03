"""OpenAI OAuth provider — uses ChatGPT subscription via Codex Responses API.

Reads tokens from ~/.codex/auth.json (created by `npx -y @openai/codex@latest login`).
Uses the Codex Responses API at chatgpt.com/backend-api/codex/responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import AsyncGenerator

import httpx

from polyglot_ai.core.security import sanitize_error
from polyglot_ai.constants import (
    EVT_AI_ERROR,
    EVT_AI_STREAM_CHUNK,
    EVT_AI_STREAM_DONE,
)
from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex",
]


class OpenAIOAuthClient(AIProvider):
    """OpenAI provider using ChatGPT subscription via Codex Responses API."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._load_tokens()

    @property
    def name(self) -> str:
        return "openai_oauth"

    @property
    def display_name(self) -> str:
        return "OpenAI (Subscription)"

    def update_api_key(self, api_key: str) -> None:
        pass

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    def _load_tokens(self) -> None:
        if not CODEX_AUTH_FILE.exists():
            return

        # Security: reject symlinks and files not owned by current user
        from polyglot_ai.core.security import check_secure_file
        secure, reason = check_secure_file(CODEX_AUTH_FILE)
        if not secure:
            logger.warning("Insecure auth file: %s — %s", CODEX_AUTH_FILE, reason)

            # Reject symlinks and wrong-owner outright — never read these
            if CODEX_AUTH_FILE.is_symlink() or "not owned" in reason:
                logger.error("Refusing to read credential file: %s", reason)
                return

            # Only auto-fix permission issues on regular files we own
            try:
                CODEX_AUTH_FILE.chmod(0o600)
                logger.info("Fixed permissions on %s", CODEX_AUTH_FILE)
            except OSError:
                logger.error("Cannot fix permissions on %s — skipping", CODEX_AUTH_FILE)
                return

            # Re-validate after chmod
            secure, reason = check_secure_file(CODEX_AUTH_FILE)
            if not secure:
                logger.error("Auth file still insecure after chmod: %s — refusing to read", reason)
                return

        try:
            data = json.loads(CODEX_AUTH_FILE.read_text())
            if isinstance(data, dict):
                tokens = data.get("tokens", {})
                if isinstance(tokens, dict):
                    self._access_token = tokens.get("access_token")
                    self._refresh_token = tokens.get("refresh_token")
            if self._access_token:
                logger.info("Loaded OpenAI auth tokens")
        except Exception:
            logger.exception("Failed to load auth file")

    async def _refresh_access_token(self) -> bool:
        if not self._refresh_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    OPENAI_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": OPENAI_CLIENT_ID,
                        "refresh_token": self._refresh_token,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._access_token = data.get("access_token")
                    if data.get("refresh_token"):
                        self._refresh_token = data["refresh_token"]
                    self._save_tokens()
                    logger.info("Refreshed OpenAI access token")
                    return True
                return False
        except Exception:
            logger.exception("Token refresh error")
            return False

    def _save_tokens(self) -> None:
        if not CODEX_AUTH_FILE.exists():
            return
        try:
            from polyglot_ai.core.security import secure_write
            data = json.loads(CODEX_AUTH_FILE.read_text())
            if "tokens" in data and isinstance(data["tokens"], dict):
                data["tokens"]["access_token"] = self._access_token
                if self._refresh_token:
                    data["tokens"]["refresh_token"] = self._refresh_token
            secure_write(CODEX_AUTH_FILE, json.dumps(data, indent=2))
        except Exception:
            logger.exception("Failed to save refreshed tokens")

    def reload_tokens(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._load_tokens()

    @staticmethod
    def run_codex_login() -> bool:
        try:
            result = subprocess.run(
                ["npx", "-y", "@openai/codex@latest", "login"],
                timeout=180,
                capture_output=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.error("npx not found. Install Node.js: sudo apt install nodejs npm")
            return False
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            logger.exception("Codex login failed")
            return False

    @staticmethod
    def is_codex_available() -> bool:
        try:
            subprocess.run(["npx", "--version"], capture_output=True, timeout=10)
            return True
        except Exception:
            return False

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        return list(DEFAULT_MODELS)

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "gpt-5.4-mini",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        if not self._access_token:
            yield StreamChunk(
                delta_content="\n\n**Error:** Not logged in. "
                "Run 'Sign in with ChatGPT' in Settings."
            )
            return

        input_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                if not system_prompt:
                    system_prompt = content
                continue
            if role in ("user", "assistant"):
                # Responses API requires structured content items
                if isinstance(content, list):
                    # Already multimodal (text + images) — convert each part
                    parts = []
                    for part in content:
                        if part.get("type") == "text":
                            ct = "input_text" if role == "user" else "output_text"
                            parts.append({"type": ct, "text": part.get("text", "")})
                        elif part.get("type") == "image_url":
                            # Convert OpenAI image_url format to Responses API input_image
                            url = part.get("image_url", {}).get("url", "")
                            parts.append({"type": "input_image", "image_url": url})
                    input_messages.append({"role": role, "content": parts})
                else:
                    ct = "input_text" if role == "user" else "output_text"
                    input_messages.append({
                        "role": role,
                        "content": [{"type": ct, "text": str(content)}],
                    })

        if not input_messages:
            input_messages = [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}]

        payload = {
            "model": model,
            "instructions": system_prompt or "You are a helpful assistant.",
            "input": input_messages,
            "store": False,
            "stream": True,
        }

        # Include tools if provided (convert to Responses API format)
        if tools:
            responses_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    fn = tool["function"]
                    responses_tools.append({
                        "type": "function",
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
            if responses_tools:
                payload["tools"] = responses_tools

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                timeout_cfg = httpx.Timeout(connect=45, read=180, write=30, pool=30)
                async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                    async with client.stream(
                        "POST",
                        CODEX_RESPONSES_URL,
                        headers=self._get_headers(),
                        json=payload,
                    ) as resp:
                        if resp.status_code == 401:
                            if await self._refresh_access_token():
                                async for chunk in self.stream_chat(
                                    messages, model, tools, temperature,
                                    max_tokens, system_prompt,
                                ):
                                    yield chunk
                                return
                            yield StreamChunk(
                                delta_content="\n\n**Error:** Session expired. "
                                "Please sign in again via Settings."
                            )
                            return

                        if resp.status_code == 429:
                            wait = 5 * (attempt + 1)
                            logger.warning("Rate limited, retrying in %ds...", wait)
                            await asyncio.sleep(wait)
                            continue

                        if resp.status_code != 200:
                            # Never expose raw backend response to the UI
                            body = await resp.aread()
                            logger.error(
                                "OpenAI API error %d: %s",
                                resp.status_code,
                                sanitize_error(body.decode(errors="replace")[:500]),
                            )
                            status = resp.status_code
                            if status == 403:
                                user_msg = "Access denied. Check your subscription status."
                            elif status == 422:
                                user_msg = "Invalid request. The model may not support this operation."
                            elif 500 <= status < 600:
                                user_msg = "Provider server error. Please try again later."
                            else:
                                user_msg = f"Provider returned HTTP {status}. See logs for details."
                            yield StreamChunk(
                                delta_content=f"\n\n**Error:** {user_msg}"
                            )
                            return

                        # Track unique tool call indices by call_id so
                        # multiple parallel tool calls don't get merged.
                        call_id_to_idx: dict[str, int] = {}
                        call_id_names: dict[str, str] = {}
                        # Track which calls got their args via deltas vs done
                        call_id_has_deltas: set[str] = set()
                        next_tool_idx = 0

                        async for line in resp.aiter_lines():
                            if not line or line.startswith("event:"):
                                continue
                            if not line.startswith("data: "):
                                continue
                            try:
                                data = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue

                            event_type = data.get("type", "")
                            # Log tool-related events for debugging
                            if "function_call" in event_type or "output_item" in event_type:
                                logger.debug("Responses API event: %s data_keys=%s",
                                             event_type, list(data.keys())[:10])
                            if event_type == "response.output_text.delta":
                                delta = data.get("delta", "")
                                if delta:
                                    self._event_bus.emit(EVT_AI_STREAM_CHUNK, content=delta)
                                    yield StreamChunk(delta_content=delta)

                            elif event_type == "response.output_item.added":
                                # First event for a new function call.
                                item = data.get("item", {})
                                if item.get("type") == "function_call":
                                    call_id = item.get("call_id", "")
                                    name = item.get("name", "")
                                    if call_id and call_id not in call_id_to_idx:
                                        call_id_to_idx[call_id] = next_tool_idx
                                        next_tool_idx += 1
                                    if name:
                                        call_id_names[call_id] = name
                                    tidx = call_id_to_idx.get(call_id, 0)
                                    yield StreamChunk(
                                        tool_calls=[{
                                            "index": tidx,
                                            "id": call_id,
                                            "function": {
                                                "name": name,
                                                "arguments": "",
                                            },
                                        }],
                                    )

                            elif event_type == "response.function_call_arguments.delta":
                                call_id = data.get("call_id", "")
                                if call_id not in call_id_to_idx:
                                    call_id_to_idx[call_id] = next_tool_idx
                                    next_tool_idx += 1
                                tidx = call_id_to_idx[call_id]
                                name = data.get("name") or call_id_names.get(call_id)
                                call_id_has_deltas.add(call_id)
                                yield StreamChunk(
                                    tool_calls=[{
                                        "index": tidx,
                                        "id": call_id,
                                        "function": {
                                            "name": name,
                                            "arguments": data.get("delta", ""),
                                        },
                                    }],
                                )

                            elif event_type == "response.function_call_arguments.done":
                                # Complete arguments for one call. If we
                                # already got deltas, skip (they accumulated).
                                # If no deltas arrived, use this as the source.
                                call_id = data.get("call_id", "")
                                if call_id not in call_id_has_deltas:
                                    if call_id not in call_id_to_idx:
                                        call_id_to_idx[call_id] = next_tool_idx
                                        next_tool_idx += 1
                                    tidx = call_id_to_idx[call_id]
                                    name = data.get("name") or call_id_names.get(call_id, "")
                                    yield StreamChunk(
                                        tool_calls=[{
                                            "index": tidx,
                                            "id": call_id,
                                            "function": {
                                                "name": name,
                                                "arguments": data.get("arguments", ""),
                                            },
                                        }],
                                    )

                            elif event_type == "response.output_item.done":
                                item = data.get("item", {})
                                if item.get("type") == "function_call":
                                    call_id = item.get("call_id", "")
                                    name = item.get("name", "") or call_id_names.get(call_id, "")
                                    # If we never got deltas OR done for this
                                    # call, use the complete item as fallback
                                    if call_id not in call_id_has_deltas:
                                        if call_id not in call_id_to_idx:
                                            call_id_to_idx[call_id] = next_tool_idx
                                            next_tool_idx += 1
                                        tidx = call_id_to_idx[call_id]
                                        yield StreamChunk(
                                            tool_calls=[{
                                                "index": tidx,
                                                "id": call_id,
                                                "function": {
                                                    "name": name,
                                                    "arguments": item.get("arguments", ""),
                                                },
                                            }],
                                            finish_reason="tool_calls",
                                        )
                                    else:
                                        tidx = call_id_to_idx.get(call_id, 0)
                                        yield StreamChunk(
                                            tool_calls=[{
                                                "index": tidx,
                                                "id": call_id,
                                                "function": {
                                                    "name": name,
                                                    "arguments": "",
                                                },
                                            }],
                                            finish_reason="tool_calls",
                                        )

                            elif event_type == "response.completed":
                                usage = data.get("response", {}).get("usage", {})
                                if usage:
                                    yield StreamChunk(usage={
                                        "prompt_tokens": usage.get("input_tokens", 0),
                                        "completion_tokens": usage.get("output_tokens", 0),
                                        "total_tokens": usage.get("total_tokens", 0),
                                    })

                self._event_bus.emit(EVT_AI_STREAM_DONE)
                return  # Success — exit retry loop

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 3 * (attempt + 1)
                    logger.warning(
                        "Connection failed (attempt %d/%d): %s. Retrying in %ds...",
                        attempt + 1, max_retries, type(e).__name__, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("All %d connection attempts failed", max_retries)

            except Exception as e:
                error_msg = sanitize_error(str(e))
                logger.exception("OpenAI subscription streaming error")
                self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
                yield StreamChunk(
                    delta_content=f"\n\n**Error:** {error_msg}"
                )
                return

        # All retries exhausted
        error_msg = (
            f"Connection timed out after {max_retries} attempts. "
            "Check your internet connection or try again."
        )
        self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
        yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")

    async def test_connection(self) -> tuple[bool, str]:
        if not self._access_token:
            return False, "Not logged in"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream(
                    "POST",
                    CODEX_RESPONSES_URL,
                    headers=self._get_headers(),
                    json={
                        "model": "gpt-5.4-mini",
                        "instructions": "Be brief.",
                        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Say ok"}]}],
                        "store": False,
                        "stream": True,
                    },
                ) as resp:
                    if resp.status_code == 200:
                        return True, "Connected via ChatGPT subscription"
                    body = await resp.aread()
                    logger.error("OpenAI test_connection failed %d: %s",
                                 resp.status_code, sanitize_error(body.decode(errors="replace")[:500]))
                    return False, f"HTTP {resp.status_code}: Connection test failed"
        except Exception as e:
            return False, sanitize_error(str(e))

    def logout(self, clear_disk: bool = True) -> str:
        """Clear tokens from memory and optionally from disk.

        NOTE: This is a local-only sign-out. Tokens are not revoked with the
        provider. If tokens were copied elsewhere, they may remain valid until
        they expire. Users should rotate credentials if compromise is suspected.

        Args:
            clear_disk: If True, also remove tokens from ~/.codex/auth.json.
                        The file is kept (for Codex CLI) but tokens are emptied.

        Returns:
            A status message indicating the logout scope.
        """
        self._access_token = None
        self._refresh_token = None

        if clear_disk and CODEX_AUTH_FILE.exists():
            try:
                from polyglot_ai.core.security import secure_write
                data = json.loads(CODEX_AUTH_FILE.read_text())
                if "tokens" in data and isinstance(data["tokens"], dict):
                    data["tokens"]["access_token"] = None
                    data["tokens"]["refresh_token"] = None
                    secure_write(CODEX_AUTH_FILE, json.dumps(data, indent=2))
            except Exception:
                logger.exception("Failed to clear tokens from disk")

        logger.info("OpenAI OAuth logged out (local tokens cleared, not revoked remotely)")
        return "Signed out locally. Tokens were not revoked with OpenAI."
