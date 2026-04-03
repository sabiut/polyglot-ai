"""AI-powered inline code completion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polyglot_ai.core.ai.provider import AIProvider

logger = logging.getLogger(__name__)

COMPLETION_SYSTEM_PROMPT = (
    "You are a code completion engine. Complete the code at the cursor position. "
    "Output ONLY the completion text — no explanation, no markdown, no code fences. "
    "Keep completions short (1-3 lines). If no useful completion, output nothing."
)


async def get_completion(
    provider: AIProvider,
    model: str,
    prefix: str,
    suffix: str,
    language: str,
) -> str:
    """Get an AI code completion for the given context.

    Args:
        provider: AI provider to use.
        model: Model ID.
        prefix: Code before the cursor.
        suffix: Code after the cursor.
        language: Programming language.

    Returns:
        The suggested completion text, or empty string.
    """
    prompt = f"Language: {language}\n\n```\n{prefix}<CURSOR>{suffix}\n```\n\nComplete at <CURSOR>:"

    messages = [
        {"role": "user", "content": prompt},
    ]

    result_parts = []
    try:
        async for chunk in provider.stream_chat(
            messages=messages,
            model=model,
            system_prompt=COMPLETION_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.2,
        ):
            if chunk.delta_content:
                result_parts.append(chunk.delta_content)
    except Exception as e:
        logger.debug("Completion request failed: %s", e)
        return ""

    result = "".join(result_parts).strip()
    # Strip any markdown code fences that slipped through
    if result.startswith("```"):
        lines = result.split("\n")
        result = "\n".join(lines[1:])
    if result.endswith("```"):
        result = result[:-3].rstrip()

    return result
