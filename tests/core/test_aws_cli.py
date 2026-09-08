"""AWS CLI plumbing: classification, profile discovery, error hints, tool policy."""

from __future__ import annotations

import pytest

from polyglot_ai.core import aws_cli
from polyglot_ai.core.ai.tools.aws_tools import is_read_only


class TestClassify:
    @pytest.mark.parametrize(
        "command",
        [
            "ec2 describe-instances",
            "lambda list-functions --max-items 10",
            "s3api get-bucket-location --bucket x",
            "s3 ls s3://bucket/",
            "logs tail /aws/lambda/fn --since 1h",
            "sts get-caller-identity",
            "aws ec2 describe-instances",  # leading 'aws' tolerated
            "iam simulate-principal-policy --policy-source-arn x",
            "cloudformation validate-template --template-body x",
        ],
    )
    def test_read_only(self, command):
        assert aws_cli.classify(command) == "read"

    @pytest.mark.parametrize(
        "command",
        [
            "ec2 run-instances --image-id ami-1",
            "lambda invoke --function-name fn out.json",
            "s3 cp a.txt s3://bucket/",
            "ecs update-service --cluster c --service s --force-new-deployment",
            "lambda update-function-code --function-name fn",
        ],
    )
    def test_mutating(self, command):
        assert aws_cli.classify(command) == "mutate"

    @pytest.mark.parametrize(
        "command",
        [
            "ec2 terminate-instances --instance-ids i-1",
            "s3 rm s3://bucket/key",
            "s3 rb s3://bucket --force",
            "lambda delete-function --function-name fn",
            "s3api put-bucket-policy --bucket b --policy file://p.json",
            "ec2 stop-instances --instance-ids i-1",
            "cloudformation delete-stack --stack-name s",
        ],
    )
    def test_destructive(self, command):
        assert aws_cli.classify(command) == "destructive"

    def test_unknown_or_empty_is_treated_as_mutation(self):
        assert aws_cli.classify("") == "mutate"
        assert aws_cli.classify("ec2") == "mutate"
        assert aws_cli.classify("frobnicate the-thing") == "mutate"
        assert aws_cli.classify("ec2 'unbalanced") == "mutate"  # shlex failure fallback


class TestProfiles:
    def test_profiles_come_from_config_and_credentials(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config"
        cfg.write_text("[default]\nregion = eu-west-1\n\n[profile work]\nregion = us-west-2\n")
        creds = tmp_path / "credentials"
        creds.write_text("[work]\naws_access_key_id = x\n\n[personal]\naws_access_key_id = y\n")
        monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

        assert aws_cli.list_profiles() == ["default", "work", "personal"]
        assert aws_cli.profile_region("default") == "eu-west-1"
        assert aws_cli.profile_region("work") == "us-west-2"
        assert aws_cli.profile_region("personal") == ""

    def test_missing_files_give_empty_results(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "nope"))
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "nope2"))
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        assert aws_cli.list_profiles() == []
        assert aws_cli.profile_region("default") == ""

    def test_env_region_wins(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
        assert aws_cli.profile_region("anything") == "ap-southeast-2"


class TestExplainError:
    def test_common_failures_become_actionable_hints(self):
        assert "aws configure" in aws_cli.explain_error("Unable to locate credentials.")
        assert "expired" in aws_cli.explain_error("An error occurred (ExpiredToken) ...").lower()
        assert "region" in aws_cli.explain_error("You must specify a region.").lower()
        assert "denied" in aws_cli.explain_error("(AccessDenied) when calling ...").lower()

    def test_unknown_error_returns_first_line(self):
        assert aws_cli.explain_error("weird thing\nsecond line") == "weird thing"


class TestToolPolicy:
    @pytest.fixture
    def registry(self, tmp_path):
        from polyglot_ai.core.ai.tools import ToolRegistry
        from polyglot_ai.core.bridge import EventBus
        from polyglot_ai.core.file_ops import FileOperations
        from polyglot_ai.core.sandbox import Sandbox

        project = tmp_path / "project"
        project.mkdir()
        file_ops = FileOperations(EventBus())
        file_ops.set_project_root(project)
        return ToolRegistry(Sandbox(project), file_ops)

    def test_read_only_aws_cli_is_auto_approved(self, registry):
        args = {"command": "lambda list-functions"}
        assert registry.needs_approval("aws_cli", args) is False
        assert registry.is_auto_approved("aws_cli", args) is True

    def test_mutating_aws_cli_needs_approval(self, registry):
        args = {"command": "lambda delete-function --function-name fn"}
        assert registry.needs_approval("aws_cli", args) is True
        assert registry.is_auto_approved("aws_cli", args) is False

    def test_aws_cli_without_args_needs_approval(self, registry):
        assert registry.needs_approval("aws_cli") is True

    def test_logs_tail_is_auto_approved(self, registry):
        assert registry.is_auto_approved("aws_logs_tail") is True

    def test_is_read_only_helper(self):
        assert is_read_only({"command": "s3 ls"}) is True
        assert is_read_only({"command": "s3 rm s3://b/k"}) is False
        assert is_read_only(None) is False

    def test_tool_definitions_registered(self):
        from polyglot_ai.core.ai.tools.definitions import TOOL_DEFINITIONS

        names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
        assert {"aws_cli", "aws_logs_tail"} <= names
