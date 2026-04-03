"""Centralized color token system for Polyglot AI theming.

All UI colors, spacing, border-radius, and font declarations live here.
Panels call ``tc.get("token")`` to resolve the current theme's value.
"""

from __future__ import annotations

# ── Dark theme ────────────────────────────────────────────────────

_DARK = {
    # Backgrounds
    "bg_base":              "#1e1e1e",
    "bg_surface":           "#252526",
    "bg_surface_raised":    "#2d2d2d",
    "bg_surface_overlay":   "#2d2d30",
    "bg_input":             "#3c3c3c",
    "bg_input_deep":        "#161616",
    "bg_hover":             "#3c3c3c",
    "bg_hover_subtle":      "#2a2d2e",
    "bg_active":            "#094771",
    "bg_code_block":        "#1e1e1e",
    "bg_code_header":       "#2f2f2f",
    "bg_terminal":          "#0e0e0e",
    "bg_activity_bar":      "#181818",
    "bg_chat_input":        "#323233",
    "bg_user_bubble":       "#303030",
    "bg_user_bubble_long":  "#2a2a2c",
    "bg_card":              "#2a2a2a",
    "bg_inline_code":       "#2f2f2f",
    "bg_feedback_pos":      "#1a3a2a",
    "bg_feedback_neg":      "#3a1a1a",
    "bg_diff_add":          "#1a3a1a",
    "bg_diff_del":          "#3a1a1a",

    # Text
    "text_primary":         "#d4d4d4",
    "text_secondary":       "#969696",
    "text_tertiary":        "#888888",
    "text_disabled":        "#6c6c6c",
    "text_heading":         "#e0e0e0",
    "text_on_accent":       "#ffffff",
    "text_muted":           "#666666",
    "text_link":            "#7cacf8",
    "text_user_msg":        "#e8e8e8",
    "text_ai_msg":          "#d1d5db",
    "text_inline_code":     "#f97583",

    # Borders
    "border_primary":       "#3c3c3c",
    "border_secondary":     "#333333",
    "border_input":         "#505050",
    "border_subtle":        "#2b2b2b",
    "border_card":          "#3a3a3a",
    "border_code":          "#374151",
    "border_menu":          "#454545",
    "border_focus":         "#0078d4",

    # Accents
    "accent_primary":       "#0078d4",
    "accent_primary_hover": "#1a8ae8",
    "accent_primary_pressed": "#005fa3",
    "accent_success":       "#10a37f",
    "accent_success_hover": "#1bbd96",
    "accent_success_muted": "#4ec9b0",
    "accent_warning":       "#e5a00d",
    "accent_error":         "#f44747",
    "accent_error_hover":   "#e53935",
    "accent_danger":        "#d32f2f",
    "accent_danger_hover":  "#e53935",
    "accent_info":          "#569cd6",
    "accent_claude":        "#d97706",
    "accent_claude_hover":  "#e69500",

    # Activity bar
    "activity_indicator":   "#10a37f",
    "activity_icon":        "#858585",
    "activity_icon_hover":  "#cccccc",
    "activity_icon_active": "#e8e8e8",

    # Status bar
    "status_bar_bg":        "#1a1a2e",
    "status_bar_fg":        "#8888cc",

    # Scrollbar
    "scrollbar_track":      "#1e1e1e",
    "scrollbar_thumb":      "#424242",
    "scrollbar_thumb_hover": "#686868",

    # Syntax highlighting (code blocks in chat)
    "syn_keyword":          "#569cd6",
    "syn_string":           "#ce9178",
    "syn_comment":          "#6a9955",
    "syn_decorator":        "#dcdcaa",
    "syn_number":           "#b5cea8",
    "syn_identifier":       "#9cdcfe",
    "syn_builtin":          "#4ec9b0",

    # Git status
    "git_added":            "#73c991",
    "git_modified":         "#e2c08d",
    "git_deleted":          "#c74e39",
    "git_untracked":        "#888888",

    # Severity
    "severity_critical":    "#f44747",
    "severity_high":        "#e5a00d",
    "severity_medium":      "#569cd6",
    "severity_low":         "#4ec9b0",
    "severity_info":        "#888888",

    # Plan status
    "plan_pending":         "#666666",
    "plan_approved":        "#569cd6",
    "plan_in_progress":     "#e5a00d",
    "plan_completed":       "#4ec9b0",
    "plan_failed":          "#f44747",
    "plan_skipped":         "#555555",

    # Diff
    "diff_add_fg":          "#4ec9b0",
    "diff_del_fg":          "#e57373",
    "diff_hunk_fg":         "#569cd6",
    "diff_meta_fg":         "#888888",

    # Changeset status
    "cs_pending":           "#e5a00d",
    "cs_applied":           "#4ec9b0",
    "cs_rejected":          "#e57373",
    "cs_rolledback":        "#888888",
}

# ── Light theme ───────────────────────────────────────────────────

_LIGHT = {
    # Backgrounds
    "bg_base":              "#ffffff",
    "bg_surface":           "#f3f3f3",
    "bg_surface_raised":    "#ececec",
    "bg_surface_overlay":   "#e8e8e8",
    "bg_input":             "#ffffff",
    "bg_input_deep":        "#f8f8f8",
    "bg_hover":             "#e0e0e0",
    "bg_hover_subtle":      "#e8e8e8",
    "bg_active":            "#c8e1ff",
    "bg_code_block":        "#f5f5f5",
    "bg_code_header":       "#e8e8e8",
    "bg_terminal":          "#fafafa",
    "bg_activity_bar":      "#2c2c2c",
    "bg_chat_input":        "#f0f0f0",
    "bg_user_bubble":       "#e3f2fd",
    "bg_user_bubble_long":  "#e8edf2",
    "bg_card":              "#f5f5f5",
    "bg_inline_code":       "#eaeaea",
    "bg_feedback_pos":      "#e8f5e9",
    "bg_feedback_neg":      "#fce4ec",
    "bg_diff_add":          "#e6ffe6",
    "bg_diff_del":          "#ffe6e6",

    # Text
    "text_primary":         "#333333",
    "text_secondary":       "#666666",
    "text_tertiary":        "#888888",
    "text_disabled":        "#a0a0a0",
    "text_heading":         "#222222",
    "text_on_accent":       "#ffffff",
    "text_muted":           "#999999",
    "text_link":            "#1a73e8",
    "text_user_msg":        "#1a1a1a",
    "text_ai_msg":          "#374151",
    "text_inline_code":     "#d63384",

    # Borders
    "border_primary":       "#d4d4d4",
    "border_secondary":     "#e0e0e0",
    "border_input":         "#c8c8c8",
    "border_subtle":        "#e8e8e8",
    "border_card":          "#d0d0d0",
    "border_code":          "#d1d5db",
    "border_menu":          "#c8c8c8",
    "border_focus":         "#0078d4",

    # Accents
    "accent_primary":       "#0078d4",
    "accent_primary_hover": "#0088e0",
    "accent_primary_pressed": "#005fa3",
    "accent_success":       "#10a37f",
    "accent_success_hover": "#0d8a6a",
    "accent_success_muted": "#2e7d6e",
    "accent_warning":       "#e5a00d",
    "accent_error":         "#d32f2f",
    "accent_error_hover":   "#c62828",
    "accent_danger":        "#d32f2f",
    "accent_danger_hover":  "#c62828",
    "accent_info":          "#1976d2",
    "accent_claude":        "#d97706",
    "accent_claude_hover":  "#b86205",

    # Activity bar (stays dark even in light theme for contrast)
    "activity_indicator":   "#10a37f",
    "activity_icon":        "#858585",
    "activity_icon_hover":  "#cccccc",
    "activity_icon_active": "#e8e8e8",

    # Status bar
    "status_bar_bg":        "#f0f0f0",
    "status_bar_fg":        "#555555",

    # Scrollbar
    "scrollbar_track":      "#ffffff",
    "scrollbar_thumb":      "#c8c8c8",
    "scrollbar_thumb_hover": "#a0a0a0",

    # Syntax highlighting
    "syn_keyword":          "#0000ff",
    "syn_string":           "#a31515",
    "syn_comment":          "#008000",
    "syn_decorator":        "#795e26",
    "syn_number":           "#098658",
    "syn_identifier":       "#001080",
    "syn_builtin":          "#267f99",

    # Git status
    "git_added":            "#22863a",
    "git_modified":         "#b08800",
    "git_deleted":          "#cb2431",
    "git_untracked":        "#888888",

    # Severity
    "severity_critical":    "#d32f2f",
    "severity_high":        "#e65100",
    "severity_medium":      "#1565c0",
    "severity_low":         "#2e7d32",
    "severity_info":        "#888888",

    # Plan status
    "plan_pending":         "#999999",
    "plan_approved":        "#1565c0",
    "plan_in_progress":     "#e65100",
    "plan_completed":       "#2e7d32",
    "plan_failed":          "#d32f2f",
    "plan_skipped":         "#bbbbbb",

    # Diff
    "diff_add_fg":          "#22863a",
    "diff_del_fg":          "#cb2431",
    "diff_hunk_fg":         "#1565c0",
    "diff_meta_fg":         "#888888",

    # Changeset status
    "cs_pending":           "#b08800",
    "cs_applied":           "#22863a",
    "cs_rejected":          "#cb2431",
    "cs_rolledback":        "#888888",
}

# ── Runtime state ─────────────────────────────────────────────────

_THEMES = {"dark": _DARK, "light": _LIGHT}
_current_theme = "dark"


def set_theme(name: str) -> None:
    """Switch the active theme. Call before re-applying styles."""
    global _current_theme
    if name in _THEMES:
        _current_theme = name


def current_theme() -> str:
    return _current_theme


def get(token: str) -> str:
    """Return the hex value for *token* in the current theme."""
    return _THEMES[_current_theme][token]


def get_for(theme: str, token: str) -> str:
    """Return the hex value for *token* in a specific theme."""
    return _THEMES[theme][token]


# ── Spacing (px) ──────────────────────────────────────────────────

SPACING_XS = 2
SPACING_SM = 4
SPACING_MD = 8
SPACING_LG = 12
SPACING_XL = 16
SPACING_2XL = 24
SPACING_3XL = 40

# ── Border radius (px) ───────────────────────────────────────────

RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 12

# ── Layout constants ─────────────────────────────────────────────

HEADER_HEIGHT = 36
DIALOG_HEADER_HEIGHT = 44

# ── Typography ────────────────────────────────────────────────────

FONT_UI = '"IBM Plex Sans", "Inter", "Noto Sans", sans-serif'
FONT_CODE = '"JetBrains Mono", "Fira Code", "Consolas", monospace'

FONT_XS = 10
FONT_SM = 11
FONT_MD = 12
FONT_BASE = 13
FONT_LG = 14
FONT_XL = 16
FONT_2XL = 20
FONT_3XL = 22
