"""AI plan generator for tasks.

Given a :class:`Task` (typically a freshly created FEATURE task), call
the configured AI provider and ask it for a short, ordered checklist
of concrete steps the user should take to ship the work. The result
is parsed into a list of :class:`PlanStep` objects which the
:class:`TaskManager` then attaches to the task and persists.

This is intentionally a thin engine — it follows the same shape as
``review_engine.ReviewEngine``: a single ``provider_manager`` is
injected at construction time, ``_resolve_provider`` picks (or falls
back to) a model, and ``stream_chat`` is consumed into a single
string before parsing. The UI is responsible for triggering the
generation; this module never touches Qt or the event bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from polyglot_ai.core.tasks import PlanStep, Task

logger = logging.getLogger(__name__)


# Hard caps so a misbehaving model can't blow up the UI or storage.
_MIN_STEPS = 3
_MAX_STEPS = 12
_MAX_STEP_LEN = 240


PLAN_SYSTEM_PROMPT = """You are an expert software engineer helping a developer plan a piece of work.

You will be given a task title and description. Produce a short, ordered
checklist of concrete steps the developer should take to ship it. Each step
must be:

- A single, actionable sentence in the imperative mood ("Add a CSV writer",
  "Wire the export button", "Cover edge cases with tests").
- Small enough to complete in one focused sitting (roughly 15-60 minutes).
- Free of pleasantries, hedging, or restating the task.

Aim for between 4 and 8 steps. Never fewer than 3, never more than 12.
Cover the obvious phases for the kind of task you are given:

- For features: design/spike, implementation, tests, docs, manual verification.
- For bugfixes: reproduce, write a failing test, fix, regression-test, document.
- For refactors: identify call sites, refactor, run tests, smoke-test the app.
- For incidents: stabilise, root-cause, fix, add monitoring/test, write postmortem.

Return ONLY a JSON object with this exact shape, no commentary:

{
  "steps": [
    {"text": "First step here"},
    {"text": "Second step here"}
  ]
}
"""


@dataclass
class PlanGenerationResult:
    """Outcome of a single plan-generation call.

    Either ``steps`` is non-empty (success) or ``error`` is set
    (failure). Callers should treat them as mutually exclusive and
    surface the error to the user when present.
    """

    steps: list[PlanStep]
    model: str = ""
    provider: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.steps) and not self.error


class PlanGenerator:
    """Asks an AI provider for a task plan and returns parsed steps."""

    def __init__(self, provider_manager) -> None:
        self._provider_manager = provider_manager

    async def generate(self, task: Task, model_id: str = "") -> PlanGenerationResult:
        """Generate a plan for ``task``.

        Resolves a provider (preferring ``model_id`` if it matches a
        registered provider, otherwise falling back to the first
        configured one), streams the completion, and parses the JSON
        body. Network errors, empty responses, and parse failures all
        return a :class:`PlanGenerationResult` with ``error`` set
        rather than raising — the caller never has to wrap this in a
        try/except.
        """
        if not task.title.strip():
            return PlanGenerationResult(
                steps=[], error="Cannot generate a plan for an untitled task."
            )

        resolved = await self._resolve_provider(model_id)
        if isinstance(resolved, PlanGenerationResult):
            return resolved
        provider, model = resolved

        user_prompt = self._build_user_prompt(task)
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        full_response = ""
        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model,
                temperature=0.2,
                max_tokens=1500,
            ):
                if chunk.delta_content:
                    full_response += chunk.delta_content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("plan_generator: streaming failed")
            return PlanGenerationResult(
                steps=[],
                model=model,
                provider=provider.name,
                error=f"Provider call failed: {exc}",
            )

        steps = self._parse(full_response)
        if not steps:
            return PlanGenerationResult(
                steps=[],
                model=model,
                provider=provider.name,
                error="Could not parse a plan from the model response.",
            )
        return PlanGenerationResult(steps=steps, model=model, provider=provider.name)

    # ── Internals ──────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(task: Task) -> str:
        parts = [
            f"Task kind: {task.kind.value}",
            f"Title: {task.title}",
        ]
        if task.description.strip():
            parts.append("")
            parts.append("Description:")
            parts.append(task.description.strip())
        parts.append("")
        parts.append("Produce the JSON checklist now.")
        return "\n".join(parts)

    @staticmethod
    def _parse(raw: str) -> list[PlanStep]:
        """Extract a ``[PlanStep, ...]`` list from a model response.

        The prompt asks for pure JSON, but real models often wrap it
        in a ``json`` fenced block or add a stray sentence. We try
        the strictest parse first, then fall back to grabbing the
        first balanced ``{...}`` substring.
        """
        if not raw.strip():
            return []

        candidates = [raw.strip()]
        # Strip a leading/trailing markdown fence if present.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fence_match:
            candidates.append(fence_match.group(1))
        # Last resort: first {...} block.
        brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace_match:
            candidates.append(brace_match.group(0))

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            steps_raw = payload.get("steps") if isinstance(payload, dict) else None
            if not isinstance(steps_raw, list):
                continue
            steps: list[PlanStep] = []
            for entry in steps_raw:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("text", "")).strip()
                if not text:
                    continue
                if len(text) > _MAX_STEP_LEN:
                    text = text[: _MAX_STEP_LEN - 1].rstrip() + "…"
                steps.append(PlanStep(text=text))
                if len(steps) >= _MAX_STEPS:
                    break
            if len(steps) >= _MIN_STEPS:
                return steps
        return []

    async def _resolve_provider(self, model_id: str):
        """Resolve a provider/model pair, falling back if needed.

        Returns ``(provider, model)`` on success or a
        :class:`PlanGenerationResult` with ``error`` set that the
        caller must return directly.
        """
        result = self._provider_manager.get_provider_for_model(model_id) if model_id else None
        if result:
            return result

        if model_id:
            logger.warning(
                "plan_generator: model_id %r not found, falling back to first provider", model_id
            )

        providers = self._provider_manager.get_all_providers()
        if not providers:
            return PlanGenerationResult(
                steps=[],
                error="No AI provider configured. Add an API key in Settings.",
            )
        provider = providers[0]
        try:
            models = await provider.list_models()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("plan_generator: list_models failed for %s", provider.display_name)
            return PlanGenerationResult(
                steps=[],
                provider=provider.name,
                error=f"Could not list models from {provider.display_name}: {exc}",
            )
        if not models:
            return PlanGenerationResult(
                steps=[],
                provider=provider.name,
                error=f"{provider.display_name} returned no models.",
            )
        return provider, models[0]
