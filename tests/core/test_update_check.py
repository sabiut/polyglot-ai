"""Tests for the GitHub release update check.

We don't hit the real network — every test patches ``_fetch_latest``
or ``urlopen`` so the cache logic, semver comparison, and result
shape are exercised without flake risk.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from polyglot_ai.core import update_check
from polyglot_ai.core.update_check import (
    UpdateInfo,
    _is_newer,
    _parse_version,
    check_for_update,
)


class TestParseVersion:
    def test_plain_semver(self):
        assert _parse_version("0.12.0") == (0, 12, 0)

    def test_v_prefix_stripped(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)

    def test_extra_suffix_ignored(self):
        # The regex anchors on the first three numeric components,
        # so a ``-rc1`` suffix lets the parse succeed (we treat it
        # as the same numeric version) — same behaviour as a
        # ``+build.42`` would get. Pre-releases are not first-class
        # citizens here.
        assert _parse_version("0.12.0-rc1") == (0, 12, 0)

    def test_garbage_returns_none(self):
        for s in ("nightly", "", "1.2", "vee.three.zero"):
            assert _parse_version(s) is None, s


class TestIsNewer:
    def test_strictly_greater(self):
        assert _is_newer("0.13.0", "0.12.0") is True
        assert _is_newer("1.0.0", "0.99.99") is True

    def test_equal_is_not_newer(self):
        assert _is_newer("0.12.0", "0.12.0") is False

    def test_older_is_not_newer(self):
        assert _is_newer("0.11.0", "0.12.0") is False

    def test_unparseable_returns_false(self):
        # Don't prompt the user to "upgrade" to a tag we can't
        # parse — they'd be downgrading or sidegrading.
        assert _is_newer("nightly", "0.12.0") is False
        assert _is_newer("0.13.0", "junk") is False


class TestCheckForUpdate:
    def setup_method(self):
        # Each test gets a fresh cache directory so the 24-hour
        # latch can't carry between tests.
        self._cache_path_orig = update_check._CACHE_PATH

    def teardown_method(self):
        update_check._CACHE_PATH = self._cache_path_orig

    def _redirect_cache(self, tmp_path: Path) -> None:
        update_check._CACHE_PATH = tmp_path / "update_check.json"

    def test_returns_update_info_when_newer(self, tmp_path):
        self._redirect_cache(tmp_path)
        with patch.object(
            update_check,
            "_fetch_latest",
            return_value=("0.13.0", "https://example/r", "2026-05-02T00:00:00Z"),
        ):
            result = check_for_update(current_version="0.12.0")
        assert isinstance(result, UpdateInfo)
        assert result.latest_version == "0.13.0"
        assert result.current_version == "0.12.0"
        assert result.release_url == "https://example/r"

    def test_returns_none_when_up_to_date(self, tmp_path):
        self._redirect_cache(tmp_path)
        with patch.object(update_check, "_fetch_latest", return_value=("0.12.0", "u", "t")):
            assert check_for_update(current_version="0.12.0") is None

    def test_cache_skips_repeat_check(self, tmp_path):
        self._redirect_cache(tmp_path)
        # First call hits the network; cache gets stamped.
        with patch.object(update_check, "_fetch_latest", return_value=("0.12.0", "u", "t")):
            check_for_update(current_version="0.12.0")
        # Second call should NOT call _fetch_latest at all.
        with patch.object(update_check, "_fetch_latest") as fetch:
            check_for_update(current_version="0.12.0")
            assert fetch.call_count == 0

    def test_force_bypasses_cache(self, tmp_path):
        self._redirect_cache(tmp_path)
        # Pretend we just checked — write the cache directly.
        update_check._CACHE_PATH.write_text(json.dumps({"mtime": 9_999_999_999}), encoding="utf-8")
        with patch.object(update_check, "_fetch_latest") as fetch:
            fetch.return_value = ("0.13.0", "u", "t")
            result = check_for_update(current_version="0.12.0", force=True)
            assert fetch.call_count == 1
            assert result is not None and result.latest_version == "0.13.0"

    def test_network_error_returns_none(self, tmp_path):
        self._redirect_cache(tmp_path)
        with patch.object(update_check, "_fetch_latest", side_effect=OSError("network down")):
            # Must NOT raise — a flaky network on launch shouldn't
            # crash the app.
            assert check_for_update(current_version="0.12.0") is None
