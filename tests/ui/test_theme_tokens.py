"""Tests for the theme token system — palette parity and helpers."""

import re

from polyglot_ai.ui import theme, theme_colors as tc

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_dark_and_light_palettes_define_identical_tokens():
    # A token present in only one palette silently KeyErrors (or renders
    # wrong) the moment the other theme is active.
    dark_keys = set(tc._DARK)
    light_keys = set(tc._LIGHT)
    assert dark_keys == light_keys, (
        f"only in dark: {sorted(dark_keys - light_keys)}, "
        f"only in light: {sorted(light_keys - dark_keys)}"
    )


def test_all_token_values_are_six_digit_hex():
    for theme_name, palette in (("dark", tc._DARK), ("light", tc._LIGHT)):
        bad = {k: v for k, v in palette.items() if not HEX_RE.match(v)}
        assert not bad, f"non-hex values in {theme_name}: {bad}"


def test_get_respects_current_theme():
    original = tc.current_theme()
    try:
        tc.set_theme("dark")
        assert tc.get("bg_base") == tc._DARK["bg_base"]
        tc.set_theme("light")
        assert tc.get("bg_base") == tc._LIGHT["bg_base"]
    finally:
        tc.set_theme(original)


def test_connect_theme_changed_is_safe_without_manager():
    # Headless/test contexts have no ThemeManager; the hook must no-op,
    # not crash, so widgets can call it unconditionally.
    if theme.instance() is None:
        assert theme.connect_theme_changed(lambda: None) is False
