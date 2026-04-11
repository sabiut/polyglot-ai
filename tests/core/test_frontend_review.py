"""Tests for the frontend_design review mode wiring.

These pin the new mode end-to-end at the engine boundary:

- The mode is registered in _MODE_PROMPTS so review_content can dispatch it
- collect_iac_files routes ``frontend_design`` to collect_frontend_files
- The collector matches React/Vue/Svelte/HTML/CSS/Tailwind config
- node_modules / .next / dist / storybook stories are filtered out
- The 80-file cap holds
- Empty / non-existent project root returns an empty dict
"""

from __future__ import annotations

from pathlib import Path

from polyglot_ai.core.review.review_engine import (
    FRONTEND_DESIGN_REVIEW_PROMPT,
    _MODE_PROMPTS,
    collect_frontend_files,
    collect_iac_files,
)


# ── Mode registration ───────────────────────────────────────────────


def test_frontend_design_in_mode_prompts():
    assert "frontend_design" in _MODE_PROMPTS
    assert _MODE_PROMPTS["frontend_design"] is FRONTEND_DESIGN_REVIEW_PROMPT


def test_frontend_prompt_contains_design_audit_directives():
    """Sanity check that the prompt asks for the right things — if
    these strings drift, somebody rewrote the audit and should
    update this test deliberately."""
    p = FRONTEND_DESIGN_REVIEW_PROMPT
    # Core audit pillars
    assert "WCAG 2.1 AA" in p
    assert "contrast" in p.lower()
    assert "hierarchy" in p.lower()
    assert "design tokens" in p.lower()
    # Anti-generic-AI smell
    assert "generic-ai smell" in p.lower() or "generic-ai" in p.lower()
    # Severity bands present
    for band in ("CRITICAL severity", "HIGH severity", "MEDIUM severity"):
        assert band in p


def test_collect_iac_files_routes_frontend_design(tmp_path):
    (tmp_path / "App.tsx").write_text("export default () => <div/>;\n")
    out = collect_iac_files(tmp_path, "frontend_design")
    assert "App.tsx" in out


# ── Collector behaviour ─────────────────────────────────────────────


def _make_project(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def test_collector_matches_react_vue_svelte_html_css(tmp_path):
    _make_project(
        tmp_path,
        {
            "src/App.tsx": "x",
            "src/Button.jsx": "x",
            "src/Hero.vue": "x",
            "src/Card.svelte": "x",
            "src/about.astro": "x",
            "public/index.html": "x",
            "src/styles.css": "x",
            "src/theme.scss": "x",
            "src/legacy.less": "x",
        },
    )
    out = collect_frontend_files(tmp_path)
    rel_paths = set(out.keys())
    for expected in (
        "src/App.tsx",
        "src/Button.jsx",
        "src/Hero.vue",
        "src/Card.svelte",
        "src/about.astro",
        "public/index.html",
        "src/styles.css",
        "src/theme.scss",
        "src/legacy.less",
    ):
        assert expected in rel_paths, f"missing {expected}"


def test_collector_picks_up_design_token_files(tmp_path):
    _make_project(
        tmp_path,
        {
            "tailwind.config.js": "module.exports = {}",
            "src/theme.ts": "export const t = {}",
            "design-tokens.json": "{}",
            "src/globals.css": "body{}",
        },
    )
    out = collect_frontend_files(tmp_path)
    rel_paths = set(out.keys())
    assert "tailwind.config.js" in rel_paths
    assert "src/theme.ts" in rel_paths
    assert "design-tokens.json" in rel_paths
    assert "src/globals.css" in rel_paths


def test_collector_skips_node_modules(tmp_path):
    _make_project(
        tmp_path,
        {
            "src/App.tsx": "real",
            "node_modules/react/index.js": "vendored",
            "node_modules/some-pkg/Button.tsx": "vendored",
        },
    )
    out = collect_frontend_files(tmp_path)
    assert "src/App.tsx" in out
    assert not any("node_modules" in p for p in out)


def test_collector_skips_build_output_dirs(tmp_path):
    _make_project(
        tmp_path,
        {
            "src/App.tsx": "real",
            "dist/App.tsx": "build artifact",
            "build/index.html": "build artifact",
            ".next/server/page.js": "framework cache",  # filtered by extension
            "out/static.html": "static export",
            "storybook-static/iframe.html": "storybook build",
        },
    )
    out = collect_frontend_files(tmp_path)
    assert "src/App.tsx" in out
    for bad in (
        "dist/App.tsx",
        "build/index.html",
        "out/static.html",
        "storybook-static/iframe.html",
    ):
        assert bad not in out


def test_collector_skips_storybook_stories(tmp_path):
    _make_project(
        tmp_path,
        {
            "src/Button.tsx": "real",
            "src/Button.stories.tsx": "story",
            "src/Modal.stories.jsx": "story",
            "src/Header.stories.ts": "story",
            "src/Footer.stories.js": "story",
        },
    )
    out = collect_frontend_files(tmp_path)
    assert "src/Button.tsx" in out
    for story in (
        "src/Button.stories.tsx",
        "src/Modal.stories.jsx",
        "src/Header.stories.ts",
        "src/Footer.stories.js",
    ):
        assert story not in out


def test_collector_caps_at_80_files(tmp_path):
    files = {f"src/Component{i:03d}.tsx": "x" for i in range(150)}
    _make_project(tmp_path, files)
    out = collect_frontend_files(tmp_path)
    assert len(out) == 80


def test_collector_returns_empty_for_missing_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert collect_frontend_files(missing) == {}


def test_collector_returns_empty_when_no_frontend_files(tmp_path):
    _make_project(
        tmp_path,
        {
            "main.py": "print('hi')",
            "Dockerfile": "FROM python",
            "README.md": "# project",
        },
    )
    assert collect_frontend_files(tmp_path) == {}
