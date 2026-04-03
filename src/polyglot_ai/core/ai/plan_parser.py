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
        steps.append(PlanStep(
            index=i,
            title=step_data.get("title", f"Step {i + 1}"),
            description=step_data.get("description", ""),
            files_affected=step_data.get("files_affected", []),
        ))
    return Plan(
        title=data.get("title", "Implementation Plan"),
        summary=data.get("summary", ""),
        steps=steps,
        original_request=original_request,
    )


def parse_plan_from_markdown(text: str, original_request: str = "") -> Plan | None:
    """Fallback: extract a plan from numbered markdown steps.

    Looks for patterns like:
    1. **Title** — description
    2. Title: description
    """
    # Try to find a title from headers
    title_match = re.search(r"^#+\s*(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Implementation Plan"

    # Find numbered steps
    step_pattern = re.compile(
        r"^(\d+)[.)]\s+"              # Step number
        r"(?:\*\*(.+?)\*\*\s*[-—:]?\s*)?"  # Optional bold title
        r"(.+)$",                      # Description
        re.MULTILINE,
    )

    steps: list[PlanStep] = []
    for match in step_pattern.finditer(text):
        idx = int(match.group(1)) - 1
        step_title = match.group(2) or match.group(3).split(".")[0].strip()
        description = match.group(3).strip()
        files = extract_file_paths(description)

        steps.append(PlanStep(
            index=idx,
            title=step_title[:80],
            description=description,
            files_affected=files,
        ))

    if not steps:
        return None

    # Re-index
    for i, step in enumerate(steps):
        step.index = i

    # Extract summary from text before first step
    first_step_pos = text.find("1.")
    summary = text[:first_step_pos].strip() if first_step_pos > 0 else ""
    # Clean markdown from summary
    summary = re.sub(r"^#+\s*.+$", "", summary, flags=re.MULTILINE).strip()
    summary = summary[:200]

    return Plan(
        title=title,
        summary=summary,
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
