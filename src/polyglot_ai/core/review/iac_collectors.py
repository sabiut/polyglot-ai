"""Filesystem collectors for the per-mode IaC review.

Extracted from ``review_engine.py``. Each ``collect_*_files`` function
walks the project root, filters by glob, skips vendored directories,
and returns ``{relative_path: file_contents}``. The engine then joins
these into a single prompt for the AI.

The collectors are deliberately defensive:
* Vendored dirs (``.terraform``, ``node_modules``, ``.venv``, …) are
  skipped globally via :data:`_SKIP_IAC_DIRS`.
* Files larger than twice :data:`MAX_FILE_BYTES` are skipped entirely.
* Permission / I/O errors are logged as warnings — one unreadable file
  never aborts the whole scan.
* Symlinks escaping the project root are refused via
  ``path.relative_to(project_root)``.

The K8s collector further gates on a YAML-parsed ``apiVersion`` +
``kind`` check so CI workflow files (which live alongside manifests
in many repos) aren't misclassified as Kubernetes resources.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Caps to keep the AI prompt under provider limits. _MAX_TOTAL_BYTES
# bounds the concatenated payload; MAX_FILE_BYTES bounds any single
# file (truncated when the engine renders the prompt). Kept public
# so the engine can reuse the same numbers.
MAX_FILE_BYTES = 50_000
MAX_TOTAL_BYTES = 500_000

# Directory names the IaC walkers refuse to descend into.
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
    if size > MAX_FILE_BYTES * 2:
        logger.info(
            "IaC scan: skipping %s (size %d > %d bytes)",
            path,
            size,
            MAX_FILE_BYTES * 2,
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


def collect_docker_compose_files(project_root: str | Path) -> dict[str, str]:
    """Collect Docker Compose files from the project.

    Matches the canonical compose filenames: ``docker-compose.yml``,
    ``docker-compose.yaml``, ``compose.yml``, ``compose.yaml``, and
    variant overrides like ``docker-compose.override.yml`` or
    ``docker-compose.prod.yaml``.
    """
    root = Path(project_root)
    if not root.is_dir():
        return {}
    files = _walk_iac(
        root,
        [
            "**/docker-compose.yml",
            "**/docker-compose.yaml",
            "**/docker-compose.*.yml",
            "**/docker-compose.*.yaml",
            "**/compose.yml",
            "**/compose.yaml",
            "**/compose.*.yml",
            "**/compose.*.yaml",
        ],
    )
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


def collect_frontend_files(project_root: str | Path) -> dict[str, str]:
    """Collect frontend UI source files for design audit.

    Casts a deliberately wide net so the audit sees the whole story —
    components AND styles AND design tokens AND framework config.
    The model needs all three to give grounded feedback (e.g. "this
    component hardcodes #f3f4f6 instead of using your tailwind gray-100
    token").

    Skips ``node_modules``, build outputs, and Storybook stories. Caps
    the result at 80 files after filtering to keep prompts under
    provider limits — larger projects should narrow the audit to a
    subdirectory by opening it as the project root.
    """
    root = Path(project_root)
    if not root.is_dir():
        return {}
    files = _walk_iac(
        root,
        [
            # Components
            "**/*.tsx",
            "**/*.jsx",
            "**/*.vue",
            "**/*.svelte",
            "**/*.astro",
            # Templates / pages
            "**/*.html",
            "**/*.htm",
            # Styles
            "**/*.css",
            "**/*.scss",
            "**/*.sass",
            "**/*.less",
            "**/*.styl",
            # Design tokens / theme config
            "**/tailwind.config.*",
            "**/postcss.config.*",
            "**/theme.ts",
            "**/theme.js",
            "**/tokens.json",
            "**/tokens.js",
            "**/tokens.ts",
            "**/design-tokens.*",
            "**/styles.*",
            "**/globals.css",
        ],
    )

    # Drop noisy build/output dirs and storybook stories that aren't
    # production UI. _walk_iac already skips node_modules / .git etc.
    DROP_PATH_PARTS = {"dist", "build", ".next", ".nuxt", ".svelte-kit", "out", "storybook-static"}
    DROP_SUFFIXES = (".stories.tsx", ".stories.jsx", ".stories.ts", ".stories.js")

    filtered: list[Path] = []
    for f in files:
        if any(part in DROP_PATH_PARTS for part in f.parts):
            continue
        if f.name.endswith(DROP_SUFFIXES):
            continue
        filtered.append(f)

    # Cap to keep prompts manageable. ~80 files * ~2KB = 160KB which
    # fits comfortably even after the system prompt overhead.
    MAX_FILES = 80
    filtered = filtered[:MAX_FILES]

    result: dict[str, str] = {}
    for f in filtered:
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
        "docker_compose": collect_docker_compose_files,
        "helm": collect_helm_files,
        "frontend_design": collect_frontend_files,
    }
    collector = collectors.get(mode)
    if not collector:
        return {}
    return collector(project_root)
