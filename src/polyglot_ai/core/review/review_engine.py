"""Review engine — sends diff or IaC files to AI and parses structured findings.

The engine itself is intentionally thin: it orchestrates the AI call
and parses the response. Two concerns it used to own in-line have
been extracted into sibling modules so this file can focus on the
orchestration:

* :mod:`polyglot_ai.core.review.prompts` — all the system prompts
  (generic code review, per-IaC security scans, PR summary).
* :mod:`polyglot_ai.core.review.iac_collectors` — the file-system
  walkers that build the ``{path: content}`` dicts the engine feeds
  to the AI.

The original public surface of ``review_engine`` is preserved via
re-exports below so external callers (tests, UI panels) don't need
to update their imports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from .diff_parser import format_diff_for_review, parse_diff
from .iac_collectors import (
    MAX_FILE_BYTES as _MAX_FILE_BYTES,
)
from .iac_collectors import (
    MAX_TOTAL_BYTES as _MAX_TOTAL_BYTES,
)
from .iac_collectors import (
    collect_docker_compose_files,
    collect_dockerfiles,
    collect_frontend_files,
    collect_helm_files,
    collect_iac_files,
    collect_k8s_manifests,
    collect_terraform_files,
)
from .models import (
    Category,
    DiffFile,
    PRSummary,
    ReviewFinding,
    ReviewResult,
    Severity,
)
from .prompts import (
    DOCKER_COMPOSE_REVIEW_PROMPT,
    DOCKERFILE_REVIEW_PROMPT,
    FRONTEND_DESIGN_REVIEW_PROMPT,
    HELM_REVIEW_PROMPT,
    KUBERNETES_REVIEW_PROMPT,
    PR_SUMMARY_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    TERRAFORM_REVIEW_PROMPT,
)
from .prompts import (
    MODE_PROMPTS as _MODE_PROMPTS,
)

logger = logging.getLogger(__name__)

# Public re-exports — kept so existing callers like
# ``from polyglot_ai.core.review.review_engine import collect_iac_files``
# and ``from ... import FRONTEND_DESIGN_REVIEW_PROMPT, _MODE_PROMPTS``
# (used by tests/core/test_frontend_review.py) continue to work.
__all__ = [
    # Prompts
    "REVIEW_SYSTEM_PROMPT",
    "TERRAFORM_REVIEW_PROMPT",
    "KUBERNETES_REVIEW_PROMPT",
    "DOCKERFILE_REVIEW_PROMPT",
    "DOCKER_COMPOSE_REVIEW_PROMPT",
    "HELM_REVIEW_PROMPT",
    "FRONTEND_DESIGN_REVIEW_PROMPT",
    "PR_SUMMARY_SYSTEM_PROMPT",
    "_MODE_PROMPTS",
    # Collectors
    "collect_terraform_files",
    "collect_k8s_manifests",
    "collect_dockerfiles",
    "collect_docker_compose_files",
    "collect_helm_files",
    "collect_frontend_files",
    "collect_iac_files",
    # Engine
    "ReviewEngine",
    "get_git_diff",
]


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
            model_id: Provider-qualified model ID (e.g. "openai:gpt-5.5").
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
        resolved = await self._resolve_provider(model_id)
        if isinstance(resolved, ReviewResult):
            return resolved
        provider, model = resolved

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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Review streaming failed")
            return ReviewResult(
                summary="Review failed while streaming AI response.",
                status="failed",
                error=str(e),
                model=model,
                provider=provider.name,
            )

        # Parse the JSON response
        return self._parse_review_response(full_response, diff_files, model, provider.name)

    async def review_content(
        self,
        files: dict[str, str],
        mode: str,
        model_id: str = "",
    ) -> ReviewResult:
        """Review a set of files against a mode-specific security prompt.

        Args:
            files: Dict of {relative_path: file_content}
            mode: One of "terraform", "kubernetes", "dockerfile", "helm"
            model_id: Provider-qualified model ID
        """
        if not files:
            return ReviewResult(
                summary=f"No {mode} files found to review.",
                status="empty",
            )

        system_prompt = _MODE_PROMPTS.get(mode, REVIEW_SYSTEM_PROMPT)

        # Build the user message with all files, tracking truncation so we
        # can surface it to the user instead of hiding it inside the prompt.
        parts = [f"Review these {len(files)} {mode} file(s) for security issues:\n"]
        truncated_files: list[str] = []
        skipped_files: list[str] = []
        total_bytes = 0
        file_items = list(files.items())
        for idx, (path, content) in enumerate(file_items):
            if len(content) > _MAX_FILE_BYTES:
                content = content[:_MAX_FILE_BYTES] + "\n... [truncated]"
                truncated_files.append(path)
            if total_bytes + len(content) > _MAX_TOTAL_BYTES:
                remaining = [p for p, _ in file_items[idx:]]
                skipped_files.extend(remaining)
                parts.append(
                    f"\n... [review truncated at {_MAX_TOTAL_BYTES} bytes, "
                    f"{len(remaining)} file(s) not included: "
                    f"{', '.join(remaining[:5])}"
                    f"{' and more' if len(remaining) > 5 else ''}]"
                )
                break
            parts.append(f"\n### {path}\n```\n{content}\n```\n")
            total_bytes += len(content)

        user_prompt = "\n".join(parts)

        # Get provider
        resolved = await self._resolve_provider(model_id)
        if isinstance(resolved, ReviewResult):
            return resolved
        provider, model = resolved

        # Stream response
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        full_response = ""
        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model,
                temperature=0.1,
                max_tokens=8000,
            ):
                if chunk.delta_content:
                    full_response += chunk.delta_content
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("IaC review streaming failed (mode=%s)", mode)
            return ReviewResult(
                summary=f"Review failed while streaming {mode} security scan.",
                status="failed",
                error=str(e),
                files_reviewed=len(files),
                model=model,
                provider=provider.name,
                truncated_files=truncated_files + skipped_files,
            )

        # Parse the JSON response (reuse parser with empty diff_files list)
        result_obj = self._parse_review_response(full_response, [], model, provider.name)
        # Override files_reviewed to reflect actual file count
        result_obj.files_reviewed = len(files)
        result_obj.truncated_files = truncated_files + skipped_files
        if truncated_files or skipped_files:
            suffix = []
            if truncated_files:
                suffix.append(f"{len(truncated_files)} file(s) truncated")
            if skipped_files:
                suffix.append(f"{len(skipped_files)} file(s) skipped (size cap)")
            result_obj.summary = f"{result_obj.summary} [{'; '.join(suffix)}]"
        return result_obj

    async def _resolve_provider(self, model_id: str):
        """Resolve a provider and model for a review.

        Returns a ``(provider, model)`` tuple on success, or a ``ReviewResult``
        with ``status='failed'`` that the caller should return directly.
        Emits a warning when it falls back because the requested model_id
        did not match any configured provider.
        """
        result = self._provider_manager.get_provider_for_model(model_id)
        if result:
            return result

        if model_id:
            logger.warning(
                "Review: requested model_id '%s' not found — falling back to first "
                "available provider. Review quality may differ from user expectation.",
                model_id,
            )

        providers = self._provider_manager.get_all_providers()
        if not providers:
            return ReviewResult(
                summary="No AI provider configured for review.",
                status="failed",
                error="No providers available. Add an API key in Settings.",
            )
        provider = providers[0]
        try:
            models = await provider.list_models()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Review: failed to list models from %s", provider.display_name)
            return ReviewResult(
                summary=f"Could not list models from {provider.display_name}.",
                status="failed",
                error=str(e),
                provider=provider.name,
            )
        if not models:
            return ReviewResult(
                summary=f"No models available from {provider.display_name}.",
                status="failed",
                error="Provider returned an empty model list. Check API key / configuration.",
                provider=provider.name,
            )
        logger.info(
            "Review: falling back to provider=%s model=%s",
            provider.display_name,
            models[0],
        )
        return provider, models[0]

    async def generate_pr_summary(
        self,
        diff_text: str,
        model_id: str = "",
    ) -> PRSummary:
        """Generate a structured PR title, summary, test plan, and risks.

        Takes a unified diff, calls the AI provider, and returns a
        :class:`PRSummary` dataclass. Failures are surfaced with
        ``status='failed'`` so the UI can render an error state.
        """
        from .models import PRSummary

        diff_files = parse_diff(diff_text)
        if not diff_files:
            return PRSummary(
                title="",
                status="empty",
                error="No changes to summarise.",
            )

        diff_summary = format_diff_for_review(diff_files)
        total_add = sum(f.additions for f in diff_files)
        total_del = sum(f.deletions for f in diff_files)

        user_prompt = (
            f"Write a PR description for these changes "
            f"({len(diff_files)} files, +{total_add}/-{total_del} lines):\n\n"
            f"{diff_summary}"
        )

        resolved = await self._resolve_provider(model_id)
        if isinstance(resolved, ReviewResult):
            # Reuse the provider-resolution error result
            return PRSummary(
                title="",
                status="failed",
                error=resolved.error or resolved.summary,
                model=resolved.model,
                provider=resolved.provider,
            )
        provider, model = resolved

        messages = [
            {"role": "system", "content": PR_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        full_response = ""
        try:
            async for chunk in provider.stream_chat(
                messages=messages,
                model=model,
                temperature=0.2,
                max_tokens=2000,
            ):
                if chunk.delta_content:
                    full_response += chunk.delta_content
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("PR summary streaming failed")
            return PRSummary(
                title="",
                status="failed",
                error=str(e),
                model=model,
                provider=provider.name,
                files_changed=len(diff_files),
                additions=total_add,
                deletions=total_del,
            )

        return self._parse_pr_summary_response(
            full_response,
            files_changed=len(diff_files),
            additions=total_add,
            deletions=total_del,
            model=model,
            provider=provider.name,
        )

    def _parse_pr_summary_response(
        self,
        response: str,
        files_changed: int,
        additions: int,
        deletions: int,
        model: str,
        provider: str,
    ) -> PRSummary:
        """Parse AI response into a PRSummary, handling fence/JSON edge cases."""
        from .models import PRSummary

        response = response.strip()

        # Strip markdown fences if the model ignored the instructions
        if response.startswith("```"):
            response = re.sub(r"^```\w*\n?", "", response)
            response = re.sub(r"\n?```$", "", response)

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Try to find the first {...} block
            m = re.search(r"\{[\s\S]*\}", response)
            if not m:
                logger.warning("PR summary: no JSON in response: %r", response[:200])
                return PRSummary(
                    title="",
                    status="failed",
                    error="AI response was not valid JSON.",
                    files_changed=files_changed,
                    additions=additions,
                    deletions=deletions,
                    model=model,
                    provider=provider,
                )
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                logger.warning("PR summary: JSON parse failed: %s", e)
                return PRSummary(
                    title="",
                    status="failed",
                    error=f"Could not parse AI JSON: {e}",
                    files_changed=files_changed,
                    additions=additions,
                    deletions=deletions,
                    model=model,
                    provider=provider,
                )

        def _list_of_str(value) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(x).strip() for x in value if str(x).strip()]

        return PRSummary(
            title=str(data.get("title", "")).strip(),
            summary=_list_of_str(data.get("summary")),
            test_plan=_list_of_str(data.get("test_plan")),
            risks=_list_of_str(data.get("risks")),
            files_changed=files_changed,
            additions=additions,
            deletions=deletions,
            model=model,
            provider=provider,
        )

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
                findings.append(
                    ReviewFinding(
                        file=f.get("file", "unknown"),
                        line=int(f.get("line", 0)),
                        severity=Severity(f.get("severity", "info")),
                        category=Category(f.get("category", "other")),
                        title=f.get("title", ""),
                        body=f.get("body", ""),
                        suggestion=f.get("suggestion"),
                    )
                )
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
                "git",
                "rev-parse",
                "--verify",
                "main",
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
