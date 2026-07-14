"""Tests for the dependency installer's batching logic.

Regression coverage for the "Install all" exit-127 failure: the
Codex CLI installs via ``npm``, but Node.js (which provides npm)
lives in the root batch that runs *after* the userland batch. The
old code ran ``npm install -g @openai/codex`` before Node existed,
died with "npm: not found", and aborted the run before the root
batch could install Node.
"""

from __future__ import annotations

import pytest

from polyglot_ai.core import dependency_check as dc
from polyglot_ai.core.dependency_check import Dependency, _bucket_deps


def _dep(
    key: str,
    *,
    requires_root: bool = True,
    requires_command: str = "",
    provides_commands: tuple[str, ...] = (),
) -> Dependency:
    return Dependency(
        key=key,
        name=key,
        command=key,
        purpose="test",
        install_urls={"unknown": f"install {key}"},
        requires_root=requires_root,
        requires_command=requires_command,
        provides_commands=provides_commands,
    )


@pytest.fixture
def npm_missing(monkeypatch):
    """Pretend npm (and only npm) is not installed anywhere."""
    monkeypatch.setattr(
        dc, "find_executable", lambda cmd: None if cmd == "npm" else f"/usr/bin/{cmd}"
    )


def test_npm_dep_deferred_after_node(npm_missing) -> None:
    """codex moves to the END of the root batch when node is being installed."""
    node = _dep("node", provides_commands=("node", "npm", "npx"))
    codex = _dep("codex", requires_root=False, requires_command="npm")
    other_user = _dep("mpremote", requires_root=False)
    other_root = _dep("git")

    user, root, unsatisfied = _bucket_deps([node, codex, other_user, other_root])

    assert codex not in user
    assert root == [node, other_root, codex]  # deferred to the end, after node
    assert user == [other_user]
    assert unsatisfied == []


def test_npm_dep_skipped_when_no_provider(npm_missing) -> None:
    """codex is skipped with a reason when nothing in the run provides npm."""
    codex = _dep("codex", requires_root=False, requires_command="npm")
    other_root = _dep("git")

    user, root, unsatisfied = _bucket_deps([codex, other_root])

    assert user == []
    assert root == [other_root]
    assert len(unsatisfied) == 1
    assert "npm" in unsatisfied[0]


def test_npm_dep_stays_userland_when_npm_present(monkeypatch) -> None:
    """No re-bucketing when the required command already exists."""
    monkeypatch.setattr(dc, "find_executable", lambda cmd: f"/usr/bin/{cmd}")
    codex = _dep("codex", requires_root=False, requires_command="npm")

    user, root, unsatisfied = _bucket_deps([codex])

    assert user == [codex]
    assert root == []
    assert unsatisfied == []


def test_plain_split_unaffected(monkeypatch) -> None:
    """Deps without requires_command keep the plain userland/root split."""
    monkeypatch.setattr(dc, "find_executable", lambda cmd: None)
    a = _dep("a", requires_root=False)
    b = _dep("b", requires_root=True)

    user, root, unsatisfied = _bucket_deps([a, b])

    assert user == [a]
    assert root == [b]
    assert unsatisfied == []


def test_install_system_deps_all_unsatisfied(monkeypatch) -> None:
    """Only an unsatisfiable dep to install → clear failure, no subprocess."""
    monkeypatch.setattr(dc, "find_executable", lambda cmd: None)
    monkeypatch.setattr(dc, "detect_distro", lambda: "debian")
    codex = _dep("codex", requires_root=False, requires_command="npm")

    result = dc.install_system_deps([codex])

    assert not result.ok
    assert "npm" in result.message


def test_codex_catalog_entry_declares_npm_requirement() -> None:
    """The real catalog wires codex → npm → node so the regression can't return."""
    by_key = {d.key: d for d in dc.DEPENDENCIES}
    assert by_key["codex"].requires_command == "npm"
    assert "npm" in by_key["node"].provides_commands


class TestNoninteractive:
    """The automated pkexec pass runs with stdin=DEVNULL — without an
    auto-confirm flag, apt/dnf/pacman/zypper read EOF at their Y/n
    prompt and abort every single run, regardless of what the user
    clicks. ``_noninteractive`` must inject the right flag per package
    manager so the automated install can actually complete.
    """

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("sudo apt install ffmpeg", "sudo apt install -y ffmpeg"),
            ("sudo apt-get install ffmpeg", "sudo apt-get install -y ffmpeg"),
            ("sudo dnf install ffmpeg", "sudo dnf install -y ffmpeg"),
            ("sudo pacman -S ffmpeg", "sudo pacman -S --noconfirm ffmpeg"),
            ("sudo zypper install ffmpeg", "sudo zypper install -y ffmpeg"),
            ("sudo apk add ffmpeg", "sudo apk add ffmpeg"),  # already non-interactive
            ("", ""),
        ],
    )
    def test_injects_autoconfirm_flag(self, cmd, expected) -> None:
        assert dc._noninteractive(cmd) == expected

    def test_build_chained_command_uses_noninteractive_form(self) -> None:
        ffmpeg = {d.key: d for d in dc.DEPENDENCIES}["ffmpeg"]
        chained = dc._build_chained_command(
            [ffmpeg], "debian", start_idx=1, total=1, emit_done=True
        )
        assert "apt install -y ffmpeg" in chained
        assert "apt install ffmpeg" not in chained  # would hang/abort under DEVNULL stdin

    def test_install_hint_stays_interactive_for_manual_copy_paste(self) -> None:
        """The dialog's per-dep hint / 'Copy all commands' text must NOT
        gain -y — a user pasting it into a real terminal should see the
        normal interactive confirmation, not a silently-forced install.
        """
        ffmpeg = {d.key: d for d in dc.DEPENDENCIES}["ffmpeg"]
        assert ffmpeg.install_hint("debian") == "sudo apt install ffmpeg"
