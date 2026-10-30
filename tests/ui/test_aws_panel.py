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
    # Rows in the shape fetch_overview produces (core/aws_resources.py).
    "identity": {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/sum"},
    "lambda": [
        {
            "type": "lambda",
            "id": "api-handler",
            "name": "api-handler",
            "label": "api-handler",
            "runtime": "python3.12",
            "status": "python3.12",
            "info": "2026-09-01 10:00",
        }
    ],
    "ec2": [
        {
            "type": "ec2",
            "id": "i-0abc",
            "name": "web-1",
            "label": "web-1  (i-0abc)",
            "state": "running",
            "status": "running",
            "instance_type": "t3.micro",
            "ip": "1.2.3.4",
            "info": "t3.micro  1.2.3.4",
        },
        {
            "type": "ec2",
            "id": "i-0def",
            "name": "",
            "label": "i-0def",
            "state": "stopped",
            "status": "stopped",
            "ip": "",
            "info": "t3.small",
        },
    ],
    "ecs": [
        {
            "type": "ecs",
            "id": "prod/api",
            "cluster": "prod",
            "name": "api",
            "label": "prod / api",
            "running": 2,
            "desired": 2,
            "status": "2/2",
            "info": "ACTIVE",
        }
    ],
    "s3": [
        {
            "type": "s3",
            "id": "my-bucket",
            "name": "my-bucket",
            "label": "my-bucket",
            "created": "2025-01-01",
            "status": "",
            "info": "2025-01-01",
        }
    ],
    "errors": {},
}


def _rows(panel: AwsPanel) -> dict[str, list[str]]:
    """``{"Title (count)": [child labels]}`` — section text without the ▾/▸."""
    out = {}
    root = panel._tree.invisibleRootItem()
    for i in range(root.childCount()):
        section = root.child(i)
        text = section.text(0)
        if text[:1] in "▾▸":
            text = text[1:].strip()
        out[text] = [section.child(j).text(0) for j in range(section.childCount())]
    return out


def _section(panel: AwsPanel, key: str):
    from PyQt6.QtCore import Qt

    root = panel._tree.invisibleRootItem()
    for i in range(root.childCount()):
        meta = root.child(i).data(0, Qt.ItemDataRole.UserRole)
        if isinstance(meta, dict) and meta.get("section") == key:
            return root.child(i)
    raise AssertionError(f"no section {key}")


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
        assert "5 resources in 4 services" in panel._status_label.text()

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
        lam_item = _section(panel, "lambda").child(0)
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
        bucket_item = _section(panel, "s3").child(0)
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


# ── More resources, readable details, findable sections ──────────────

CANNED = {
    ("sts", "get-caller-identity"): '{"Account": "1", "Arn": "arn:aws:iam::1:user/x"}',
    ("ec2", "describe-instances"): '{"Reservations": []}',
    ("lambda", "list-functions"): '{"Functions": []}',
    ("ecs", "list-clusters"): '{"clusterArns": ["arn:aws:ecs:r:1:cluster/prod"]}',
    ("ecs", "list-services"): '{"serviceArns": ['
    + ", ".join(f'"arn:aws:ecs:r:1:service/prod/svc{i}"' for i in range(23))
    + "]}",
    ("eks", "list-clusters"): '{"clusters": ["platform"]}',
    ("s3api", "list-buckets"): '{"Buckets": []}',
    ("rds", "describe-db-instances"): '{"DBInstances": [{"DBInstanceIdentifier": "orders-db",'
    ' "DBInstanceStatus": "available", "Engine": "postgres", "DBInstanceClass": "db.t3.micro"}]}',
    ("dynamodb", "list-tables"): '{"TableNames": ["sessions"]}',
    ("sqs", "list-queues"): '{"QueueUrls": ["https://sqs.ap-southeast-2.amazonaws.com/1/jobs"]}',
    ("sns", "list-topics"): '{"Topics": [{"TopicArn": "arn:aws:sns:ap-southeast-2:1:alerts"}]}',
    (
        "cloudformation",
        "describe-stacks",
    ): '{"Stacks": [{"StackName": "app", "StackId": "arn:x/app/1",'
    ' "StackStatus": "UPDATE_ROLLBACK_COMPLETE", "CreationTime": "2026-01-01T00:00:00Z"}]}',
    ("cloudwatch", "describe-alarms"): '{"MetricAlarms": ['
    '{"AlarmName": "a-ok", "StateValue": "OK"}, {"AlarmName": "z-fire", "StateValue": "ALARM"}]}',
}


def _canned_run(argv, **kw):
    import json as _json

    if argv[:2] == ["ecs", "describe-services"]:
        names = [a.rsplit("/", 1)[-1] for a in argv[argv.index("--services") + 1 :]]
        assert len(names) <= 10, "describe-services takes at most 10"
        return _json.dumps(
            {"services": [{"serviceName": n, "runningCount": 1, "desiredCount": 1} for n in names]}
        ), 0
    return CANNED.get(tuple(argv[:2]), "{}"), 0


class TestMoreResources:
    def test_new_services_are_listed(self, monkeypatch):
        monkeypatch.setattr(aws_cli, "run", _canned_run)
        data = fetch_overview("default", "ap-southeast-2")
        assert [r["name"] for r in data["rds"]] == ["orders-db"]
        assert [r["name"] for r in data["dynamodb"]] == ["sessions"]
        assert [r["name"] for r in data["sqs"]] == ["jobs"]
        assert [r["name"] for r in data["sns"]] == ["alerts"]
        assert [r["name"] for r in data["eks"]] == ["platform"]
        assert [r["name"] for r in data["cloudformation"]] == ["app"]
        # firing alarms first
        assert [r["name"] for r in data["alarms"]] == ["z-fire", "a-ok"]
        assert all(r["type"] == "rds" for r in data["rds"])

    def test_ecs_pages_past_ten_services(self, monkeypatch):
        monkeypatch.setattr(aws_cli, "run", _canned_run)
        data = fetch_overview("default", "ap-southeast-2")
        assert len(data["ecs"]) == 23  # used to stop at 10

    def test_every_type_can_describe_and_open_in_console(self):
        from polyglot_ai.core.aws_resources import RESOURCE_TYPES

        row = {
            "id": "x",
            "name": "x",
            "cluster": "c",
            "url": "https://q/x",
            "arn": "arn:x",
            "stack_id": "s",
        }
        for rt in RESOURCE_TYPES:
            assert rt.describe is not None and rt.describe(row), rt.key
            url = rt.console_url(row, "ap-southeast-2")
            assert url.startswith("https://") and "ap-southeast-2" in url, rt.key


class TestSummaries:
    def test_lambda_summary_lists_env_names_not_values(self):
        from polyglot_ai.core.aws_resources import BY_KEY

        pairs = dict(
            BY_KEY["lambda"].summarize(
                {
                    "FunctionName": "api",
                    "Runtime": "python3.12",
                    "MemorySize": 256,
                    "Environment": {"Variables": {"DB_PASSWORD": "hunter2", "STAGE": "prod"}},
                }
            )
        )
        assert pairs["Memory"] == "256 MB"
        assert pairs["Environment variables"] == "DB_PASSWORD, STAGE"
        assert "hunter2" not in str(pairs)

    def test_ec2_and_alarm_summaries(self):
        from polyglot_ai.core.aws_resources import BY_KEY

        ec2 = dict(
            BY_KEY["ec2"].summarize(
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-1",
                                    "State": {"Name": "running"},
                                    "InstanceType": "t3.micro",
                                    "Tags": [{"Key": "Name", "Value": "web"}],
                                    "SecurityGroups": [{"GroupName": "web-sg"}],
                                }
                            ]
                        }
                    ]
                }
            )
        )
        assert ec2["Name"] == "web" and ec2["State"] == "running"
        assert ec2["Security groups"] == "web-sg"
        alarm = dict(
            BY_KEY["alarms"].summarize(
                {
                    "MetricAlarms": [
                        {"AlarmName": "cpu", "StateValue": "ALARM", "StateReason": "CPU > 90"}
                    ]
                }
            )
        )
        assert alarm["Why"] == "CPU > 90"

    def test_details_pane_shows_summary_then_full_output(self, qapp, monkeypatch):
        monkeypatch.setattr(
            aws_cli,
            "run",
            lambda argv, **kw: (
                '{"FunctionName": "api-handler", "Runtime": "python3.12", "MemorySize": 128}',
                0,
            ),
        )
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
        assert _pump_until(lambda: "Full output" in panel._details.toPlainText())
        text = panel._details.toPlainText()
        assert text.index("Memory") < text.index("Full output") < text.index('"FunctionName"')
        assert panel._console_btn.isEnabled()


class TestFindableSections:
    def test_empty_sections_fold_into_one_note(self, qapp):
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        texts = [
            panel._tree.topLevelItem(i).text(0) for i in range(panel._tree.topLevelItemCount())
        ]
        assert texts[0].startswith("▾")
        assert any(t.startswith("None here:") and "RDS databases" in t for t in texts)

    def test_click_toggles_and_collapse_survives_refresh(self, qapp):
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        s3 = _section(panel, "s3")
        panel._on_item_clicked(s3, 0)
        assert not s3.isExpanded() and s3.text(0).startswith("▸")
        panel._on_overview(SAMPLE)  # refresh
        s3 = _section(panel, "s3")
        assert not s3.isExpanded() and s3.text(0).startswith("▸")
        panel._on_item_clicked(s3, 0)
        assert s3.isExpanded() and s3.text(0).startswith("▾")

    def test_filter_hides_non_matching(self, qapp):
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        panel._filter.setText("web")
        ec2 = _section(panel, "ec2")
        assert not ec2.child(0).isHidden() and ec2.child(1).isHidden()
        assert _section(panel, "s3").isHidden()
        panel._filter.setText("")
        assert not _section(panel, "s3").isHidden()


def test_send_to_ai_redacts_secrets(qapp):
    panel = AwsPanel()
    sent = []

    class _Chat:
        def prefill_input(self, text):
            sent.append(text)

    panel.chat_panel = _Chat()  # the panel is its own window here
    panel._details.setPlainText('{"Environment": {"Variables": {"DB_PASSWORD": "hunter2hunter2"}}}')
    panel._send_details_to_ai()
    assert sent and "hunter2hunter2" not in sent[0]
