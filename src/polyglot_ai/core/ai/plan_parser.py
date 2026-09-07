"""Parse AI output into structured Plan objects."""

from __future__ import annotations

import json
import logging
import re

from polyglot_ai.core.ai.plan_models import Plan, PlanStep

logger = logging.getLogger(__name__)


def parse_plan_from_tool_call(arguments: str, original_request: str = "") -> Plan:
    """Parse a create_plan tool call JSON into a Plan object."""
    data = json.loads(arguments)
    steps = []
    for i, step_data in enumerate(data.get("steps", [])):
        steps.append(
            PlanStep(
                index=i,
                title=step_data.get("title", f"Step {i + 1}"),
                description=step_data.get("description", ""),
                files_affected=step_data.get("files_affected", []),
            )
        )
    return Plan(
        title=data.get("title", "Implementation Plan"),
        summary=data.get("summary", ""),
        steps=steps,
        original_request=original_request,
    )


def extract_file_paths(text: str) -> list[str]:
    """Extract file paths from text (e.g. src/foo/bar.py)."""
    pattern = r"(?:^|\s|`)((?:[\w.-]+/)+[\w.-]+\.[\w]+)"
    matches = re.findall(pattern, text)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result
