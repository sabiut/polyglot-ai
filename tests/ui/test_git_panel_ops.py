"""Tests for the git panel's remote-sync, bulk-stage, and diff-view ops.

Covers the three feature groups added on top of commit/push:

* Pull / Fetch buttons — exist, are tooltipped, and launch the right
  async task (git itself is mocked via a recorded ``safe_task``).
* Stage All / Unstage All — same wiring contract.
* Double-click a changed file — launches the diff task, and the
  content-assembly helper produces the correct (old, new) pair against
  a real temp git repo, including untracked and binary edge cases.

Async runners are exercised with ``asyncio.run`` since the panel's
``_run_git`` only needs an event loop, not the qasync bridge.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402

from polyglot_ai.ui.panels.git_panel import STATUS_ROLE, GitPanel  # noqa: E402


# ── Helpers / fixtures ──


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return stdout, failing the test on error."""
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file (``file.txt``)."""
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "file.txt").write_text("old line\n")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.fixture
def panel(qtbot, git_repo: Path) -> GitPanel:
    """A GitPanel pointed at the temp repo, with polling disabled.

    ``_is_git_repo`` stays False so ``_refresh`` (and its background
    thread + 10 s timer tick) short-circuits — tests drive the panel
    surface directly instead of racing the poller.
    """
    p = GitPanel()
    qtbot.addWidget(p)
    p._refresh_timer.stop()
    p._project_root = git_repo
    p._is_git_repo = False
    return p


class TaskRecorder:
    """Stand-in for ``safe_task`` that records names and closes coros."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, coro, *, name: str = "unnamed", on_error=None):
        self.names.append(name)
        coro.close()  # avoid "coroutine was never awaited" warnings

        class _FakeTask:
            def cancel(self) -> None:
                pass

        return _FakeTask()


@pytest.fixture
def recorded_tasks(monkeypatch) -> TaskRecorder:
    """Replace safe_task so handlers record instead of running git."""
    import polyglot_ai.core.async_utils as async_utils

    recorder = TaskRecorder()
    monkeypatch.setattr(async_utils, "safe_task", recorder)
    return recorder


# ── Buttons exist and are wired ──


class TestButtonsExist:
    def test_pull_and_fetch_buttons_exist_with_tooltips(self, panel: GitPanel):
        assert "Pull" in panel._pull_btn.text()
        assert "Fetch" in panel._fetch_btn.text()
        assert panel._pull_btn.toolTip()
        assert panel._fetch_btn.toolTip()

    def test_stage_all_and_unstage_all_buttons_exist_with_tooltips(self, panel: GitPanel):
        assert panel._stage_all_btn.text() == "Stage All"
        assert panel._unstage_all_btn.text() == "Unstage All"
        assert "git add -A" in panel._stage_all_btn.toolTip()
        assert "git reset" in panel._unstage_all_btn.toolTip()


class TestButtonWiring:
    def test_pull_click_launches_git_pull_task(self, panel: GitPanel, recorded_tasks):
        panel._pull_btn.click()
        assert recorded_tasks.names == ["git_pull"]
        # Busy state while the task is in flight
        assert not panel._pull_btn.isEnabled()
        assert "Pulling" in panel._pull_btn.text()
        # And the reset path restores it
        panel._reset_pull_button()
        assert panel._pull_btn.isEnabled()

    def test_fetch_click_launches_git_fetch_task(self, panel: GitPanel, recorded_tasks):
        panel._fetch_btn.click()
        assert recorded_tasks.names == ["git_fetch"]
        assert not panel._fetch_btn.isEnabled()
        panel._reset_fetch_button()
        assert panel._fetch_btn.isEnabled()

    def test_stage_all_click_launches_task(self, panel: GitPanel, recorded_tasks):
        panel._stage_all_btn.click()
        assert recorded_tasks.names == ["git_stage_all"]

    def test_unstage_all_click_launches_task(self, panel: GitPanel, recorded_tasks):
        panel._unstage_all_btn.click()
        assert recorded_tasks.names == ["git_unstage_all"]

    def test_no_project_root_shows_message_instead_of_task(
        self, panel: GitPanel, recorded_tasks, monkeypatch
    ):
        import polyglot_ai.ui.panels.git_panel as gp

        messages: list[tuple[str, str]] = []
        monkeypatch.setattr(
            gp, "show_message", lambda _p, title, msg, kind="info": messages.append((title, kind))
        )
        panel._project_root = None
        panel._pull_btn.click()
        panel._fetch_btn.click()
        assert recorded_tasks.names == []
        assert messages == [("No project", "info"), ("No project", "info")]


# ── Double-click → diff task ──


class TestDiffDoubleClick:
    def test_double_click_unstaged_item_launches_diff_task(self, panel: GitPanel, recorded_tasks):
        from PyQt6.QtWidgets import QListWidgetItem

        item = QListWidgetItem("  M  file.txt")
        item.setData(Qt.ItemDataRole.UserRole, "file.txt")
        item.setData(STATUS_ROLE, "M")
        panel._unstaged_list.addItem(item)

        panel._unstaged_list.itemDoubleClicked.emit(item)
        assert recorded_tasks.names == ["git_view_diff"]

    def test_double_click_staged_item_launches_diff_task(self, panel: GitPanel, recorded_tasks):
        from PyQt6.QtWidgets import QListWidgetItem

        item = QListWidgetItem("  M  file.txt")
        item.setData(Qt.ItemDataRole.UserRole, "file.txt")
        item.setData(STATUS_ROLE, "M")
        panel._staged_list.addItem(item)

        panel._staged_list.itemDoubleClicked.emit(item)
        assert recorded_tasks.names == ["git_view_diff"]

    def test_item_without_filepath_is_ignored(self, panel: GitPanel, recorded_tasks):
        from PyQt6.QtWidgets import QListWidgetItem

        item = QListWidgetItem("  (garbage)")
        panel._unstaged_list.addItem(item)
        panel._unstaged_list.itemDoubleClicked.emit(item)
        assert recorded_tasks.names == []


# ── Diff content assembly against a real repo ──


class TestDiffContentAssembly:
    def test_tracked_modified_unstaged(self, panel: GitPanel, git_repo: Path):
        (git_repo / "file.txt").write_text("new line\n")
        old, new = asyncio.run(panel._load_diff_contents("file.txt", "M", staged=False))
        assert old == "old line\n"
        assert new == "new line\n"

    def test_tracked_modified_staged_uses_index_not_worktree(self, panel: GitPanel, git_repo: Path):
        (git_repo / "file.txt").write_text("staged line\n")
        _git(git_repo, "add", "file.txt")
        # Dirty the worktree AGAIN so index != worktree — the staged
        # list must diff HEAD against the index version.
        (git_repo / "file.txt").write_text("worktree line\n")
        old, new = asyncio.run(panel._load_diff_contents("file.txt", "M", staged=True))
        assert old == "old line\n"
        assert new == "staged line\n"

    def test_untracked_file_has_empty_old_side(self, panel: GitPanel, git_repo: Path):
        (git_repo / "brand_new.txt").write_text("hello\n")
        old, new = asyncio.run(panel._load_diff_contents("brand_new.txt", "?", staged=False))
        assert old == ""
        assert new == "hello\n"

    def test_deleted_worktree_file_has_empty_new_side(self, panel: GitPanel, git_repo: Path):
        (git_repo / "file.txt").unlink()
        old, new = asyncio.run(panel._load_diff_contents("file.txt", "D", staged=False))
        assert old == "old line\n"
        assert new == ""

    def test_binary_worktree_file_shows_placeholder(self, panel: GitPanel, git_repo: Path):
        (git_repo / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
        old, new = asyncio.run(panel._load_diff_contents("blob.bin", "?", staged=False))
        assert old == ""
        assert new == "(binary file)"

    def test_guard_binary_replaces_nul_content(self):
        assert GitPanel._guard_binary("abc\x00def") == "(binary file)"
        assert GitPanel._guard_binary("plain text") == "plain text"


# ── Async runners against a real repo ──


class TestAsyncRunners:
    def test_stage_all_stages_untracked_files(self, panel: GitPanel, git_repo: Path):
        (git_repo / "extra.txt").write_text("x\n")
        asyncio.run(panel._run_stage_all())
        status = _git(git_repo, "status", "--porcelain")
        assert "A  extra.txt" in status

    def test_unstage_all_clears_the_index(self, panel: GitPanel, git_repo: Path):
        (git_repo / "extra.txt").write_text("x\n")
        _git(git_repo, "add", "-A")
        asyncio.run(panel._run_unstage_all())
        status = _git(git_repo, "status", "--porcelain")
        assert "?? extra.txt" in status

    def test_pull_failure_shows_error_and_reenables_button(self, panel: GitPanel, monkeypatch):
        """No remote configured → pull fails; the panel must surface
        git's stderr in a dialog and restore the button, not crash."""
        import polyglot_ai.ui.panels.git_panel as gp

        messages: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            gp,
            "show_message",
            lambda _p, title, msg, kind="info": messages.append((title, msg, kind)),
        )
        panel._pull_btn.setEnabled(False)  # simulate the in-flight state
        asyncio.run(panel._run_pull())
        assert len(messages) == 1
        title, msg, kind = messages[0]
        assert title == "Pull failed"
        assert kind == "error"
        assert msg  # git's stderr made it into the dialog body
        assert panel._pull_btn.isEnabled()

    def test_fetch_failure_shows_error_and_reenables_button(self, panel: GitPanel, monkeypatch):
        import polyglot_ai.ui.panels.git_panel as gp

        messages: list[tuple[str, str]] = []
        monkeypatch.setattr(
            gp, "show_message", lambda _p, title, msg, kind="info": messages.append((title, kind))
        )
        panel._fetch_btn.setEnabled(False)
        asyncio.run(panel._run_fetch())
        assert messages == [("Fetch failed", "error")]
        assert panel._fetch_btn.isEnabled()


# ── Pull summary formatting ──


class TestPullSummary:
    def test_up_to_date(self):
        assert GitPanel._summarize_pull("Already up to date.\n") == "Already up to date."

    def test_files_changed_summary_uses_last_line(self):
        out = (
            "Updating abc..def\nFast-forward\n file.txt | 2 +-\n 3 files changed, 4 insertions(+)\n"
        )
        assert GitPanel._summarize_pull(out) == "Pulled: 3 files changed, 4 insertions(+)"

    def test_empty_output(self):
        assert GitPanel._summarize_pull("") == "Pulled from origin."
