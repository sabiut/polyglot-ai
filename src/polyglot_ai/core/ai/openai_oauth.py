"""OpenAI OAuth provider — uses ChatGPT subscription via Codex Responses API.

Reads tokens from ~/.codex/auth.json (created by `npx -y @openai/codex@latest login`).
Uses the Codex Responses API at chatgpt.com/backend-api/codex/responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
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
    "gpt-5.5",
    "gpt-5.4",
]


@dataclass(frozen=True)
class CodexAvailability:
    """Three-state result of probing for a working npx/Codex install.

    ``ok=True`` means a Codex login attempt will at least reach
    the OAuth flow. ``ok=False`` requires the dialog to surface
    *different* messages depending on ``reason`` — telling the
    user to install Node when Node *is* installed (just broken)
    leaves them stuck.
    """

    ok: bool
    reason: str  # one of: "ok", "missing", "broken"
    detail: str  # short stderr/exception text for the broken case

    @classmethod
    def from_probe(cls) -> "CodexAvailability":
        """Run ``npx --version`` with a 10s timeout and classify."""
        try:
            result = subprocess.run(["npx", "--version"], capture_output=True, timeout=10)
        except FileNotFoundError:
            # ``npx`` not on PATH at all — actual missing-Node case.
            return cls(ok=False, reason="missing", detail="")
        except (OSError, subprocess.TimeoutExpired) as exc:
            # ``npx`` exists but doesn't run (permission denied,
            # hung, broken symlink). Treat as broken — the binary
            # is there, something else is wrong.
            return cls(ok=False, reason="broken", detail=str(exc)[:200])

        if result.returncode == 0:
            return cls(ok=True, reason="ok", detail="")
        # ``npx`` ran and exited non-zero — broken install (bad npm
        # cache, missing modules, EACCES on ~/.npm, etc.). Capture
        # stderr so the dialog can quote it back.
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        return cls(ok=False, reason="broken", detail=stderr[:200])


class OpenAIOAuthClient(AIProvider):
    """OpenAI provider using ChatGPT subscription via Codex Responses API."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
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
            data = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
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
                    new_access = data.get("access_token")
                    # Validate before mutating state — a 200 with a
                    # missing or empty ``access_token`` would otherwise
                    # leave us with ``self._access_token = None`` and
                    # a returned ``True``, causing the caller to send
                    # ``Authorization: Bearer None`` on its next retry.
                    if not isinstance(new_access, str) or not new_access:
                        logger.warning("Token refresh returned 200 without a usable access_token")
                        return False
                    self._access_token = new_access
                    if data.get("refresh_token"):
                        self._refresh_token = data["refresh_token"]
                    self._save_tokens()
                    logger.info("Refreshed OpenAI access token")
                    return True
                # Non-200 — log status + sanitized body so an expired
                # or revoked refresh token (most common cause of 400/
                # 401) is diagnosable without trawling the OAuth
                # provider's logs. Without this, refresh failures were
                # silent and looked indistinguishable from network
                # errors to the caller.
                logger.warning(
                    "OpenAI token refresh failed %d: %s",
                    resp.status_code,
                    sanitize_error(resp.text[:500]),
                )
                return False
        except Exception:
            logger.exception("Token refresh error")
            return False

    def _save_tokens(self) -> None:
        if not CODEX_AUTH_FILE.exists():
            return
        try:
            from polyglot_ai.core.security import secure_write

            data = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
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
        # Note: ``capture_output=False`` is intentional — codex login is
        # interactive and prints its OAuth URL/PIN to the terminal.
        # Capturing would hide that prompt from the user. We pay for
        # that with no stderr to log on failure; instead, surface the
        # exit code so the UI can hint at "see the terminal where you
        # launched the app" if it's non-zero. ``is_codex_available()``
        # should be called before this to catch the broken-npx case
        # cheaply; if you got here, npx was working a moment ago but
        # the login itself failed.
        try:
            result = subprocess.run(
                ["npx", "-y", "@openai/codex@latest", "login"],
                timeout=180,
                capture_output=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "codex login exited with code %d — see the terminal where you "
                    "launched the app for details (the login command prints to stderr "
                    "interactively, so we can't relay it here).",
                    result.returncode,
                )
                return False
            return True
        except FileNotFoundError:
            logger.error("npx not found. Install Node.js: sudo apt install nodejs npm")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("codex login timed out after 180s")
            return False
        except Exception:
            logger.exception("Codex login failed")
            return False

    @staticmethod
    def is_codex_available() -> bool:
        """Return True iff ``npx`` is on PATH, answers within 10s, AND
        exits cleanly.

        Backward-compat wrapper around :meth:`codex_availability`.
        Prefer that method for UI flows so the message can
        differentiate "missing" from "broken".
        """
        return CodexAvailability.from_probe().ok

    @staticmethod
    def codex_availability() -> "CodexAvailability":
        """Three-state Codex/npx probe for UI flows.

        Returns one of:

        * ``ok=True`` — Node + npx work.
        * ``ok=False`` with ``reason="missing"`` — ``npx`` isn't on
          PATH at all. The fix is "install Node.js".
        * ``ok=False`` with ``reason="broken"`` — ``npx`` is on PATH
          but errored (broken npm cache, bad permissions on the
          cache dir, corrupt symlinks). The fix is "run ``npx
          --version`` in a terminal and look at the error" — telling
          the user to install Node would just confuse them when
          Node IS installed.

        Separating the two halves means the dialog can show the
        right call-to-action instead of a one-size-fits-all message.
        """
        return CodexAvailability.from_probe()

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
        model: str = "gpt-5.5",
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
            if role == "tool":
                # Tool result → Responses API function_call_output
                input_messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id", ""),
                        "output": str(content) if content else "",
                    }
                )
                continue
            if role == "assistant" and msg.get("tool_calls"):
                # Assistant message with tool calls → emit text + function_call items
                if content:
                    input_messages.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": str(content)}],
                        }
                    )
                for tc_idx, tc in enumerate(msg["tool_calls"]):
                    fn = tc.get("function", {})
                    call_id = tc.get("id", "") or ""
                    # Responses API requires ``id`` to start with
                    # ``fc_`` and ``call_id`` to be the original
                    # ``call_...`` identifier. Normalise defensively
                    # for ids that don't match either prefix (custom
                    # provider ids, persisted conversations from older
                    # builds, empty strings from malformed messages).
                    if call_id.startswith("fc_"):
                        fc_id = call_id
                    elif call_id.startswith("call_"):
                        fc_id = "fc_" + call_id[len("call_") :]
                    elif call_id:
                        fc_id = f"fc_{call_id}"
                    else:
                        # Empty call_id — synthesize unique stable ids
                        # using the loop index so multiple
                        # missing-id tool calls in the same message
                        # don't collapse onto the same ``fc_unknown_…``
                        # value. Both ``id`` and ``call_id`` get the
                        # synthetic so the function_call_output that
                        # references this call later in the
                        # conversation can match by ``call_id``.
                        fn_name = fn.get("name", "tool")
                        fc_id = f"fc_unknown_{fn_name}_{tc_idx}"
                        call_id = f"call_unknown_{fn_name}_{tc_idx}"
                    input_messages.append(
                        {
                            "type": "function_call",
                            "id": fc_id,
                            "call_id": call_id,
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", ""),
                        }
                    )
                continue
            if role in ("user", "assistant"):
                # Responses API requires structured content items
                if isinstance(content, list):
                    # Already multimodal (text + images) — convert each part.
                    # Skip non-dict entries defensively: persisted or
                    # provider-generated messages occasionally contain raw
                    # strings, ``None``, or other shapes inside the list,
                    # and ``.get()`` on those would raise AttributeError
                    # and surface as a generic "streaming failed" instead
                    # of a recognisable malformed-message error.
                    parts = []
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "text":
                            ct = "input_text" if role == "user" else "output_text"
                            parts.append({"type": ct, "text": part.get("text", "")})
                        elif part.get("type") == "image_url" and role == "user":
                            # Convert OpenAI image_url to Responses API
                            # ``input_image``. Only emitted for user
                            # messages — assistant content blocks should
                            # never carry incoming images, and the
                            # Responses API rejects ``input_*`` types
                            # under an ``assistant`` role item. If a
                            # legacy stored conversation has an image
                            # part on an assistant message, we drop it
                            # silently rather than emit an invalid
                            # request.
                            url = part.get("image_url", {}).get("url", "")
                            parts.append({"type": "input_image", "image_url": url})
                    input_messages.append({"role": role, "content": parts})
                else:
                    ct = "input_text" if role == "user" else "output_text"
                    input_messages.append(
                        {
                            "role": role,
                            "content": [{"type": ct, "text": str(content)}],
                        }
                    )

        if not input_messages:
            input_messages = [
                {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}
            ]

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
                    responses_tools.append(
                        {
                            "type": "function",
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        }
                    )
            if responses_tools:
                payload["tools"] = responses_tools

        max_retries = 3
        # Refresh-once flag: on a 401 we attempt a token refresh and
        # retry within this loop. We deliberately do NOT recurse into
        # ``stream_chat()`` (the previous implementation), because if
        # the refreshed token also returned 401 the call would recurse
        # again on the same code path, producing an unbounded retry
        # cycle that ate the stack rather than failing cleanly.
        refresh_attempted = False
        # Track the cause of the most-recent failure so the
        # all-retries-exhausted error reflects what *actually* went
        # wrong on the final attempt. The previous version used a
        # sticky boolean that latched True on any 429 — a 429-then-
        # connection-error sequence would still report "rate limited"
        # at the end even though the real final failure was the
        # network. Possible values:
        #   - None: no failure yet
        #   - "rate_limited": the last attempt returned 429
        #   - "connection": the last attempt raised a httpx network error
        last_failure: str | None = None

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
                            # Try refreshing the token at most once. If
                            # the next attempt is still 401, fall
                            # through to the "session expired" message
                            # rather than retrying forever.
                            if not refresh_attempted and await self._refresh_access_token():
                                refresh_attempted = True
                                continue
                            yield StreamChunk(
                                delta_content="\n\n**Error:** Session expired. "
                                "Please sign in again via Settings."
                            )
                            return

                        if resp.status_code == 429:
                            last_failure = "rate_limited"
                            # Don't sleep on the final attempt — we'd
                            # just wait then immediately fall through to
                            # the all-retries-exhausted error, wasting
                            # the user's time.
                            if attempt < max_retries - 1:
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
                                user_msg = (
                                    "Invalid request. The model may not support this operation."
                                )
                            elif 500 <= status < 600:
                                user_msg = "Provider server error. Please try again later."
                            else:
                                user_msg = f"Provider returned HTTP {status}. See logs for details."
                            yield StreamChunk(delta_content=f"\n\n**Error:** {user_msg}")
                            return

                        # Track unique tool call indices by call_id so
                        # multiple parallel tool calls don't get merged.
                        call_id_to_idx: dict[str, int] = {}
                        call_id_names: dict[str, str] = {}
                        # Map item_id -> call_id (delta events use item_id)
                        item_id_to_call_id: dict[str, str] = {}
                        # Track which calls got their args via deltas vs done
                        call_id_has_deltas: set[str] = set()
                        # Track which calls have already emitted their full
                        # arguments — separate from "had deltas" so the
                        # ``arguments.done`` and ``output_item.done`` branches
                        # don't double-emit when neither delta arrived. The
                        # agent loop accumulates by index, so a duplicate
                        # emission produces ``{"path":"x"}{"path":"x"}`` and
                        # breaks JSON parsing on the tool call.
                        call_id_args_emitted: set[str] = set()
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
                                logger.debug(
                                    "Responses API event: %s data_keys=%s",
                                    event_type,
                                    list(data.keys())[:10],
                                )
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
                                    item_id = item.get("id", "")
                                    # Use ``item_id`` as a fallback key
                                    # when the API didn't send a
                                    # ``call_id`` yet. Without this,
                                    # multiple partial events with empty
                                    # ``call_id`` would all collide on
                                    # the same dict slot ("") and the
                                    # agent would see them merged into
                                    # one tool call with corrupted args.
                                    call_key = call_id or item_id
                                    if not call_key:
                                        logger.warning(
                                            "Responses API: output_item.added without "
                                            "call_id or id; skipping"
                                        )
                                        continue
                                    name = item.get("name", "")
                                    # Map item_id to call_id for delta events
                                    if item_id and call_id:
                                        item_id_to_call_id[item_id] = call_id
                                    if call_key not in call_id_to_idx:
                                        call_id_to_idx[call_key] = next_tool_idx
                                        next_tool_idx += 1
                                    if name:
                                        call_id_names[call_key] = name
                                    tidx = call_id_to_idx[call_key]
                                    yield StreamChunk(
                                        tool_calls=[
                                            {
                                                "index": tidx,
                                                "id": call_id or call_key,
                                                "function": {
                                                    "name": name,
                                                    "arguments": "",
                                                },
                                            }
                                        ],
                                    )

                            elif event_type == "response.function_call_arguments.delta":
                                # Delta events use item_id, not call_id
                                item_id = data.get("item_id", "")
                                call_key = (
                                    data.get("call_id")
                                    or item_id_to_call_id.get(item_id)
                                    or item_id
                                )
                                if not call_key:
                                    logger.warning(
                                        "Responses API: function_call_arguments.delta "
                                        "without call_id or item_id; skipping"
                                    )
                                    continue
                                if call_key not in call_id_to_idx:
                                    call_id_to_idx[call_key] = next_tool_idx
                                    next_tool_idx += 1
                                tidx = call_id_to_idx[call_key]
                                name = data.get("name") or call_id_names.get(call_key)
                                call_id_has_deltas.add(call_key)
                                call_id_args_emitted.add(call_key)
                                yield StreamChunk(
                                    tool_calls=[
                                        {
                                            "index": tidx,
                                            "id": call_key,
                                            "function": {
                                                "name": name,
                                                "arguments": data.get("delta", ""),
                                            },
                                        }
                                    ],
                                )

                            elif event_type == "response.function_call_arguments.done":
                                # Complete arguments for one call. If we
                                # already got deltas, skip (they accumulated).
                                # If no deltas arrived, use this as the source.
                                # Mark args as emitted so the later
                                # ``output_item.done`` event doesn't re-yield
                                # the same arguments and corrupt the JSON.
                                item_id = data.get("item_id", "")
                                call_key = (
                                    data.get("call_id")
                                    or item_id_to_call_id.get(item_id)
                                    or item_id
                                )
                                if not call_key:
                                    logger.warning(
                                        "Responses API: function_call_arguments.done "
                                        "without call_id or item_id; skipping"
                                    )
                                    continue
                                if call_key not in call_id_args_emitted:
                                    if call_key not in call_id_to_idx:
                                        call_id_to_idx[call_key] = next_tool_idx
                                        next_tool_idx += 1
                                    tidx = call_id_to_idx[call_key]
                                    name = data.get("name") or call_id_names.get(call_key, "")
                                    call_id_args_emitted.add(call_key)
                                    yield StreamChunk(
                                        tool_calls=[
                                            {
                                                "index": tidx,
                                                "id": call_key,
                                                "function": {
                                                    "name": name,
                                                    "arguments": data.get("arguments", ""),
                                                },
                                            }
                                        ],
                                    )

                            elif event_type == "response.output_item.done":
                                item = data.get("item", {})
                                if item.get("type") == "function_call":
                                    call_id = item.get("call_id", "")
                                    item_id = item.get("id", "")
                                    call_key = call_id or item_id
                                    if not call_key:
                                        logger.warning(
                                            "Responses API: output_item.done function_call "
                                            "without call_id or id; skipping"
                                        )
                                        continue
                                    name = item.get("name", "") or call_id_names.get(call_key, "")
                                    if call_key not in call_id_to_idx:
                                        call_id_to_idx[call_key] = next_tool_idx
                                        next_tool_idx += 1
                                    tidx = call_id_to_idx[call_key]
                                    # Two cases, both must yield ``finish_reason``
                                    # so the agent loop sees the tool-call
                                    # completion signal:
                                    # - args NOT yet emitted → emit them now
                                    #   from the item payload (last-resort
                                    #   fallback when no delta and no
                                    #   ``arguments.done`` arrived)
                                    # - args already emitted → emit an empty
                                    #   args chunk just to carry the
                                    #   finish_reason without duplicating
                                    if call_key not in call_id_args_emitted:
                                        call_id_args_emitted.add(call_key)
                                        args_payload = item.get("arguments", "")
                                    else:
                                        args_payload = ""
                                    yield StreamChunk(
                                        tool_calls=[
                                            {
                                                "index": tidx,
                                                "id": call_id or call_key,
                                                "function": {
                                                    "name": name,
                                                    "arguments": args_payload,
                                                },
                                            }
                                        ],
                                        finish_reason="tool_calls",
                                    )

                            elif event_type == "response.completed":
                                usage = data.get("response", {}).get("usage", {})
                                if usage:
                                    yield StreamChunk(
                                        usage={
                                            "prompt_tokens": usage.get("input_tokens", 0),
                                            "completion_tokens": usage.get("output_tokens", 0),
                                            "total_tokens": usage.get("total_tokens", 0),
                                        }
                                    )

                self._event_bus.emit(EVT_AI_STREAM_DONE)
                return  # Success — exit retry loop

            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ) as e:
                last_failure = "connection"
                if attempt < max_retries - 1:
                    wait = 3 * (attempt + 1)
                    logger.warning(
                        "Connection failed (attempt %d/%d): %s. Retrying in %ds...",
                        attempt + 1,
                        max_retries,
                        type(e).__name__,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("All %d connection attempts failed", max_retries)

            except Exception as e:
                error_msg = sanitize_error(str(e))
                logger.exception("OpenAI subscription streaming error")
                self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
                yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")
                return

        # All retries exhausted. The error message reflects the cause
        # of the LAST failure, not whatever happened earlier in the
        # sequence — this matches what the user will hit if they retry
        # immediately.
        if last_failure == "rate_limited":
            error_msg = (
                f"Rate limited after {max_retries} attempts. "
                "Wait a moment before trying again, or reduce request frequency."
            )
        else:
            error_msg = (
                f"Connection timed out after {max_retries} attempts. "
                "Check your internet connection or try again."
            )
        self._event_bus.emit(EVT_AI_ERROR, error=error_msg)
        yield StreamChunk(delta_content=f"\n\n**Error:** {error_msg}")

    async def test_connection(self) -> tuple[bool, str]:
        if not self._access_token:
            return False, "Not logged in"
        # Try once, refresh-and-retry once on 401. Without the refresh
        # path the Settings UI would report "connection failed" for any
        # user whose access token expired even though streaming would
        # have succeeded after a quick refresh.
        #
        # Explicit flag instead of ``for x in (False, True)`` so the
        # control flow is obvious: at most two attempts, and the
        # second is only reached after a successful refresh.
        refresh_attempted = False
        for _attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    async with client.stream(
                        "POST",
                        CODEX_RESPONSES_URL,
                        headers=self._get_headers(),
                        json={
                            "model": "gpt-5.5",
                            "instructions": "Be brief.",
                            "input": [
                                {
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "Say ok"}],
                                }
                            ],
                            "store": False,
                            "stream": True,
                        },
                    ) as resp:
                        if resp.status_code == 200:
                            return True, "Connected via ChatGPT subscription"
                        if resp.status_code == 401 and not refresh_attempted:
                            refresh_attempted = True
                            if await self._refresh_access_token():
                                continue  # retry once with fresh token
                            return False, "Session expired. Please sign in again."
                        body = await resp.aread()
                        logger.error(
                            "OpenAI test_connection failed %d: %s",
                            resp.status_code,
                            sanitize_error(body.decode(errors="replace")[:500]),
                        )
                        return False, f"HTTP {resp.status_code}: Connection test failed"
            except Exception as e:
                return False, sanitize_error(str(e))
        # Fall-through: refresh succeeded once but the retry still 401'd.
        return False, "Session expired. Please sign in again."

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

                data = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
                if "tokens" in data and isinstance(data["tokens"], dict):
                    data["tokens"]["access_token"] = None
                    data["tokens"]["refresh_token"] = None
                    secure_write(CODEX_AUTH_FILE, json.dumps(data, indent=2))
            except Exception:
                logger.exception("Failed to clear tokens from disk")

        logger.info("OpenAI OAuth logged out (local tokens cleared, not revoked remotely)")
        return "Signed out locally. Tokens were not revoked with OpenAI."
