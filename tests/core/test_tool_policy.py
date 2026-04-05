"""Tests for tool approval policy and JSON splitting."""

import pytest

from polyglot_ai.core.ai.tools import ToolRegistry
from polyglot_ai.core.ai.tools.definitions import (
    AUTO_APPROVE,
    REQUIRES_APPROVAL,
    TOOL_DEFINITIONS,
)


# ── Approval policy ─────────────────────────────────────────────────


class TestApprovalPolicy:
    @pytest.fixture
    def registry(self, tmp_path):
        from polyglot_ai.core.sandbox import Sandbox
        from polyglot_ai.core.file_ops import FileOperations
        from polyglot_ai.core.bridge import EventBus

        project = tmp_path / "project"
        project.mkdir()
        sandbox = Sandbox(project)
        file_ops = FileOperations(EventBus())
        file_ops.set_project_root(project)
        return ToolRegistry(sandbox, file_ops)

    def test_file_read_auto_approved(self, registry):
        assert registry.is_auto_approved("file_read") is True
        assert registry.needs_approval("file_read") is False

    def test_file_search_auto_approved(self, registry):
        assert registry.is_auto_approved("file_search") is True

    def test_list_directory_auto_approved(self, registry):
        assert registry.is_auto_approved("list_directory") is True

    def test_git_status_auto_approved(self, registry):
        assert registry.is_auto_approved("git_status") is True

    def test_git_diff_auto_approved(self, registry):
        assert registry.is_auto_approved("git_diff") is True

    def test_web_search_auto_approved(self, registry):
        assert registry.is_auto_approved("web_search") is True

    def test_file_write_requires_approval(self, registry):
        assert registry.needs_approval("file_write") is True
        assert registry.is_auto_approved("file_write") is False

    def test_file_patch_requires_approval(self, registry):
        assert registry.needs_approval("file_patch") is True

    def test_shell_exec_requires_approval(self, registry):
        assert registry.needs_approval("shell_exec") is True

    def test_git_commit_requires_approval(self, registry):
        assert registry.needs_approval("git_commit") is True

    def test_unknown_tool_requires_approval(self, registry):
        """Fail-safe: unknown tools should require approval."""
        assert registry.needs_approval("totally_unknown_tool") is True

    def test_mcp_tool_requires_approval(self, registry):
        """MCP tools should require approval."""
        assert registry.needs_approval("mcp_github_create_issue") is True


# ── Policy consistency ──────────────────────────────────────────────


class TestPolicyConsistency:
    def test_no_overlap(self):
        overlap = AUTO_APPROVE & REQUIRES_APPROVAL
        assert overlap == set(), f"Tools in both sets: {overlap}"

    def test_all_defined_tools_have_policy(self):
        defined_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        policy_names = AUTO_APPROVE | REQUIRES_APPROVAL
        missing = defined_names - policy_names
        assert missing == set(), f"Tools without policy: {missing}"

    def test_no_phantom_policies(self):
        defined_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        policy_names = AUTO_APPROVE | REQUIRES_APPROVAL
        extra = policy_names - defined_names
        assert extra == set(), f"Policy for undefined tools: {extra}"


# ── _split_concat_json ──────────────────────────────────────────────


class TestSplitConcatJson:
    def test_two_objects(self):
        result = ToolRegistry._split_concat_json('{"a": 1}{"b": 2}')
        assert result == [{"a": 1}, {"b": 2}]

    def test_three_objects_with_whitespace(self):
        result = ToolRegistry._split_concat_json('{"a": 1} {"b": 2} {"c": 3}')
        assert result == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_single_object_returns_none(self):
        result = ToolRegistry._split_concat_json('{"a": 1}')
        assert result is None

    def test_invalid_json_returns_none(self):
        result = ToolRegistry._split_concat_json("not json at all")
        assert result is None

    def test_empty_string_returns_none(self):
        result = ToolRegistry._split_concat_json("")
        assert result is None

    def test_non_dict_object_skipped(self):
        result = ToolRegistry._split_concat_json("[1, 2, 3]")
        assert result is None
