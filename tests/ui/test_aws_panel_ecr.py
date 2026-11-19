"""The AWS panel showing ECR repositories, over the made-up registry in the core tests."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QMenu  # noqa: E402

from polyglot_ai.core import aws_cli  # noqa: E402
from polyglot_ai.ui.panels.aws_panel import AwsPanel, fetch_overview  # noqa: E402
from tests.core.test_aws_ecr import REGISTRY, FakeEcr  # noqa: E402
from tests.ui.test_aws_panel import _pump_until, _rows, _section  # noqa: E402

# ``qapp`` is pytest-qt's, which keeps the one application for the whole run.


def _account(monkeypatch, **kw) -> FakeEcr:
    """An account with a registry in it and nothing else."""
    aws = FakeEcr(**kw)

    def run(argv, **_kw):
        if argv[:2] == ["sts", "get-caller-identity"]:
            return '{"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/x"}', 0
        return aws.run(argv) if argv[0] == "ecr" else ("{}", 0)

    monkeypatch.setattr(aws_cli, "run", run)
    return aws


def _panel() -> AwsPanel:
    panel = AwsPanel()
    panel._profile, panel._region = "work", "eu-west-1"
    panel._on_overview(fetch_overview("work", "eu-west-1"))
    return panel


@pytest.fixture
def account(monkeypatch):
    return _account(monkeypatch)


@pytest.fixture
def panel(qapp, account):
    return _panel()


def test_the_panel_lists_repositories_with_their_images(panel):
    assert _rows(panel) == {"ECR repositories (3)": ["Empty", "team/api", "web"]}
    assert "3 resources in 1 services" in panel._status_label.text()
    section = _section(panel, "ecr")
    web, empty = section.child(2), section.child(0)
    assert (web.text(1), web.text(2)) == (
        "3 images",
        "latest: latest, v1.1  2026-11-10  2 critical, 5 high, 12 lower",
    )
    assert empty.text(1) == "empty"
    assert empty.foreground(1).color() != web.foreground(1).color()


def test_more_images_than_were_read_is_not_more_repositories(qapp, monkeypatch):
    _account(monkeypatch, page=2)
    panel = _panel()
    assert list(_rows(panel)) == ["ECR repositories (3)"]
    assert _section(panel, "ecr").child(2).text(1) == "2+ images"


def test_details_say_what_it_is_and_then_what_is_in_it(panel, account):
    panel._tree.setCurrentItem(_section(panel, "ecr").child(2))
    assert _pump_until(lambda: "── Full output ──" in panel._details.toPlainText())
    text = panel._details.toPlainText()
    assert panel._details_title.text() == "ECR — web"
    order = ["URI", "Images", "Newest", "Lifecycle policy", "Access policy", "── Full output ──"]
    assert [text.index(word) for word in order] == sorted(text.index(word) for word in order)
    assert "account 444455556666, role/deploy in 777788889999 can pull" in text
    assert panel._console_url(panel._details_row).endswith(
        "/ecr/repositories/private/111122223333/web?region=eu-west-1"
    )
    assert {aws_cli.classify(argv) for argv in account.calls} == {"read"}


def test_a_repositorys_menu_copies_its_uri_and_a_login_command(panel, monkeypatch):
    offered: list[str] = []
    copied: list[str] = []

    def choose(menu, *_args):
        offered[:] = [a.text() for a in menu.actions() if a.text()]
        for wanted in ("Copy repository URI", "Copy docker login command"):
            next(a for a in menu.actions() if a.text() == wanted).trigger()

    monkeypatch.setattr(QMenu, "exec", choose)
    monkeypatch.setattr(panel, "_copy_text", copied.append)
    panel.resize(700, 900)
    item = _section(panel, "ecr").child(1)
    panel._tree.scrollToItem(item)
    panel._show_context_menu(panel._tree.visualItemRect(item).center())
    assert offered == [
        "Describe",
        "Copy repository URI",
        "Copy docker login command",
        "Open in AWS Console",
        "Copy ARN",
        "Send details to AI",
    ]
    assert copied == [
        f"{REGISTRY}/team/api",
        "aws ecr get-login-password --profile work --region eu-west-1"
        f" | docker login --username AWS --password-stdin {REGISTRY}",
    ]
