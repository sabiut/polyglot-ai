"""Review engine — sends diff or IaC files to AI and parses structured findings."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .diff_parser import format_diff_for_review, parse_diff
from .models import (
    Category,
    DiffFile,
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

Reason step by step internally before writing the JSON:
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

Reason step by step internally before writing the JSON:
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
            return ReviewResult(summary=f"No {mode} files found to review.")

        system_prompt = _MODE_PROMPTS.get(mode, REVIEW_SYSTEM_PROMPT)

        # Build the user message with all files
        parts = [f"Review these {len(files)} {mode} file(s) for security issues:\n"]
        total_bytes = 0
        for path, content in files.items():
            # Truncate individual files
            if len(content) > _MAX_FILE_BYTES:
                content = content[:_MAX_FILE_BYTES] + "\n... [truncated]"
            parts.append(f"\n### {path}\n```\n{content}\n```\n")
            total_bytes += len(content)
            if total_bytes > _MAX_TOTAL_BYTES:
                parts.append(
                    f"\n... [review truncated at {_MAX_TOTAL_BYTES} bytes, "
                    f"{len(files) - len(parts) + 1} files not included]"
                )
                break

        user_prompt = "\n".join(parts)

        # Get provider
        result = self._provider_manager.get_provider_for_model(model_id)
        if not result:
            providers = self._provider_manager.get_all_providers()
            if not providers:
                return ReviewResult(summary="No AI provider available for review.")
            provider = providers[0]
            model = ""
            try:
                models = await provider.list_models()
                if models:
                    model = models[0]
                else:
                    return ReviewResult(
                        summary=f"No models available from {provider.display_name}."
                    )
            except Exception as e:
                return ReviewResult(
                    summary=f"Failed to list models from {provider.display_name}: {e}"
                )
        else:
            provider, model = result

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
        except Exception as e:
            logger.error("IaC review streaming failed: %s", e)
            return ReviewResult(summary=f"Review failed: {e}")

        # Parse the JSON response (reuse parser with empty diff_files list)
        result_obj = self._parse_review_response(full_response, [], model, provider.name)
        # Override files_reviewed to reflect actual file count
        result_obj.files_reviewed = len(files)
        return result_obj

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
    """Read a file, returning (relative_path, content) or None on error."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES * 2:
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(project_root))
        return rel, content
    except Exception:
        return None


def _walk_iac(project_root: Path, patterns: list[str]) -> list[Path]:
    """Glob multiple patterns while skipping vendored directories."""
    results: list[Path] = []
    for pattern in patterns:
        for p in project_root.glob(pattern):
            if not p.is_file():
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


def collect_k8s_manifests(project_root: str | Path) -> dict[str, str]:
    """Collect Kubernetes YAML manifests from k8s/, manifests/, deploy/ directories."""
    root = Path(project_root)
    if not root.is_dir():
        return {}

    candidate_dirs = ["k8s", "manifests", "deploy", "kubernetes"]
    files: list[Path] = []
    for dir_name in candidate_dirs:
        subdir = root / dir_name
        if subdir.is_dir():
            files.extend(_walk_iac(subdir, ["**/*.yaml", "**/*.yml"]))

    # Also include YAML files at the project root
    for yml in root.glob("*.yaml"):
        if yml.is_file():
            files.append(yml)
    for yml in root.glob("*.yml"):
        if yml.is_file():
            files.append(yml)

    result: dict[str, str] = {}
    for f in sorted(set(files)):
        entry = _read_file_safe(f, root)
        if not entry:
            continue
        # Only include files that look like K8s manifests
        if "apiVersion" in entry[1] and "kind" in entry[1]:
            result[entry[0]] = entry[1]
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
