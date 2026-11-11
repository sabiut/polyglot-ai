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

from polyglot_ai.core import aws_cli, panel_state  # noqa: E402
from polyglot_ai.ui.panels.aws_panel import AwsPanel, fetch_overview  # noqa: E402


@pytest.fixture(autouse=True)
def _no_leaked_panel_state():
    """The panel publishes its profile/region for the AI tools; don't let
    one test's selection reach another test's prompt or tool call."""
    yield
    panel_state.clear()


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
            if argv[:2] != ["s3", "ls"]:
                return "{}", 0  # the summary's own lookups
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
        # In a wrapping label under the tree: as a tree row the list was
        # cut off after the first few names.
        assert not any("None here" in t for t in texts)
        note = panel._empty_note
        assert note.wordWrap() and note.text().startswith("None here:")
        for title in ("RDS databases", "DynamoDB tables", "CloudWatch alarms"):
            assert title in note.text()
        assert not note.isHidden()
        panel._filter.setText("web")  # it's about the account, not the filter
        assert note.isHidden()
        panel._filter.setText("")
        assert not note.isHidden()
        panel._show_hint("Loading…")
        assert note.isHidden() and note.text() == ""

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


def test_stale_overview_does_not_overwrite_newer(qapp, monkeypatch):
    """Switching region quickly: the older fetch finishing last must be dropped."""
    import threading

    gate = threading.Event()
    calls = []

    def slow_fetch(profile, region):
        calls.append(region)
        if region == "us-east-1":
            gate.wait(2)  # the first fetch finishes last
        return dict(SAMPLE, identity={"Account": region, "Arn": "arn:aws:iam::1:user/x"})

    import polyglot_ai.ui.panels.aws_panel as mod

    monkeypatch.setattr(mod, "fetch_overview", slow_fetch)
    monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
    panel = AwsPanel()
    panel._profile, panel._region = "default", "us-east-1"
    panel._refresh()
    panel._region = "eu-west-1"
    panel._refresh()
    assert _pump_until(lambda: "eu-west-1" in panel._identity_label.text())
    gate.set()
    _pump_until(lambda: False, timeout_s=0.5)  # let the stale one land
    assert "eu-west-1" in panel._identity_label.text()
    assert calls == ["us-east-1", "eu-west-1"]


# ── Details must never outlive what they describe ────────────────────


def _describe_by_argv(argv, **kw):
    if argv[:2] == ["lambda", "get-function-configuration"]:
        return '{"FunctionName": "api-handler", "Runtime": "python3.12"}', 0
    if argv[:2] == ["ec2", "describe-instances"]:
        return '{"Reservations": []}', 0
    if argv[:2] == ["ec2", "stop-instances"]:
        return '{"StoppingInstances": [{"InstanceId": "i-0abc"}]}', 0
    return "{}", 0


class TestDetailsFollowTheTree:
    def test_refresh_without_the_row_marks_details_as_gone(self, qapp, monkeypatch):
        """The screenshot bug: a deleted database stayed in the details pane
        as if current, with a live Console button."""
        monkeypatch.setattr(aws_cli, "run", _describe_by_argv)
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
        assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())
        assert panel._console_btn.isEnabled()

        gone = dict(SAMPLE, **{"lambda": []})
        panel._on_overview(gone)
        assert panel._details_title.text() == "LAMBDA — api-handler · no longer listed"
        assert not panel._console_btn.isEnabled()
        assert panel._details_row is None
        assert "python3.12" in panel._details.toPlainText()  # still readable
        panel._on_overview(gone)  # a second refresh doesn't stack the suffix
        assert panel._details_title.text().count("no longer listed") == 1

    def test_refresh_keeps_the_selection_and_reloads_its_details(self, qapp, monkeypatch):
        outputs = iter(
            [
                '{"FunctionName": "api-handler", "Runtime": "python3.12"}',
                '{"FunctionName": "api-handler", "Runtime": "python3.13"}',
            ]
        )
        monkeypatch.setattr(aws_cli, "run", lambda argv, **kw: (next(outputs), 0))
        panel = AwsPanel()
        panel._on_overview(SAMPLE)
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
        assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())

        panel._on_overview(SAMPLE)
        assert panel._tree.currentItem().text(0) == "api-handler"
        assert "Loading" not in panel._details.toPlainText()  # no flicker while reloading
        assert _pump_until(lambda: "python3.13" in panel._details.toPlainText())
        assert panel._console_btn.isEnabled()

    def test_switching_region_clears_rows_and_details(self, qapp, monkeypatch):
        """Old rows are actionable and old details get the new region's
        label and Console link, so both go the moment the context changes."""
        import threading

        import polyglot_ai.ui.panels.aws_panel as mod

        describe_gate, fetch_gate = threading.Event(), threading.Event()

        started = threading.Event()

        def slow_describe(argv, **kw):
            started.set()
            describe_gate.wait(2)
            return '{"FunctionName": "api-handler", "Runtime": "python3.12"}', 0

        def slow_fetch(profile, region):
            fetch_gate.wait(2)
            return dict(SAMPLE, identity={"Account": region, "Arn": "arn:aws:iam::1:user/x"})

        monkeypatch.setattr(aws_cli, "run", slow_describe)
        monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
        monkeypatch.setattr(mod, "fetch_overview", slow_fetch)
        panel = AwsPanel()
        panel._profile, panel._region = "default", "us-east-1"
        panel._on_overview(SAMPLE)
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
        assert _pump_until(started.is_set)  # the describe is in flight

        panel._on_region_changed("eu-west-1")
        assert panel._tree.topLevelItemCount() == 1
        assert "Loading" in panel._tree.topLevelItem(0).text(0)
        assert panel._details.toPlainText() == ""
        assert panel._details_title.text() == "DETAILS"
        assert not panel._console_btn.isEnabled()
        assert panel._console_url(panel._details_row) == ""

        describe_gate.set()  # the us-east-1 describe finishes late: dropped
        _pump_until(lambda: False, timeout_s=0.3)
        assert panel._details.toPlainText() == ""

        fetch_gate.set()
        assert _pump_until(lambda: "eu-west-1" in panel._identity_label.text())
        assert "Lambda (1)" in _rows(panel)

    def test_send_to_ai_names_the_context_the_details_came_from(self, qapp, monkeypatch):
        monkeypatch.setattr(aws_cli, "run", _describe_by_argv)
        panel = AwsPanel()
        sent = []

        class _Chat:
            def prefill_input(self, text):
                sent.append(text)

        panel.chat_panel = _Chat()
        panel._profile, panel._region = "work", "eu-west-1"
        panel._on_overview(SAMPLE)
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
        assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())
        panel._profile, panel._region = "personal", "us-east-1"  # selectors moved on
        panel._send_details_to_ai()
        assert "profile work, region eu-west-1" in sent[0]


class TestRegionField:
    @pytest.fixture
    def panel(self, qapp, monkeypatch):
        import polyglot_ai.ui.panels.aws_panel as mod

        self.fetched = []
        monkeypatch.setattr(
            mod, "fetch_overview", lambda p, r: self.fetched.append(r) or dict(SAMPLE)
        )
        monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
        panel = AwsPanel()
        panel._profile, panel._region = "default", "us-east-1"
        return panel

    def test_typing_does_not_refresh_until_enter(self, panel):
        """Each keystroke used to start a full refresh against a half-typed region."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        edit = panel._region_combo.lineEdit()
        edit.selectAll()
        QTest.keyClicks(edit, "eu-west-3")
        _pump_until(lambda: False, timeout_s=0.2)
        assert self.fetched == [] and panel._region == "us-east-1"

        QTest.keyClick(edit, Qt.Key.Key_Return)
        assert _pump_until(lambda: self.fetched == ["eu-west-3"])
        assert panel._region == "eu-west-3"
        _pump_until(lambda: False, timeout_s=0.2)
        assert self.fetched == ["eu-west-3"]  # Enter then focus-out is still one refresh

    def test_picking_from_the_list_refreshes_once(self, panel):
        index = panel._region_combo.findText("ap-southeast-2")
        panel._region_combo.setCurrentIndex(index)
        panel._region_combo.activated.emit(index)
        assert _pump_until(lambda: self.fetched == ["ap-southeast-2"])

    def test_a_typo_is_refused_without_any_calls(self, panel):
        panel._on_region_changed("eu-west")
        _pump_until(lambda: False, timeout_s=0.2)
        assert self.fetched == [] and panel._region == "us-east-1"
        assert "isn't an AWS region" in panel._status_label.text()

    @pytest.mark.parametrize(
        "region", ["us-east-1", "ap-southeast-2", "us-gov-west-1", "cn-north-1", "il-central-1"]
    )
    def test_real_region_names_are_accepted(self, region):
        import polyglot_ai.ui.panels.aws_panel as mod

        assert mod._REGION_RE.fullmatch(region)


class TestActionsRefreshTheTree:
    @pytest.fixture
    def panel(self, qapp, monkeypatch):
        import polyglot_ai.ui.panels.aws_panel as mod

        self.fetched, self.prompts = [], []
        monkeypatch.setattr(
            mod, "fetch_overview", lambda p, r: self.fetched.append(r) or dict(SAMPLE)
        )
        monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
        monkeypatch.setattr(aws_cli, "run", _describe_by_argv)
        monkeypatch.setattr(AwsPanel, "_FOLLOW_UP_REFRESH_MS", 0)
        panel = AwsPanel()
        monkeypatch.setattr(
            panel, "_confirm", lambda title, text: self.prompts.append(text) or True
        )
        panel._profile, panel._region = "work", "eu-west-1"
        panel._on_overview(SAMPLE)
        return panel

    def test_stop_refreshes_and_keeps_the_result_on_screen(self, panel):
        panel._tree.setCurrentItem(_section(panel, "ec2").child(0))
        assert _pump_until(lambda: "Reservations" in panel._details.toPlainText())

        panel._ec2_action("stop-instances", "i-0abc", "Stop")
        assert "eu-west-1 (profile work)" in self.prompts[0]
        assert _pump_until(lambda: self.fetched == ["eu-west-1"])
        _pump_until(lambda: False, timeout_s=0.3)  # let the refresh land
        # The refresh re-selects the instance but must not replace the
        # action's output with a fresh describe.
        assert panel._details_title.text() == "STOP — i-0abc"
        assert "StoppingInstances" in panel._details.toPlainText()
        assert panel._tree.currentItem().text(0) == "web-1  (i-0abc)"

    def test_refreshes_even_if_another_row_was_clicked_meanwhile(self, panel, monkeypatch):
        import threading

        gate = threading.Event()

        def run(argv, **kw):
            if argv[:2] == ["ec2", "stop-instances"]:
                gate.wait(2)
            return _describe_by_argv(argv, **kw)

        monkeypatch.setattr(aws_cli, "run", run)
        panel._ec2_action("stop-instances", "i-0abc", "Stop")
        panel._tree.setCurrentItem(_section(panel, "lambda").child(0))  # pane moves on
        assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())
        assert self.fetched == []
        gate.set()
        assert _pump_until(lambda: self.fetched == ["eu-west-1"])
        _pump_until(lambda: False, timeout_s=0.3)
        assert panel._details_title.text() == "LAMBDA — api-handler"  # not overwritten

    def test_a_failed_action_does_not_refresh(self, panel, monkeypatch):
        monkeypatch.setattr(aws_cli, "run", lambda argv, **kw: ("Error: Access denied", 254))
        panel._ec2_action("stop-instances", "i-0abc", "Stop")
        assert _pump_until(lambda: "Access denied" in panel._details.toPlainText())
        _pump_until(lambda: False, timeout_s=0.2)
        assert self.fetched == []

    def test_declining_runs_nothing(self, panel, monkeypatch):
        monkeypatch.setattr(panel, "_confirm", lambda title, text: False)
        panel._ecs_redeploy("prod", "api")
        _pump_until(lambda: False, timeout_s=0.2)
        assert self.fetched == [] and panel._details.toPlainText() == ""

    def test_one_follow_up_refresh_shows_the_settled_state(self, panel, monkeypatch):
        monkeypatch.setattr(AwsPanel, "_FOLLOW_UP_REFRESH_MS", 40)
        panel._ecs_redeploy("prod", "api")
        assert "cluster prod, eu-west-1 (profile work)" in self.prompts[0]
        assert _pump_until(lambda: len(self.fetched) == 2)
        _pump_until(lambda: False, timeout_s=0.3)
        assert len(self.fetched) == 2  # one follow-up, not a poll

    def test_follow_up_is_dropped_when_the_region_changes(self, panel, monkeypatch):
        monkeypatch.setattr(AwsPanel, "_FOLLOW_UP_REFRESH_MS", 150)
        panel._ecs_redeploy("prod", "api")
        assert _pump_until(lambda: self.fetched == ["eu-west-1"])
        panel._on_region_changed("us-west-2")
        assert _pump_until(lambda: self.fetched == ["eu-west-1", "us-west-2"])
        _pump_until(lambda: False, timeout_s=0.4)
        assert self.fetched == ["eu-west-1", "us-west-2"]


class TestTruncatedListings:
    def test_fetch_reports_listings_that_were_cut_short(self, monkeypatch):
        import json

        def run(argv, **kw):
            if argv[:2] == ["sts", "get-caller-identity"]:
                return '{"Account": "1", "Arn": "arn:aws:iam::1:user/x"}', 0
            if argv[:2] == ["lambda", "list-functions"]:
                return json.dumps({"Functions": [{"FunctionName": "f"}], "NextToken": "abc"}), 0
            if argv[:2] == ["ecs", "list-clusters"]:
                arns = [f"arn:aws:ecs:us-east-1:1:cluster/c{i}" for i in range(25)]
                return json.dumps({"clusterArns": arns}), 0
            return "{}", 0

        monkeypatch.setattr(aws_cli, "run", run)
        data = fetch_overview("default", "us-east-1")
        assert data["notes"]["lambda"] == "Only the first 200 are shown — there are more."
        assert data["notes"]["ecs"] == "Only the first 20 of 25 clusters are shown."
        assert "ec2" not in data["notes"]  # complete listings carry no note

    def test_ecs_reports_its_per_cluster_service_cap(self):
        from polyglot_ai.core import aws_resources

        notes = []

        def call(argv, **kw):
            if argv[:2] == ["ecs", "list-clusters"]:
                return {"clusterArns": ["arn:aws:ecs:us-east-1:1:cluster/prod"]}
            if argv[:2] == ["ecs", "list-services"]:
                return {"serviceArns": [f"arn:svc/{i}" for i in range(130)]}
            return {"services": []}

        call.note = notes.append
        aws_resources._fetch_ecs(call)
        assert notes == ["Only the first 100 of 130 services in cluster prod are shown."]

    def test_tree_marks_the_section_and_says_why(self, qapp):
        note = "Only the first 200 are shown — there are more."
        panel = AwsPanel()
        panel._on_overview(dict(SAMPLE, notes={"ec2": note}))
        rows = _rows(panel)
        assert rows["EC2 (2+)"] == ["web-1  (i-0abc)", "i-0def", note]
        assert "Lambda (1)" in rows
        ec2 = _section(panel, "ec2")
        panel._on_item_clicked(ec2, 0)  # collapse: the "+" must survive
        assert ec2.text(0) == "▸  EC2 (2+)"


# ── Second round: S3 summary, selection debounce, AI context ─────────


class TestS3Details:
    def _run(self, ls=("2025-01-01 10:00:00   12 Bytes hello.txt", 0)):
        import json

        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            if argv[:2] == ["s3", "ls"]:
                return ls
            if argv[:2] == ["s3api", "get-public-access-block"]:
                cfg = dict.fromkeys(
                    ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy"), True
                )
                cfg["RestrictPublicBuckets"] = False
                return json.dumps({"PublicAccessBlockConfiguration": cfg}), 0
            if argv[:2] == ["s3api", "get-bucket-encryption"]:
                rule = {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                return json.dumps({"ServerSideEncryptionConfiguration": {"Rules": [rule]}}), 0
            if argv[:2] == ["s3api", "get-bucket-versioning"]:
                return '{"Status": "Enabled"}', 0
            return "{}", 0

        return run, calls

    def _open_bucket(self, panel, region="ap-southeast-2"):
        bucket = dict(SAMPLE["s3"][0], region=region, status=region)
        panel._on_overview(dict(SAMPLE, s3=[bucket]))
        panel._tree.setCurrentItem(_section(panel, "s3").child(0))

    def test_summary_sits_above_the_listing(self, qapp, monkeypatch):
        run, calls = self._run()
        monkeypatch.setattr(aws_cli, "run", run)
        panel = AwsPanel()
        panel._region = "us-east-1"  # the bucket lives elsewhere
        self._open_bucket(panel)
        assert _pump_until(lambda: "hello.txt" in panel._details.toPlainText())
        text = panel._details.toPlainText()
        assert "Region" in text and "ap-southeast-2" in text
        assert "NOT fully blocked — off: RestrictPublicBuckets" in text
        assert "SSE-S3 (AES256)" in text
        assert "Versioning" in text and "Enabled" in text
        assert text.index("Public access") < text.index("── Top-level objects ──")
        assert text.index("── Top-level objects ──") < text.index("hello.txt")
        # every call goes to the bucket's own region, not the selected one
        assert all(argv[-2:] == ["--region", "ap-southeast-2"] for argv in calls)
        assert not any(argv[:2] == ["s3api", "get-bucket-location"] for argv in calls)
        assert "region=ap-southeast-2" in panel._console_url(panel._details_row)

    def test_facts_show_even_when_the_listing_is_refused(self, qapp, monkeypatch):
        run, _ = self._run(ls=("Error: Access denied — not allowed to call ListObjectsV2.", 254))
        monkeypatch.setattr(aws_cli, "run", run)
        panel = AwsPanel()
        self._open_bucket(panel)
        assert _pump_until(lambda: "Access denied" in panel._details.toPlainText())
        text = panel._details.toPlainText()
        assert "SSE-S3 (AES256)" in text and "── Top-level objects ──" in text


def test_arrowing_through_rows_describes_only_where_it_stops(qapp, monkeypatch):
    """Each row passed used to start its own CLI process."""
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        return _describe_by_argv(argv, **kw)

    monkeypatch.setattr(aws_cli, "run", run)
    panel = AwsPanel()
    panel._on_overview(SAMPLE)
    ec2 = _section(panel, "ec2")
    panel._tree.setCurrentItem(ec2.child(0))
    panel._tree.setCurrentItem(ec2.child(1))
    panel._tree.setCurrentItem(_section(panel, "lambda").child(0))
    assert _pump_until(lambda: "python3.12" in panel._details.toPlainText())
    _pump_until(lambda: False, timeout_s=0.3)
    assert calls == [["lambda", "get-function-configuration", "--function-name", "api-handler"]]


def test_lambda_log_tail_uses_the_functions_own_log_group(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(aws_cli, "run", lambda argv, **kw: calls.append(list(argv)) or ("", 0))
    panel = AwsPanel()
    panel._tail_lambda_logs({"name": "api-handler", "log_group": "/custom/api"})
    panel._tail_lambda_logs({"name": "old-fn"})
    assert _pump_until(lambda: len(calls) == 2)
    assert sorted(argv[2] for argv in calls) == ["/aws/lambda/old-fn", "/custom/api"]


def test_panel_publishes_its_selection_for_the_ai_tools(qapp, monkeypatch):
    import polyglot_ai.ui.panels.aws_panel as mod

    monkeypatch.setattr(mod, "fetch_overview", lambda p, r: dict(SAMPLE))
    monkeypatch.setattr(aws_cli, "aws_available", lambda: True)
    panel = AwsPanel()
    assert panel_state.get_aws_context() is None  # nothing until the panel is used
    panel._profile, panel._region = "work", "us-east-1"
    panel._on_region_changed("eu-west-1")
    assert panel_state.get_aws_context() == ("work", "eu-west-1")
