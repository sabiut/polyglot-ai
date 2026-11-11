"""The AWS resource table: listings, status columns and details facts.

No AWS calls — ``call`` / ``run`` are stubs.
"""

from __future__ import annotations

import json

import pytest

from polyglot_ai.core import aws_cli, aws_resources
from polyglot_ai.core.aws_resources import BY_KEY


class TestS3:
    def test_listing_asks_for_and_shows_each_buckets_region(self):
        seen = {}

        def call(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return {
                "Buckets": [
                    {
                        "Name": "logs",
                        "CreationDate": "2026-11-11T01:02:03",
                        "BucketRegion": "eu-west-1",
                    },
                    {"Name": "old", "CreationDate": "2020-01-01T00:00:00"},
                ]
            }

        rows = aws_resources._fetch_s3(call)
        # S3 only reports BucketRegion when the request carries a parameter.
        assert seen["argv"] == ["s3api", "list-buckets", "--page-size", "1000"]
        assert seen["kw"] == {"global_call": True}
        assert [(r["name"], r["status"], r["info"]) for r in rows] == [
            ("logs", "eu-west-1", "2026-11-11"),
            ("old", "", "2020-01-01"),
        ]

    def test_describe_and_console_use_the_buckets_region(self):
        s3 = BY_KEY["s3"]
        row = {"name": "logs", "region": "eu-west-1"}
        assert s3.describe(row)[-2:] == ["--region", "eu-west-1"]
        assert s3.console_url(row, "us-east-1").endswith("/logs?region=eu-west-1")
        # unknown region: fall back to the selected one, add no --region
        assert "--region" not in s3.describe({"name": "old"})
        assert s3.console_url({"name": "old"}, "us-east-1").endswith("?region=us-east-1")

    @pytest.mark.parametrize(
        "cfg, expected",
        [
            ((True, True, True, True), "blocked (all four settings on)"),
            (
                (True, False, True, False),
                "NOT fully blocked — off: IgnorePublicAcls, RestrictPublicBuckets",
            ),
        ],
    )
    def test_public_access_block(self, cfg, expected):
        names = (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
        out = json.dumps({"PublicAccessBlockConfiguration": dict(zip(names, cfg))})
        assert aws_resources._s3_public_access(out, 0) == expected

    def test_states_that_arrive_as_errors(self):
        missing = aws_cli.explain_error(
            "\nAn error occurred (NoSuchPublicAccessBlockConfiguration) when calling the "
            "GetPublicAccessBlock operation: The public access block configuration was not found"
        )
        assert aws_resources._s3_public_access(f"Error: {missing}", 254).startswith(
            "no bucket-level block"
        )
        unencrypted = aws_cli.explain_error(
            "\nAn error occurred (ServerSideEncryptionConfigurationNotFoundError) when calling "
            "the GetBucketEncryption operation: The server side encryption configuration was not found"
        )
        assert aws_resources._s3_encryption(f"Error: {unencrypted}", 254) == "none configured"
        denied = "Error: Access denied — this profile lacks permission for s3:GetBucketVersioning."
        assert aws_resources._s3_versioning(denied, 254) == (
            "unknown — Access denied — this profile lacks permission for s3:GetBucketVersioning."
        )

    def test_encryption_and_versioning_values(self):
        def enc(default):
            rules = [{"ApplyServerSideEncryptionByDefault": default}]
            return json.dumps({"ServerSideEncryptionConfiguration": {"Rules": rules}})

        assert aws_resources._s3_encryption(enc({"SSEAlgorithm": "AES256"}), 0) == "SSE-S3 (AES256)"
        kms = {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": "arn:aws:kms:r:1:key/abc-123"}
        assert aws_resources._s3_encryption(enc(kms), 0) == "SSE-KMS (abc-123)"
        assert aws_resources._s3_encryption(enc({"SSEAlgorithm": "aws:kms"}), 0) == (
            "SSE-KMS (AWS managed key)"
        )
        assert aws_resources._s3_versioning("{}", 0) == "off"
        assert aws_resources._s3_versioning("", 0) == "off"
        assert aws_resources._s3_versioning('{"Status": "Suspended"}', 0) == "Suspended"
        assert aws_resources._s3_versioning('{"Status": "Enabled", "MFADelete": "Enabled"}', 0) == (
            "Enabled, MFA delete on"
        )

    def test_facts_look_the_region_up_only_when_the_listing_lacked_it(self):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[1] == "get-bucket-location":
                return '{"LocationConstraint": null}', 0
            return "not json", 0  # a surprise must not lose the other facts

        facts = dict(aws_resources._s3_facts({"name": "old", "created": "2020-01-01"}, run))
        assert facts["Region"] == "us-east-1"  # null is S3's spelling of us-east-1
        assert facts["Versioning"] == "unknown — unexpected output"
        assert facts["Bucket"] == "old" and facts["Created"] == "2020-01-01"
        assert all("--region" not in argv for argv in calls)

        calls.clear()
        aws_resources._s3_facts({"name": "logs", "region": "eu-west-1"}, run)
        assert not any(argv[1] == "get-bucket-location" for argv in calls)
        assert all(argv[-2:] == ["--region", "eu-west-1"] for argv in calls)


class TestEcs:
    def test_scaled_to_zero_is_not_a_warning(self):
        colour = BY_KEY["ecs"].status_colour
        assert colour({"running": 0, "desired": 0}) == "text_muted"
        assert colour({"running": 2, "desired": 2}) == "accent_success_muted"
        assert colour({"running": 1, "desired": 2}) == "accent_warning"

    def test_clusters_fetched_in_parallel_keep_their_order(self):
        import threading
        import time

        active, peak, lock = 0, 0, threading.Lock()

        def call(argv, **kw):
            nonlocal active, peak
            if argv[:2] == ["ecs", "list-clusters"]:
                return {"clusterArns": [f"arn:aws:ecs:r:1:cluster/c{i}" for i in range(6)]}
            cluster = argv[argv.index("--cluster") + 1]
            if argv[:2] == ["ecs", "list-services"]:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return {"serviceArns": [f"arn:svc/{cluster}-api"]}
            return {
                "services": [
                    {"serviceName": f"{cluster}-api", "runningCount": 1, "desiredCount": 1}
                ]
            }

        rows = aws_resources._fetch_ecs(call)
        assert [r["cluster"] for r in rows] == [f"c{i}" for i in range(6)]
        assert peak > 1  # they overlapped


def test_lambda_rows_carry_the_configured_log_group():
    def call(argv, **kw):
        return {
            "Functions": [
                {"FunctionName": "api", "LoggingConfig": {"LogGroup": "/custom/api"}},
                {"FunctionName": "old"},
            ]
        }

    rows = aws_resources._fetch_lambda(call)
    assert [r["log_group"] for r in rows] == ["/custom/api", "/aws/lambda/old"]


class TestFilledColumns:
    """EKS, DynamoDB, SQS and SNS list calls return names only."""

    def _call(self, listing, described):
        def call(argv, **kw):
            return listing if argv[1].startswith("list-") else described

        return call

    def test_eks(self):
        rows = aws_resources._fetch_eks(
            self._call(
                {"clusters": ["platform"]}, {"cluster": {"status": "ACTIVE", "version": "1.31"}}
            )
        )
        assert (rows[0]["status"], rows[0]["info"]) == ("ACTIVE", "Kubernetes 1.31")
        colour = BY_KEY["eks"].status_colour
        assert colour(rows[0]) == "accent_success_muted"
        assert colour({"status": "UPDATING"}) == "accent_warning"
        assert colour({"status": "FAILED"}) == "accent_error"
        assert colour({"status": ""}) is None

    def test_dynamodb(self):
        rows = aws_resources._fetch_dynamodb(
            self._call(
                {"TableNames": ["sessions"]},
                {"Table": {"TableStatus": "ACTIVE", "ItemCount": 12345}},
            )
        )
        assert (rows[0]["status"], rows[0]["info"]) == ("ACTIVE", "12,345 items")

    def test_sqs(self):
        attrs = {"ApproximateNumberOfMessages": "7", "ApproximateNumberOfMessagesNotVisible": "2"}
        rows = aws_resources._fetch_sqs(
            self._call(
                {"QueueUrls": ["https://sqs/1/jobs.fifo", "https://sqs/1/mail"]},
                {"Attributes": attrs},
            )
        )
        assert (rows[0]["status"], rows[0]["info"]) == ("7 waiting", "2 in flight  FIFO")
        assert (rows[1]["status"], rows[1]["info"]) == ("7 waiting", "2 in flight")

    def test_sns(self):
        rows = aws_resources._fetch_sns(
            self._call(
                {"Topics": [{"TopicArn": "arn:aws:sns:r:1:alerts"}]},
                {"Attributes": {"SubscriptionsConfirmed": "1"}},
            )
        )
        assert rows[0]["status"] == "1 subscriber"

    def test_only_the_first_few_rows_are_looked_up(self):
        described = []

        def call(argv, **kw):
            if argv[1] == "list-tables":
                return {"TableNames": [f"t{i:02}" for i in range(30)]}
            described.append(argv[-1])
            return {"Table": {"TableStatus": "ACTIVE"}}

        rows = aws_resources._fetch_dynamodb(call)
        assert len(rows) == 30 and len(described) == aws_resources._ENRICH_MAX
        assert rows[0]["status"] == "ACTIVE" and rows[-1]["status"] == ""

    def test_a_refused_lookup_leaves_the_row_blank_and_reports_nothing(self, monkeypatch):
        """The listing worked; a missing describe permission isn't an error under it."""
        from polyglot_ai.ui.panels.aws_panel import fetch_overview

        def run(argv, **kw):
            if argv[:2] == ["sts", "get-caller-identity"]:
                return '{"Account": "1", "Arn": "arn:aws:iam::1:user/x"}', 0
            if argv[:2] == ["dynamodb", "list-tables"]:
                return '{"TableNames": ["sessions"]}', 0
            if argv[:2] == ["dynamodb", "describe-table"]:
                return "Error: Access denied — this profile lacks permission for that call.", 254
            return "{}", 0

        monkeypatch.setattr(aws_cli, "run", run)
        data = fetch_overview("default", "us-east-1")
        assert [(r["name"], r["status"]) for r in data["dynamodb"]] == [("sessions", "")]
        assert "dynamodb" not in data["errors"]
