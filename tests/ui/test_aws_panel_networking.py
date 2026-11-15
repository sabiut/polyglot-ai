"""The AWS panel showing the Networking sections, over the made-up account in the core tests."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QMenu  # noqa: E402

from polyglot_ai.core import aws_cli  # noqa: E402
from polyglot_ai.ui.panels.aws_panel import AwsPanel, fetch_overview  # noqa: E402
from tests.core.test_aws_networking import NETWORKING, FakeAws  # noqa: E402
from tests.ui.test_aws_panel import _pump_until, _rows, _section  # noqa: E402


# ``qapp`` is pytest-qt's, which keeps the one application for the whole run. A
# module of its own would destroy it — and the thread bridge with it — on the way out.


@pytest.fixture
def account(monkeypatch):
    """An account with networking in it and nothing else."""
    aws = FakeAws()

    def run(argv, **_kw):
        if argv[:2] == ["sts", "get-caller-identity"]:
            return '{"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/x"}', 0
        if (argv[0], argv[1]) in {(c[0], c[1]) for c in KNOWN}:
            return aws.run(argv)
        return "{}", 0

    monkeypatch.setattr(aws_cli, "run", run)
    return aws


KNOWN = [
    ("ec2", "describe-vpcs"),
    ("ec2", "describe-subnets"),
    ("ec2", "describe-route-tables"),
    ("ec2", "describe-internet-gateways"),
    ("ec2", "describe-nat-gateways"),
    ("ec2", "describe-security-groups"),
    ("ec2", "describe-network-interfaces"),
    ("elbv2", "describe-load-balancers"),
    ("elbv2", "describe-listeners"),
    ("elbv2", "describe-target-groups"),
    ("elbv2", "describe-target-health"),
]


@pytest.fixture
def panel(qapp, account):
    panel = AwsPanel()
    panel._profile, panel._region = "work", "eu-west-1"
    panel._on_overview(fetch_overview("work", "eu-west-1"))
    return panel


def test_the_panel_lists_the_five_networking_sections(panel):
    rows = _rows(panel)
    assert list(rows) == [
        "VPCs (2)",
        "Subnets (4)",
        "Load balancers (2)",
        "Target groups (3)",
        "Security groups (5)",
    ]
    assert rows["VPCs (2)"] == ["platform  (vpc-0aaa)", "vpc-0def"]
    assert "16 resources in 5 services" in panel._status_label.text()

    public = _section(panel, "subnets").child(1)
    assert (public.text(0), public.text(1)) == ("public-a  (subnet-0pub)", "public")
    web = _section(panel, "target_groups").child(2)
    assert (web.text(1), web.text(2)) == ("1/3 healthy", "HTTP:8080  instance  ← web")
    bastion = _section(panel, "security_groups").child(0)
    assert bastion.text(1) == "open: 22, 443, 8000-8100, icmp"
    assert (
        bastion.foreground(1).color()
        != _section(panel, "security_groups").child(1).foreground(1).color()
    )


@pytest.mark.parametrize(
    ("key", "index", "title", "first", "then"),
    [
        ("vpc", 0, "VPC — platform", "VPC ID", "Subnets"),
        ("subnets", 1, "SUBNET — public-a", "Subnet ID", "Routes"),
        ("elb", 1, "LOAD BALANCER — web", "DNS name", "Listeners"),
        ("target_groups", 2, "TARGET GROUP — web", "Health check", "Targets"),
        ("security_groups", 0, "SECURITY GROUP — bastion", "Outbound", "Used by"),
    ],
)
def test_details_say_what_it_is_and_then_what_is_in_it(panel, key, index, title, first, then):
    panel._tree.setCurrentItem(_section(panel, key).child(index))
    assert _pump_until(lambda: "── Full output ──" in panel._details.toPlainText())
    text = panel._details.toPlainText()
    assert panel._details_title.text() == title
    assert 0 <= text.index(first) < text.index(then) < text.index("── Full output ──")
    assert "eu-west-1" in panel._console_url(panel._details_row)


def test_opening_every_row_only_reads(panel, account):
    for key in NETWORKING:
        section = _section(panel, key)
        for i in range(section.childCount()):
            before = len(account.calls)
            panel._on_item_selected(section.child(i), None)
            assert _pump_until(lambda: "── Full output ──" in panel._details.toPlainText())
            assert len(account.calls) > before
    assert {aws_cli.classify(argv) for argv in account.calls} == {"read"}


def test_a_load_balancers_menu_copies_its_dns_name(panel, monkeypatch):
    offered: list[str] = []
    copied: list[str] = []

    def choose(menu, *_args):
        offered[:] = [a.text() for a in menu.actions() if a.text()]
        next(a for a in menu.actions() if a.text() == "Copy DNS name").trigger()

    monkeypatch.setattr(QMenu, "exec", choose)
    monkeypatch.setattr(panel, "_copy_text", copied.append)
    panel.resize(700, 900)
    item = _section(panel, "elb").child(1)
    panel._tree.scrollToItem(item)
    panel._show_context_menu(panel._tree.visualItemRect(item).center())
    assert offered == [
        "Describe",
        "Copy DNS name",
        "Open in AWS Console",
        "Copy ARN",
        "Send details to AI",
    ]
    assert copied == ["web-123.eu-west-1.elb.amazonaws.com"]

    # The failed one has no DNS name to copy.
    offered.clear()
    monkeypatch.setattr(
        QMenu,
        "exec",
        lambda menu, *_a: offered.extend(a.text() for a in menu.actions() if a.text()),
    )
    other = _section(panel, "elb").child(0)
    panel._show_context_menu(panel._tree.visualItemRect(other).center())
    assert offered and "Copy DNS name" not in offered
