"""Review engine — sends diff or IaC files to AI and parses structured findings."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from .diff_parser import format_diff_for_review, parse_diff
from .models import (
    Category,
    DiffFile,
    PRSummary,
    ReviewFinding,
    ReviewResult,
    Severity,
)

logger = logging.getLogger(__name__)

# Caps to prevent hitting AI token limits
_MAX_FILE_BYTES = 50_000
_MAX_TOTAL_BYTES = 500_000

REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. You will receive a unified diff of code changes.

Your task:
1. Review ONLY the changed code (lines starting with + or -).
2. Focus on real issues: bugs, security risks, logic errors, performance problems.
3. Do NOT nitpick style unless it harms readability.
4. Be specific — cite the exact file and line number.

Reason step by step **silently** before writing the JSON. Your visible
output must start with `{` — do not emit any chain-of-thought, preamble,
markdown fences, or explanation outside the JSON object.
- Walk the diff hunk by hunk.
- For each change, ask: could this break something, leak something, or regress behaviour?
- Consider edge cases, concurrency, error paths, and the blast radius of the change.
- Only report findings you'd stake your reputation on.

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


_IAC_JSON_INSTRUCTIONS = """
Return your review as JSON with this exact structure:
{
  "summary": "Brief overview of what was scanned and overall security posture",
  "findings": [
    {
      "file": "path/to/file.tf",
      "line": 42,
      "severity": "critical|high|medium|low|info",
      "category": "security|bug|performance|maintainability|style|other",
      "title": "Short issue title",
      "body": "Detailed explanation of the issue and its impact",
      "suggestion": "Optional: suggested fix as a code snippet"
    }
  ]
}

Severity guide:
- critical: Exposes secrets, public access to internal resources, destroys data
- high: Major security risk, likely to be exploited, compliance violations
- medium: Should fix before production, best-practice violations
- low: Nice to fix, minor hardening opportunities
- info: Observations and recommendations

Reason step by step **silently** before writing the JSON. Your visible
output must start with `{` — do not emit any chain-of-thought, preamble,
markdown fences, or explanation outside the JSON object.
- Scan each file resource by resource.
- For each resource, ask: what's the blast radius if this is misconfigured?
- Check against the issue list above systematically — do not skip categories.
- Chain related findings (e.g. public S3 + no encryption = data exfil risk).
- Only report issues you are confident about.

Return ONLY valid JSON, no markdown fences, no extra text.
If everything looks secure, return an empty findings array with a positive summary.
"""

TERRAFORM_REVIEW_PROMPT = (
    """You are a cloud security expert reviewing Terraform infrastructure code.

Look for these security issues:

CRITICAL / HIGH severity:
- Hardcoded secrets: passwords, API keys, access tokens, private keys in .tf files
- Public exposure: security groups allowing 0.0.0.0/0 on SSH (22), DB ports, or wildcard ports
- Public S3 buckets: acl = "public-read" or "public-read-write", missing block_public_access
- Unencrypted resources: RDS without storage_encrypted, S3 without server_side_encryption, EBS without encrypted=true
- Over-permissive IAM: Action = "*", Resource = "*", NotAction, iam:PassRole wildcards
- Hardcoded AWS account IDs or ARNs that should be variables
- Root user access keys, missing MFA on root
- Publicly accessible RDS instances (publicly_accessible = true)

MEDIUM severity:
- Missing backup retention, point-in-time recovery
- Missing logging: CloudTrail, VPC flow logs, S3 access logs, RDS audit logs
- Missing deletion protection on critical resources
- Outdated provider versions or deprecated resources
- Using default VPC instead of custom VPCs
- Missing tags for cost allocation and compliance

LOW / INFO severity:
- Missing lifecycle rules (prevent_destroy, ignore_changes)
- Hardcoded AMI IDs without data sources
- Non-descriptive resource names
- Missing comments on complex logic
"""
    + _IAC_JSON_INSTRUCTIONS
)


KUBERNETES_REVIEW_PROMPT = (
    """You are a Kubernetes security expert reviewing K8s manifests.

Look for these security issues:

CRITICAL / HIGH severity:
- Privileged containers: securityContext.privileged = true
- Running as root: missing runAsNonRoot or runAsUser: 0
- Host namespace access: hostNetwork, hostPID, hostIPC = true
- Hostpath volume mounts with sensitive paths (/, /etc, /var)
- Capabilities: NET_ADMIN, SYS_ADMIN, ALL not dropped
- Hardcoded secrets in env vars (use Secret resources instead)
- Missing NetworkPolicies (default allow-all)
- allowPrivilegeEscalation = true (or missing)
- Service type LoadBalancer exposed without proper restrictions

MEDIUM severity:
- Using :latest image tags (non-reproducible)
- Missing image pull policies
- Missing resource limits and requests (can cause OOM kills, starve nodes)
- Missing liveness/readiness probes
- Missing securityContext entirely
- Using default service account
- Missing RBAC (ClusterRole with wildcard verbs/resources)
- No pod anti-affinity for HA workloads

LOW / INFO severity:
- Missing labels for selector matching
- No podDisruptionBudget
- Missing topology spread constraints
- No horizontal pod autoscaler
"""
    + _IAC_JSON_INSTRUCTIONS
)


DOCKERFILE_REVIEW_PROMPT = (
    """You are a container security expert reviewing Dockerfiles.

Look for these security issues:

CRITICAL / HIGH severity:
- Hardcoded secrets: passwords, API keys, tokens in ENV or ARG
- Running as root (missing USER directive or USER root)
- Base image from untrusted registry
- :latest tags (non-reproducible builds)
- curl | bash / wget | sh patterns (untrusted script execution)
- COPY/ADD of sensitive files (.env, .git, credentials)
- chmod 777 or overly permissive permissions
- Adding all files with COPY . . instead of specific paths

MEDIUM severity:
- Not using multi-stage builds (bloated final image)
- Missing HEALTHCHECK directive
- Using apt-get without --no-install-recommends or missing cleanup
- Not pinning package versions (apt-get install pkg without =version)
- Missing .dockerignore (leaks sensitive files)
- Layers not ordered for cache efficiency
- ENTRYPOINT with shell form instead of exec form

LOW / INFO severity:
- Missing LABEL metadata (maintainer, version, description)
- Not using specific base image digests (FROM image@sha256:...)
- ENV vars that should be ARGs
- Dockerfile not at repository root
"""
    + _IAC_JSON_INSTRUCTIONS
)


HELM_REVIEW_PROMPT = (
    """You are a Kubernetes security expert reviewing Helm charts.

Look for these security issues:

CRITICAL / HIGH severity:
- Hardcoded secrets in values.yaml (passwords, tokens)
- Chart dependencies pulled from untrusted repositories
- Templates generating privileged pods, host network, or root containers
- Missing securityContext in default templates
- Default values enabling debug/dev modes
- Services of type LoadBalancer without source IP restrictions
- ClusterRole templates with wildcard permissions

MEDIUM severity:
- Missing PodSecurityPolicy / PodSecurityAdmission templates
- No resource limits in default values
- Missing NetworkPolicy templates
- No ServiceAccount defined (uses default)
- Missing readiness/liveness probes
- Using {{ .Values }} without defaults or validation
- No chart dependency pinning

LOW / INFO severity:
- Missing NOTES.txt
- No values.schema.json for validation
- Missing chart metadata (maintainers, description)
- Templates without _helpers.tpl abstractions
- Not using the `required` function for mandatory values
"""
    + _IAC_JSON_INSTRUCTIONS
)


_MODE_PROMPTS: dict[str, str] = {
    "terraform": TERRAFORM_REVIEW_PROMPT,
    "kubernetes": KUBERNETES_REVIEW_PROMPT,
    "dockerfile": DOCKERFILE_REVIEW_PROMPT,
    "helm": HELM_REVIEW_PROMPT,
}


PR_SUMMARY_SYSTEM_PROMPT = """You are a senior engineer writing a pull-request description.

You will receive a unified diff of code changes. Produce a PR title and
a structured description that a reviewer will actually read.

Reason silently before writing the JSON. Your visible output must start
with `{` — no preamble, no markdown fences, no chain-of-thought.

Return JSON with this exact shape:
{
  "title": "Short, imperative PR title (under 72 chars)",
  "summary": [
    "Bullet summarising the main change",
    "Second bullet for any other meaningful change",
    "…up to 5 bullets total"
  ],
  "test_plan": [
    "Manual step a reviewer can run to verify the change",
    "Or an automated test command like `pytest tests/foo`"
  ],
  "risks": [
    "Any migration, rollback, breaking-change, performance, or security",
    "concern worth flagging. Empty array if none."
  ]
}

Rules:
- Title: imperative voice ("add X", "fix Y", "refactor Z"), under 72 chars,
  no trailing period, no scope prefix unless the repo uses conventional commits
- Summary: 1-5 bullets focused on *why* and *what*, not a literal file list
- Test plan: 1-4 checkable items
- Risks: only real concerns, never padding — empty array if genuinely none
- All strings plain text, no markdown formatting inside values
- Return ONLY valid JSON
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


# ── IaC file collectors ─────────────────────────────────────────────

_SKIP_IAC_DIRS = {".terraform", ".git", "node_modules", ".venv", "venv", "__pycache__"}


def _read_file_safe(path: Path, project_root: Path) -> tuple[str, str] | None:
    """Read a file, returning (relative_path, content) or None on error.

    Errors are logged as warnings so skipped files are visible in diagnostics
    instead of being silently dropped from a security scan.
    """
    try:
        size = path.stat().st_size
    except OSError as e:
        logger.warning("IaC scan: cannot stat %s: %s", path, e)
        return None
    if size > _MAX_FILE_BYTES * 2:
        logger.info(
            "IaC scan: skipping %s (size %d > %d bytes)",
            path,
            size,
            _MAX_FILE_BYTES * 2,
        )
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("IaC scan: cannot read %s: %s", path, e)
        return None
    try:
        rel = str(path.relative_to(project_root))
    except ValueError:
        # Symlink escaped the project root — refuse to include.
        logger.warning(
            "IaC scan: refusing to include %s (outside project root %s)",
            path,
            project_root,
        )
        return None
    return rel, content


def _walk_iac(project_root: Path, patterns: list[str]) -> list[Path]:
    """Glob multiple patterns while skipping vendored directories.

    Each ``glob()`` is wrapped in a try/except so one unreadable directory
    (e.g. permission denied) can't crash the entire collection.
    """
    results: list[Path] = []
    for pattern in patterns:
        try:
            matches = list(project_root.glob(pattern))
        except OSError as e:
            logger.warning("IaC scan: glob '%s' failed under %s: %s", pattern, project_root, e)
            continue
        for p in matches:
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if any(part in _SKIP_IAC_DIRS for part in p.parts):
                continue
            results.append(p)
    return sorted(set(results))


def collect_terraform_files(project_root: str | Path) -> dict[str, str]:
    """Collect Terraform .tf and .tfvars files."""
    root = Path(project_root)
    if not root.is_dir():
        return {}
    files = _walk_iac(root, ["**/*.tf", "**/*.tfvars", "**/*.hcl"])
    result: dict[str, str] = {}
    for f in files:
        entry = _read_file_safe(f, root)
        if entry:
            result[entry[0]] = entry[1]
    return result


#: Substrings in apiVersion values that identify a YAML document as a
#: real Kubernetes manifest (rather than a GitHub Actions workflow, an
#: OpenAPI spec, a docker-compose file, etc. that merely contains the
#: word ``kind`` somewhere).
_K8S_API_GROUPS = (
    "",  # bare "v1"
    "apps/",
    "batch/",
    "networking.k8s.io/",
    "rbac.authorization.k8s.io/",
    "storage.k8s.io/",
    "policy/",
    "autoscaling/",
    "admissionregistration.k8s.io/",
    "apiextensions.k8s.io/",
    "certificates.k8s.io/",
    "coordination.k8s.io/",
    "discovery.k8s.io/",
    "events.k8s.io/",
    "node.k8s.io/",
    "scheduling.k8s.io/",
    "flowcontrol.apiserver.k8s.io/",
    "helm.sh/",
    "argoproj.io/",
    "cert-manager.io/",
    "monitoring.coreos.com/",
    "networking.istio.io/",
    "security.istio.io/",
    "gateway.networking.k8s.io/",
)


def _looks_like_k8s_doc(doc: object) -> bool:
    """True if a parsed YAML document looks like a Kubernetes resource."""
    if not isinstance(doc, dict):
        return False
    api_version = doc.get("apiVersion")
    kind = doc.get("kind")
    if not isinstance(api_version, str) or not isinstance(kind, str):
        return False
    if not kind.strip():
        return False
    # apiVersion is either "v1" (bare) or "<group>/<version>". Bare "v1"
    # is core K8s; "<group>/..." must start with a known K8s API group.
    if "/" not in api_version:
        return api_version == "v1"
    group_prefix = api_version.split("/", 1)[0] + "/"
    return any(api_version == g.rstrip("/") or group_prefix == g for g in _K8S_API_GROUPS)


def collect_k8s_manifests(project_root: str | Path) -> dict[str, str]:
    """Collect Kubernetes manifests from common manifest directories.

    Scans ``k8s/``, ``kubernetes/``, ``manifests/``, ``deploy/`` subdirectories
    and any root-level ``*.yaml``/``*.yml`` file. A file is included only if
    at least one YAML document inside parses as a mapping with an ``apiVersion``
    matching a known Kubernetes API group and a non-empty ``kind`` — this keeps
    GitHub Actions workflows, OpenAPI specs, pre-commit configs, etc. from
    being misclassified as K8s manifests.
    """
    root = Path(project_root)
    if not root.is_dir():
        return {}

    candidate_dirs = ["k8s", "manifests", "deploy", "kubernetes"]
    files: list[Path] = []
    for dir_name in candidate_dirs:
        subdir = root / dir_name
        if subdir.is_dir():
            files.extend(_walk_iac(subdir, ["**/*.yaml", "**/*.yml"]))

    # Also include YAML files at the project root (but still require the
    # manifest-shape check below, so CI files don't sneak in).
    for glob_pat in ("*.yaml", "*.yml"):
        try:
            for yml in root.glob(glob_pat):
                try:
                    if yml.is_file():
                        files.append(yml)
                except OSError:
                    continue
        except OSError as e:
            logger.warning("IaC scan: root glob '%s' failed: %s", glob_pat, e)

    try:
        import yaml  # PyYAML
    except ImportError:
        logger.warning(
            "IaC scan: PyYAML not installed — falling back to substring heuristic. "
            "Install pyyaml for more accurate Kubernetes manifest detection."
        )
        yaml = None  # type: ignore[assignment]

    result: dict[str, str] = {}
    for f in sorted(set(files)):
        entry = _read_file_safe(f, root)
        if not entry:
            continue
        rel, content = entry
        if yaml is not None:
            try:
                docs = list(yaml.safe_load_all(content))
            except yaml.YAMLError as e:
                logger.debug("IaC scan: YAML parse failed for %s: %s", rel, e)
                continue
            if any(_looks_like_k8s_doc(d) for d in docs):
                result[rel] = content
        else:
            # Fallback: loose substring check. Better than nothing.
            if "apiVersion:" in content and "kind:" in content:
                result[rel] = content
    return result


def collect_dockerfiles(project_root: str | Path) -> dict[str, str]:
    """Collect Dockerfile* files from the project."""
    root = Path(project_root)
    if not root.is_dir():
        return {}
    files = _walk_iac(root, ["**/Dockerfile", "**/Dockerfile.*", "**/*.dockerfile"])
    result: dict[str, str] = {}
    for f in files:
        entry = _read_file_safe(f, root)
        if entry:
            result[entry[0]] = entry[1]
    return result


def collect_helm_files(project_root: str | Path) -> dict[str, str]:
    """Collect Helm chart files (Chart.yaml, values.yaml, templates/)."""
    root = Path(project_root)
    if not root.is_dir():
        return {}
    files = _walk_iac(
        root,
        [
            "**/Chart.yaml",
            "**/values.yaml",
            "**/values-*.yaml",
            "**/templates/**/*.yaml",
            "**/templates/**/*.yml",
            "**/templates/**/*.tpl",
        ],
    )
    result: dict[str, str] = {}
    for f in files:
        entry = _read_file_safe(f, root)
        if entry:
            result[entry[0]] = entry[1]
    return result


def collect_iac_files(project_root: str | Path, mode: str) -> dict[str, str]:
    """Collect IaC files for a given mode."""
    collectors = {
        "terraform": collect_terraform_files,
        "kubernetes": collect_k8s_manifests,
        "dockerfile": collect_dockerfiles,
        "helm": collect_helm_files,
    }
    collector = collectors.get(mode)
    if not collector:
        return {}
    return collector(project_root)
