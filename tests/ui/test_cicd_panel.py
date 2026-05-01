"""Tests for the CI/CD panel's repo-capability guards.

The panel only makes sense for git-tracked projects with a GitHub
remote. Without these guards the panel surfaced ``gh``'s raw
``fatal: not a git repository`` stderr to users who happened to
open a non-git folder (e.g. a freshly-scaffolded blank Arduino
sketch under ``~/Videos/test``). These tests pin the friendly
empty-state contract so the regression doesn't return.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.ui.panels.cicd_panel import CICDPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _init_git_repo(path: Path, with_github: bool = False) -> None:
    """Create a real git repo at ``path`` so the helpers can probe it."""
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    if with_github:
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:test/test.git"],
            cwd=path,
            check=True,
            capture_output=True,
        )


class TestRepoCapabilityGuards:
    def test_no_project_root_shows_open_project_message(self, qapp):
        panel = CICDPanel()
        panel._refresh_runs()
        # The pre-init message is the same string the original code
        # used; pinning it keeps users' muscle memory intact.
        assert "Open a project first" in panel._status_label.text()

    def test_non_git_folder_shows_friendly_message(self, qapp, tmp_path):
        panel = CICDPanel()
        (tmp_path / "blink.ino").write_text("void setup(){} void loop(){}")
        panel.set_project_root(tmp_path)
        # ``set_project_root`` only auto-refreshes when the panel is
        # visible. Tests never show widgets, so drive the refresh
        # directly to exercise the same code path the real panel hits
        # when the user clicks the Refresh button.
        panel._refresh_runs()

        text = panel._status_label.text()
        # The error must NOT be the raw git/gh stderr that prompted
        # this fix in the first place.
        assert "fatal" not in text.lower()
        assert "not a git repository" not in text.lower()
        # And must mention git so the user knows what's expected.
        assert "git" in text.lower()

    def test_git_repo_without_github_remote_shows_remote_hint(self, qapp, tmp_path):
        _init_git_repo(tmp_path, with_github=False)
        panel = CICDPanel()
        panel.set_project_root(tmp_path)
        panel._refresh_runs()

        text = panel._status_label.text()
        # No GitHub remote → tell the user how to add one. ``gh``
        # never gets invoked, so its stderr can't leak.
        assert "github" in text.lower()
        assert "remote" in text.lower()
        assert "fatal" not in text.lower()

    def test_helper_returns_false_for_non_git(self, qapp, tmp_path):
        panel = CICDPanel()
        panel._project_root = tmp_path
        assert panel._is_git_repo() is False
        assert panel._has_github_remote() is False

    def test_helper_returns_true_for_git_with_github_remote(self, qapp, tmp_path):
        _init_git_repo(tmp_path, with_github=True)
        panel = CICDPanel()
        panel._project_root = tmp_path
        assert panel._is_git_repo() is True
        assert panel._has_github_remote() is True

    def test_helper_returns_false_when_root_missing(self, qapp, tmp_path):
        panel = CICDPanel()
        panel._project_root = tmp_path / "does-not-exist"
        # Subprocess will fail; helpers must report False rather
        # than raise.
        assert panel._is_git_repo() is False
        assert panel._has_github_remote() is False


class TestProjectChangeClearsTable:
    def test_set_project_root_clears_runs_table(self, qapp, tmp_path):
        panel = CICDPanel()
        # Pretend the previous project loaded two runs.
        panel._runs_data = [{"databaseId": 1}, {"databaseId": 2}]
        panel._runs_table.setRowCount(2)

        # Switch to a non-git folder — table should empty so the
        # user doesn't see the previous project's CI runs labelled
        # under the new path.
        panel.set_project_root(tmp_path)
        assert panel._runs_table.rowCount() == 0
        assert panel._runs_data == []
