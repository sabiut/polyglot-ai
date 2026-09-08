"""AWS panel: overview rendering, details commands, and main-window wiring.

No AWS calls are made — ``aws_cli.run`` is stubbed.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core import aws_cli  # noqa: E402
from polyglot_ai.ui.panels.aws_panel import AwsPanel, fetch_overview  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _pump_until(pred, timeout_s=3.0):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


SAMPLE = {
    "identity": {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/sum"},
    "lambda": [{"name": "api-handler", "runtime": "python3.12", "modified": "2026-09-01 10:00"}],
    "ec2": [
        {"id": "i-0abc", "name": "web-1", "state": "running", "type": "t3.micro", "ip": "1.2.3.4"},
        {"id": "i-0def", "name": "", "state": "stopped", "type": "t3.small", "ip": ""},
    ],
    "ecs": [{"cluster": "prod", "name": "api", "running": 2, "desired": 2, "status": "ACTIVE"}],
    "s3": [{"name": "my-bucket", "created": "2025-01-01"}],
    "errors": {},
}


def _rows(panel: AwsPanel) -> dict[str, list[str]]:
    out = {}
    root = panel._tree.invisibleRootItem()
    for i in range(root.childCount()):
        section = root.child(i)
        out[section.text(0)] = [section.child(j).text(0) for j in range(section.childCount())]
    return out


class TestOverviewRendering:
    def test_populates_sections_and_identity(self, qapp):
        panel = AwsPanel()
        panel._profile, panel._region = "default", "us-east-1"
        panel._on_overview(SAMPLE)
        rows = _rows(panel)
        assert rows["Lambda (1)"] == ["api-handler"]
        assert rows["EC2 (2)"] == ["web-1  (i-0abc)", "i-0def"]
        assert rows["ECS services (1)"] == ["prod / api"]
        assert rows["S3 buckets (1)"] == ["my-bucket"]
        assert "123456789012" in panel._identity_label.text()
        assert "1 functions" in panel._status_label.text()

    def test_identity_failure_shows_hint_not_empty_tree(self, qapp):
        panel = AwsPanel()
        panel._on_overview(
            {
                "identity": None,
                "lambda": [],
                "ec2": [],
                "ecs": [],
                "s3": [],
                "errors": {"identity": "No AWS credentials found — run `aws configure`."},
            }
        )
        root = panel._tree.invisibleRootItem()
        assert root.childCount() == 1
        assert "aws configure" in root.child(0).text(0)
        assert "aws configure" in panel._status_label.text()

    def test_per_section_error_is_shown_under_its_section(self, qapp):
        data = dict(
            SAMPLE,
            ec2=[],
            errors={"ec2": "Access denied — this profile lacks permission for that call."},
        )
        panel = AwsPanel()
        panel._on_overview(data)
        rows = _rows(panel)
        assert rows["EC2 (0)"] == ["Access denied — this profile lacks permission for that call."]
        assert "ec2: Access denied" in panel._status_label.text()


class TestDetails:
    def test_selecting_lambda_runs_get_function_configuration(self, qapp, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append((list(argv), kw))
            return '{"FunctionName": "api-handler", "Runtime": "python3.12"}', 0

        monkeypatch.setattr(aws_cli, "run", fake_run)
        panel = AwsPanel()
        panel._profile, panel._region = "work", "eu-west-1"
        panel._on_overview(SAMPLE)
        lam_item = panel._tree.invisibleRootItem().child(0).child(0)
        panel._tree.setCurrentItem(lam_item)
        assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())
        argv, kw = calls[-1]
        assert argv[:3] == ["lambda", "get-function-configuration", "--function-name"]
        assert kw["profile"] == "work" and kw["region"] == "eu-west-1"
        assert panel._details_title.text() == "LAMBDA — api-handler"
        # JSON is pretty-printed for humans
        assert '"FunctionName": "api-handler"' in panel._details.toPlainText()

    def test_s3_listing_is_not_json(self, qapp, monkeypatch):
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw)
            seen["argv"] = list(argv)
            return "2025-01-01 10:00:00   12 Bytes hello.txt", 0

        monkeypatch.setattr(aws_cli, "run", fake_run)
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        bucket_item = panel._tree.invisibleRootItem().child(3).child(0)
        panel._tree.setCurrentItem(bucket_item)
        assert _pump_until(lambda: "hello.txt" in panel._details.toPlainText())
        assert seen["argv"][:3] == ["s3", "ls", "s3://my-bucket"]
        assert seen["json_output"] is False


class TestFetchOverview:
    def test_stops_after_identity_failure(self, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            return "Error: No AWS credentials found — run `aws configure`.", 255

        monkeypatch.setattr(aws_cli, "run", fake_run)
        data = fetch_overview("default", "us-east-1")
        assert data["identity"] is None
        assert "identity" in data["errors"]
        assert calls == [["sts", "get-caller-identity"]]  # no further calls

    def test_sections_fail_independently(self, monkeypatch):
        def fake_run(argv, **kw):
            if argv[:2] == ["sts", "get-caller-identity"]:
                return '{"Account": "1", "Arn": "arn:aws:iam::1:user/x"}', 0
            if argv[0] == "lambda":
                return (
                    '{"Functions": [{"FunctionName": "f", "Runtime": "python3.12", "LastModified": "2026-09-01T10:00:00"}]}',
                    0,
                )
            if argv[0] == "ec2":
                return "Error: Access denied — this profile lacks permission for that call.", 254
            if argv[:2] == ["ecs", "list-clusters"]:
                return '{"clusterArns": []}', 0
            if argv[:2] == ["s3api", "list-buckets"]:
                return '{"Buckets": [{"Name": "b", "CreationDate": "2025-01-01T00:00:00"}]}', 0
            return "{}", 0

        monkeypatch.setattr(aws_cli, "run", fake_run)
        data = fetch_overview("default", "us-east-1")
        assert [f["name"] for f in data["lambda"]] == ["f"]
        assert data["ec2"] == []
        assert "Access denied" in data["errors"]["ec2"]
        assert [b["name"] for b in data["s3"]] == ["b"]


class TestMainWindowWiring:
    def test_activity_bar_menu_and_palette(self, qapp):
        from polyglot_ai.ui.main_window import MainWindow

        w = MainWindow()
        assert "aws" in w._activity_bar._buttons
        w._on_activity_changed("aws")
        assert w._sidebar_stack.currentWidget() is w._aws_panel
        ids = {a.action_id for a in w.action_registry.get_all()}
        assert "view.aws" in ids
        assert w._action_toggle_aws.shortcut().toString() == "Ctrl+Shift+W"
        w.close()
