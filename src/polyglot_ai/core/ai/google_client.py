"""Google provider — implements AIProvider for the Gemini API."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from google import genai
from google.genai import types

from polyglot_ai.core.ai.models import StreamChunk
from polyglot_ai.core.ai.provider import AIProvider, ModelListCache
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
]


class GoogleClient(AIProvider):
    """Google (Gemini) provider with async streaming."""

    def __init__(self, api_key: str, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        # Do not store the raw API key as an attribute — pass it directly to
        # the SDK so it cannot appear in heap dumps or repr() output.
        self._client = genai.Client(api_key=api_key)
        self._model_cache = ModelListCache(DEFAULT_MODELS, "Google")

    @property
    def name(self) -> str:
        return "google"

    @property
    def display_name(self) -> str:
        return "Google"

    def update_api_key(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def list_models(self) -> list[str]:
        async def _fetch() -> list[str]:
            # genai's models.list() is a synchronous, blocking iterator.
            # Run it on a worker thread so we don't stall the event
            # loop (network latency on a cold call has been observed
            # at ~1s, which freezes the UI). The outer cache still
            # ensures this only runs at most once per TTL window.
            import asyncio as _asyncio

            def _list_sync() -> list[str]:
                models: list[str] = []
                for model in self._client.models.list():
                    model_id = model.name
                    if model_id.startswith("models/"):
                        model_id = model_id[7:]
                    if "gemini" in model_id:
                        models.append(model_id)
                return models

            models = await _asyncio.to_thread(_list_sync)
            # De-duplicate before the cache sorts. The cache sorts a
            # list; a set would change iteration order per run, so we
            # normalise here.
            return sorted(set(models))

        return await self._model_cache.get(_fetch)

    async def stream_chat(
        self,
        messages: list[dict],
        model: str = "gemini-2.5-flash",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        try:
            # Convert messages to Gemini format
            import json as _json

            # Map each tool_call id → function name so a later role="tool"
            # result can be attached to the right function_response (Gemini
            # keys responses by function name, not by call id).
            tool_name_by_id: dict[str, str] = {}
            for msg in messages:
                for tc in msg.get("tool_calls") or []:
                    tool_name_by_id[tc.get("id", "")] = tc.get("function", {}).get("name", "")

            gemini_contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    if not system_prompt:
                        system_prompt = content
                    continue

                if role == "tool":
                    # Tool result → Gemini function_response part on a
                    # "user"-role turn. Without this the result was sent
                    # as plain "model" text, so Gemini never saw a proper
                    # response and would re-issue the same call.
                    name = tool_name_by_id.get(msg.get("tool_call_id", ""), "tool")
                    gemini_contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_function_response(
                                    name=name or "tool",
                                    response={"result": content if content is not None else ""},
                                )
                            ],
                        )
                    )
                    continue

                if role == "assistant" and msg.get("tool_calls"):
                    # Assistant turn that called tools → function_call
                    # parts (plus any leading text). Previously the tool
                    # calls were dropped entirely and an empty-text part
                    # was sent, corrupting multi-turn tool conversations.
                    parts = []
                    if content:
                        parts.append(types.Part.from_text(text=content))
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        raw_args = fn.get("arguments", "") or "{}"
                        try:
                            call_args = _json.loads(raw_args) if raw_args else {}
                        except (ValueError, TypeError):
                            call_args = {}
                        parts.append(
                            types.Part.from_function_call(
                                name=fn.get("name", ""),
                                args=call_args,
                            )
                        )
                    gemini_contents.append(types.Content(role="model", parts=parts))
                    continue

                gemini_role = "user" if role == "user" else "model"
                gemini_contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=[types.Part.from_text(text=content or "")],
                    )
                )

            if not gemini_contents:
                gemini_contents = [
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="Hello")],
                    )
                ]

            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

            if system_prompt:
                config.system_instruction = system_prompt

            # Translate OpenAI-format tool definitions to Gemini function declarations
            if tools:
                gemini_tools = []
                for tool_def in tools:
                    func = tool_def.get("function", {})
                    func_name = func.get("name", "")
                    func_desc = func.get("description", "")
                    params = func.get("parameters", {})
                    if func_name:
                        gemini_tools.append(
                            types.Tool(
                                function_declarations=[
                                    types.FunctionDeclaration(
                                        name=func_name,
                                        description=func_desc,
                                        parameters=params if params.get("properties") else None,
                                    )
                                ]
                            )
                        )
                if gemini_tools:
                    config.tools = gemini_tools

            total_text = ""
            # Running counter so each function call gets a unique index
            # across all streaming chunks (not just within a single chunk).
            next_tool_idx = 0
            # Track whether the model asked to call any tool so we can
            # signal ``finish_reason="tool_calls"`` at the end. Without
            # it, the agent loop (agent.py) and plan executor break out
            # immediately and Gemini tool calls never execute.
            emitted_tool_call = False

            async for chunk in self._client.aio.models.generate_content_stream(
                model=model,
                contents=gemini_contents,
                config=config,
            ):
                if chunk.text:
                    total_text += chunk.text
                    yield self._emit_text_delta(chunk.text)

                # Extract function calls from Gemini response. Gemini
                # returns whole calls (no fragmentation), so we emit a
                # single start-chunk with the full id+name and the
                # args already packed into the ``arguments`` field.
                # OpenAI-compatible shape is preserved via
                # :meth:`_tool_call_start_chunk` then a direct
                # args-dict overwrite — cleaner than faking a split.
                if (
                    hasattr(chunk, "candidates")
                    and chunk.candidates
                    and chunk.candidates[0].content
                    and chunk.candidates[0].content.parts
                ):
                    for part in chunk.candidates[0].content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            fc = part.function_call
                            import json as _json

                            tidx = next_tool_idx
                            next_tool_idx += 1
                            args_str = _json.dumps(dict(fc.args)) if fc.args else "{}"
                            sc = self._tool_call_start_chunk(
                                tidx, f"call_{fc.name}_{tidx}", fc.name
                            )
                            # Gemini gives us the full args at once,
                            # not fragments — patch the builder shape
                            # in place rather than emit a start+args pair.
                            sc.tool_calls[0]["function"]["arguments"] = args_str
                            yield sc
                            emitted_tool_call = True

                # Check for usage metadata
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    um = chunk.usage_metadata
                    yield StreamChunk(
                        usage={
                            "prompt_tokens": getattr(um, "prompt_token_count", 0) or 0,
                            "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
                            "total_tokens": getattr(um, "total_token_count", 0) or 0,
                        }
                    )

            # Mirror the other providers: a terminal chunk carrying the
            # stop reason. The agent/plan loops key tool execution off
            # ``finish_reason in ("tool_calls", "tool_use")``.
            if emitted_tool_call:
                yield StreamChunk(finish_reason="tool_calls")

            self._emit_stream_done()

        except Exception as e:
            yield self._handle_stream_error(e)

    async def test_connection(self) -> tuple[bool, str]:
        # The genai SDK's models.list() is a synchronous blocking iterator
        # (same as in list_models). Calling it directly on the event loop
        # freezes the UI for ~1 s on a cold call. Run it on a worker
        # thread via asyncio.to_thread, matching the pattern used above.
        import asyncio as _asyncio

        async def _check() -> list[str]:
            return await _asyncio.to_thread(lambda: list(self._client.models.list()))

        return await self._test_connection_via_list(_check)
