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
        self._api_key = api_key
        self._client = genai.Client(api_key=api_key)
        self._model_cache = ModelListCache(DEFAULT_MODELS, "Google")

    @property
    def name(self) -> str:
        return "google"

    @property
    def display_name(self) -> str:
        return "Google"

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = genai.Client(api_key=api_key)

    async def list_models(self) -> list[str]:
        async def _fetch() -> list[str]:
            # genai's list() is sync; iterate inline (the outer cache
            # ensures this only runs at most once per TTL window).
            models: list[str] = []
            for model in self._client.models.list():
                model_id = model.name
                if model_id.startswith("models/"):
                    model_id = model_id[7:]
                if "gemini" in model_id:
                    models.append(model_id)
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
            gemini_contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    if not system_prompt:
                        system_prompt = content
                    continue
                gemini_role = "user" if role == "user" else "model"
                gemini_contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=[types.Part.from_text(text=content)],
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

            self._emit_stream_done()

        except Exception as e:
            yield self._handle_stream_error(e)

    async def test_connection(self) -> tuple[bool, str]:
        # The genai SDK's list() is synchronous, so wrap it in an async
        # lambda for the shared helper. An empty-list case reports
        # success-ish (connected but no models) — treat it the same as
        # a successful connection rather than a failure, matching the
        # other providers' "Connection successful" outcome.
        async def _check() -> list[str]:
            return list(self._client.models.list())

        return await self._test_connection_via_list(_check)
