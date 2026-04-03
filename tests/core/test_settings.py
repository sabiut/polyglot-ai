"""Tests for SettingsManager."""

import pytest

from polyglot_ai.core.settings import SettingsManager


@pytest.mark.asyncio
async def test_get_default(db):
    settings = SettingsManager(db)
    await settings.load()
    assert settings.get("theme") == "dark"
    assert settings.get("editor.font_size") == 11


@pytest.mark.asyncio
async def test_set_and_get(db):
    settings = SettingsManager(db)
    await settings.load()
    await settings.set("theme", "light")
    assert settings.get("theme") == "light"


@pytest.mark.asyncio
async def test_persistence(db):
    settings1 = SettingsManager(db)
    await settings1.load()
    await settings1.set("custom_key", "custom_value")

    # New instance should load persisted value
    settings2 = SettingsManager(db)
    await settings2.load()
    assert settings2.get("custom_key") == "custom_value"


@pytest.mark.asyncio
async def test_delete(db):
    settings = SettingsManager(db)
    await settings.load()
    await settings.set("to_delete", "value")
    await settings.delete("to_delete")
    assert settings.get("to_delete") is None


@pytest.mark.asyncio
async def test_get_all(db):
    settings = SettingsManager(db)
    await settings.load()
    all_settings = settings.get_all()
    assert "theme" in all_settings
    assert "editor.font_size" in all_settings
