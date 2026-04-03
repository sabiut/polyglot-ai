"""Review engine — sends diff to AI and parses structured findings."""

from __future__ import annotations

import json
import logging
import re

from .diff_parser import format_diff_for_review, parse_diff
from .models import (
    Category,
    DiffFile,
    ReviewFinding,
    ReviewResult,
    Severity,
)

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. You will receive a unified diff of code changes.

Your task:
1. Review ONLY the changed code (lines starting with + or -).
2. Focus on real issues: bugs, security risks, logic errors, performance problems.
3. Do NOT nitpick style unless it harms readability.
4. Be specific — cite the exact file and line number.

Return your review as JSON with this exact structure:
{
  "summary": "Brief 1-3 sentence overview of the changes and their quality",
  "findings": [
    {
      "file": "path/to/file.py",
      "line": 42,
      "severity": "critical|high|medium|low|info",
      "category": "bug|security|performance|maintainability|style|tests|logic|error_handling|other",
      "title": "Short issue title",
      "body": "Detailed explanation of the issue and why it matters",
      "suggestion": "Optional: suggested fix as a code snippet"
    }
  ]
}

Rules:
- Only report issues you are confident about.
- severity "critical" = will cause crashes/data loss. "high" = likely bugs. "medium" = should fix. "low" = minor. "info" = observation.
- If the code looks good, return an empty findings array with a positive summary.
- Return ONLY valid JSON, no markdown fences, no extra text.
"""


class ReviewEngine:
    """Orchestrates code review using AI providers."""

    def __init__(self, provider_manager):
        self._provider_manager = provider_manager

    async def review_diff(
        self,
        diff_text: str,
        model_id: str = "",
        context_files: dict[str, str] | None = None,
    ) -> ReviewResult:
        """Review a unified diff and return structured findings.

        Args:
            diff_text: Raw unified diff output (from git diff).
            model_id: Provider-qualified model ID (e.g. "openai:gpt-5.4").
            context_files: Optional dict of {path: content} for extra context.
        """
        # Parse diff
        diff_files = parse_diff(diff_text)
        if not diff_files:
            return ReviewResult(
                summary="No changes to review.",
                files_reviewed=0,
            )

        # Build the review prompt
        diff_summary = format_diff_for_review(diff_files)
        total_add = sum(f.additions for f in diff_files)
        total_del = sum(f.deletions for f in diff_files)

        user_prompt = f"""Review these code changes ({len(diff_files)} files, +{total_add}/-{total_del} lines):

{diff_summary}"""

        # Add context files if provided
        if context_files:
            user_prompt += "\n\n--- Full file context for reference ---\n"
            for path, content in context_files.items():
                # Limit each file to 200 lines
                lines = content.splitlines()[:200]
                user_prompt += f"\n### {path}\n```\n" + "\n".join(lines) + "\n```\n"

        # Get provider
        result = self._provider_manager.get_provider_for_model(model_id)
        if not result:
            # Try first available provider
            providers = self._provider_manager.get_all_providers()
            if not providers:
                return ReviewResult(summary="No AI provider available for review.")
            provider = providers[0]
            model = ""
            # Get first model from provider — fail clearly if none available
            try:
                models = await provider.list_models()
                if models:
                    model = models[0]
                else:
                    return ReviewResult(
                        summary=f"No models available from {provider.display_name}. "
                        "Check your API key or provider configuration.",
                    )
            except Exception as e:
                return ReviewResult(
                    summary=f"Failed to list models from {provider.display_name}: {e}",
                )
        else:
            provider, model = result

        # Stream response and collect full text
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        full_response = ""
        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model,
                temperature=0.1,  # Low temperature for consistent reviews
                max_tokens=8000,
            ):
                if chunk.delta_content:
                    full_response += chunk.delta_content
        except Exception as e:
            logger.error("Review streaming failed: %s", e)
            return ReviewResult(summary=f"Review failed: {e}")

        # Parse the JSON response
        return self._parse_review_response(full_response, diff_files, model, provider.name)

    def _parse_review_response(
        self,
        response: str,
        diff_files: list[DiffFile],
        model: str,
        provider: str,
    ) -> ReviewResult:
        """Parse AI response into structured ReviewResult."""
        # Try to extract JSON from response
        response = response.strip()

        # Remove markdown fences if present
        if response.startswith("```"):
            response = re.sub(r"^```\w*\n?", "", response)
            response = re.sub(r"\n?```$", "", response)

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Try to find JSON in the response
            m = re.search(r"\{[\s\S]*\}", response)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    logger.warning("Could not parse review JSON from response")
                    return ReviewResult(
                        summary=response[:500],
                        files_reviewed=len(diff_files),
                        total_additions=sum(f.additions for f in diff_files),
                        total_deletions=sum(f.deletions for f in diff_files),
                        model=model,
                        provider=provider,
                    )
            else:
                return ReviewResult(
                    summary=response[:500],
                    files_reviewed=len(diff_files),
                    model=model,
                    provider=provider,
                )

        # Build findings
        findings: list[ReviewFinding] = []
        for f in data.get("findings", []):
            try:
                findings.append(ReviewFinding(
                    file=f.get("file", "unknown"),
                    line=int(f.get("line", 0)),
                    severity=Severity(f.get("severity", "info")),
                    category=Category(f.get("category", "other")),
                    title=f.get("title", ""),
                    body=f.get("body", ""),
                    suggestion=f.get("suggestion"),
                ))
            except (ValueError, KeyError) as e:
                logger.warning("Skipping malformed finding: %s", e)

        return ReviewResult(
            summary=data.get("summary", "Review complete."),
            findings=findings,
            files_reviewed=len(diff_files),
            total_additions=sum(f.additions for f in diff_files),
            total_deletions=sum(f.deletions for f in diff_files),
            model=model,
            provider=provider,
        )


async def get_git_diff(project_root: str, mode: str = "working") -> str:
    """Get git diff from a project directory.

    Args:
        project_root: Path to the git repository root.
        mode: One of "working" (unstaged), "staged", "branch" (vs main/master).
    """
    import asyncio

    if mode == "staged":
        cmd = ["git", "diff", "--cached"]
    elif mode == "branch":
        # Try main, then master
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--verify", "main",
                cwd=project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            base = "main" if proc.returncode == 0 else "master"
        except Exception:
            base = "main"
        cmd = ["git", "diff", f"{base}...HEAD"]
    else:
        cmd = ["git", "diff"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error("Failed to get git diff: %s", e)
        return ""
