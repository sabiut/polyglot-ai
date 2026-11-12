"""“Create snapshot” on an RDS row: the command, the questions asked first, a fake CLI."""

from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from polyglot_ai.core import aws_cli, panel_state  # noqa: E402
from polyglot_ai.core.aws_resources import (  # noqa: E402
    _fetch_rds,
    default_snapshot_name,
    list_snapshots_command,
    snapshot_command,
    snapshot_name_problem,
)
from polyglot_ai.ui.panels.aws_panel import AwsPanel  # noqa: E402

INSTANCE = {
    "type": "rds",
    "id": "orders-db",
    "name": "orders-db",
    "engine": "postgres",
    "cluster": "",
}
AURORA = {
    "type": "rds",
    "id": "shop-instance-1",
    "name": "shop-instance-1",
    "engine": "aurora-postgresql",
    "cluster": "shop",
}


# ── the command ─────────────────────────────────────────────────────


def test_rows_say_which_cluster_they_belong_to():
    answer = {
        "DBInstances": [
            {"DBInstanceIdentifier": "orders-db", "Engine": "postgres"},
            {
                "DBInstanceIdentifier": "shop-instance-1",
                "Engine": "aurora-postgresql",
                "DBClusterIdentifier": "shop",
            },
        ]
    }
    rows = _fetch_rds(lambda argv: answer)
    assert [(r["id"], r["cluster"]) for r in rows] == [
        ("orders-db", ""),
        ("shop-instance-1", "shop"),
    ]


def test_an_instance_is_snapshotted_by_its_own_name():
    assert snapshot_command(INSTANCE, "before-upgrade") == [
        "rds",
        "create-db-snapshot",
        "--db-instance-identifier",
        "orders-db",
        "--db-snapshot-identifier",
        "before-upgrade",
    ]


def test_a_cluster_member_is_snapshotted_as_its_cluster():
    """RDS refuses create-db-snapshot for Aurora and Multi-AZ cluster members."""
    assert snapshot_command(AURORA, "before-upgrade") == [
        "rds",
        "create-db-cluster-snapshot",
        "--db-cluster-identifier",
        "shop",
        "--db-cluster-snapshot-identifier",
        "before-upgrade",
    ]


def test_listing_snapshots_changes_nothing():
    assert list_snapshots_command(INSTANCE)[:4] == [
        "rds",
        "describe-db-snapshots",
        "--db-instance-identifier",
        "orders-db",
    ]
    assert list_snapshots_command(AURORA)[:4] == [
        "rds",
        "describe-db-cluster-snapshots",
        "--db-cluster-identifier",
        "shop",
    ]


def test_default_name_says_what_and_when_and_is_valid():
    when = time.mktime((2026, 11, 12, 22, 15, 0, 0, 0, -1))
    assert default_snapshot_name(INSTANCE, when) == "orders-db-manual-20261112-2215"
    assert default_snapshot_name(AURORA, when) == "shop-manual-20261112-2215"
    long_cluster = dict(AURORA, cluster="c" + "-x" * 31)  # 63 characters, the longest allowed
    name = default_snapshot_name(long_cluster, when)
    assert len(name) <= 63 and snapshot_name_problem(name, long_cluster) == ""


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("before-upgrade-2", ""),
        ("", "Give the snapshot a name."),
        ("2026-backup", "starting with a letter"),
        ("rds:orders-db-2026", "letters, digits and hyphens"),
        ("my snapshot", "letters, digits and hyphens"),
        ("x; rm -rf ~", "letters, digits and hyphens"),
        ("--query", "letters, digits and hyphens"),
        ("ends-", "can't end with a hyphen"),
        ("two--hyphens", "two in a row"),
        ("a" * 256, "at most 255"),
    ],
)
def test_snapshot_names_rds_would_refuse_are_caught_first(name, problem):
    assert problem in snapshot_name_problem(name, INSTANCE)
    if not problem:
        assert snapshot_name_problem(name, INSTANCE) == ""


def test_cluster_snapshot_names_are_shorter():
    assert snapshot_name_problem("a" * 64, INSTANCE) == ""
    assert "at most 63" in snapshot_name_problem("a" * 64, AURORA)


# ── the panel ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_leaked_panel_state():
    yield
    panel_state.clear()


def _pump_until(pred, timeout_s=3.0):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class _Asked:
    """Stands in for the name box and the two message boxes."""

    def __init__(self, monkeypatch, panel, *, name="before-upgrade", accept=True, confirm=True):
        self.confirmations, self.warnings, self.offered = [], [], []

        def get_text(_parent, _title, _label, text=""):
            self.offered.append(text)
            return (text if name is None else name), accept

        monkeypatch.setattr(QInputDialog, "getText", get_text)
        monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: self.warnings.append(text))
        monkeypatch.setattr(
            panel, "_confirm", lambda _title, text: self.confirmations.append(text) or confirm
        )


@pytest.fixture
def panel(qapp, monkeypatch):
    import polyglot_ai.ui.panels.aws_panel as mod

    calls, refreshed = [], []

    def run(argv, **kw):
        calls.append((argv, kw.get("profile"), kw.get("region")))
        return '{"DBSnapshot": {"DBSnapshotIdentifier": "before-upgrade", "Status": "creating"}}', 0

    monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
    monkeypatch.setattr(aws_cli, "run", run)
    monkeypatch.setattr(mod, "fetch_overview", lambda p, r: refreshed.append(r) or {})
    monkeypatch.setattr(AwsPanel, "_FOLLOW_UP_REFRESH_MS", 0)
    widget = AwsPanel()
    widget._profile, widget._region = "work", "eu-west-1"
    widget.calls, widget.refreshed = calls, refreshed
    return widget


def test_snapshot_asks_for_a_name_then_confirms_then_runs(panel, monkeypatch):
    asked = _Asked(monkeypatch, panel)
    panel._rds_snapshot(INSTANCE)

    assert asked.offered[0].startswith("orders-db-manual-")
    (question,) = asked.confirmations
    assert (
        "Create snapshot before-upgrade of RDS orders-db in eu-west-1 (profile work)?" in question
    )
    assert "billed as backup storage" in question
    assert "aws rds create-db-snapshot --db-instance-identifier orders-db" in question

    assert _pump_until(lambda: "creating" in panel._details.toPlainText())
    assert panel.calls == [(snapshot_command(INSTANCE, "before-upgrade"), "work", "eu-west-1")]
    assert panel._details_title.text() == "SNAPSHOT — orders-db"
    assert _pump_until(lambda: panel.refreshed == ["eu-west-1"])


def test_aurora_member_snapshots_the_cluster_and_says_so(panel, monkeypatch):
    asked = _Asked(monkeypatch, panel, name=None)  # keep the offered name
    panel._rds_snapshot(AURORA)
    assert asked.offered[0].startswith("shop-manual-")
    assert "cluster shop (which shop-instance-1 belongs to)" in asked.confirmations[0]
    assert _pump_until(lambda: len(panel.calls) == 1)
    assert panel.calls[0][0][:4] == [
        "rds",
        "create-db-cluster-snapshot",
        "--db-cluster-identifier",
        "shop",
    ]


@pytest.mark.parametrize(
    "answers",
    [{"accept": False}, {"confirm": False}, {"name": "bad name; --force"}, {"name": "  "}],
)
def test_nothing_runs_without_a_valid_name_and_a_yes(panel, monkeypatch, answers):
    asked = _Asked(monkeypatch, panel, **answers)
    panel._rds_snapshot(INSTANCE)
    _pump_until(lambda: False, timeout_s=0.15)
    assert panel.calls == [] and panel.refreshed == []
    if "name" in answers:
        assert len(asked.warnings) == 1 and asked.confirmations == []


def test_a_refused_snapshot_shows_why_and_does_not_refresh(panel, monkeypatch):
    _Asked(monkeypatch, panel)
    monkeypatch.setattr(
        aws_cli,
        "run",
        lambda argv, **kw: ("Error: DBSnapshotAlreadyExists: before-upgrade already exists", 254),
    )
    panel._rds_snapshot(INSTANCE)
    assert _pump_until(lambda: "DBSnapshotAlreadyExists" in panel._details.toPlainText())
    _pump_until(lambda: False, timeout_s=0.15)
    assert panel.refreshed == []


def test_listing_snapshots_needs_no_confirmation(panel, monkeypatch):
    asked = _Asked(monkeypatch, panel)
    panel._rds_snapshots(INSTANCE)
    assert _pump_until(lambda: len(panel.calls) == 1)
    assert panel.calls[0][0][:2] == ["rds", "describe-db-snapshots"]
    assert asked.confirmations == [] and panel._details_title.text() == "SNAPSHOTS — orders-db"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX script")
def test_the_real_cli_wrapper_gets_exactly_these_arguments(qapp, monkeypatch, tmp_path):
    """End to end against a stand-in ``aws`` program: no account is touched."""
    import polyglot_ai.ui.panels.aws_panel as mod

    log = tmp_path / "argv.log"
    fake = tmp_path / "bin" / "aws"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do printf \'%s\\n\' "$arg" >> {log}; done\n'
        'echo \'{"DBSnapshot": {"Status": "creating"}}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    monkeypatch.setattr(mod, "fetch_overview", lambda p, r: {})
    monkeypatch.setattr(AwsPanel, "_FOLLOW_UP_REFRESH_MS", 0)
    panel = AwsPanel()
    panel._profile, panel._region = "work", "eu-west-1"
    _Asked(monkeypatch, panel, name="before-upgrade")

    panel._rds_snapshot(INSTANCE)

    assert _pump_until(lambda: "creating" in panel._details.toPlainText())
    assert log.read_text().splitlines() == [
        "rds",
        "create-db-snapshot",
        "--db-instance-identifier",
        "orders-db",
        "--db-snapshot-identifier",
        "before-upgrade",
        "--profile",
        "work",
        "--region",
        "eu-west-1",
        "--output",
        "json",
    ]
