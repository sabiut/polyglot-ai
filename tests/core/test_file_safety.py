"""Tests for file_safety — blocked files, sensitive paths, syntax validation."""

from polyglot_ai.core.file_safety import (
    check_blocked_file,
    is_sensitive_path,
    validate_python_syntax,
)


# ── check_blocked_file ──────────────────────────────────────────────


class TestCheckBlockedFile:
    def test_blocked_filename_bashrc(self):
        assert check_blocked_file(".bashrc") is not None

    def test_blocked_filename_id_rsa(self):
        assert check_blocked_file("id_rsa") is not None

    def test_blocked_filename_shadow(self):
        assert check_blocked_file("shadow") is not None

    def test_blocked_filename_credentials_json(self):
        assert check_blocked_file("credentials.json") is not None

    def test_blocked_filename_env_local(self):
        assert check_blocked_file(".env.local") is not None

    def test_blocked_extension_env(self):
        assert check_blocked_file("config.env") is not None

    def test_blocked_extension_pem(self):
        assert check_blocked_file("server.pem") is not None

    def test_blocked_extension_key(self):
        assert check_blocked_file("private.key") is not None

    def test_blocked_extension_sudoers(self):
        assert check_blocked_file("custom.sudoers") is not None

    def test_blocked_hidden_dir(self):
        assert check_blocked_file(".secrets/data.txt") is not None

    def test_allowed_hidden_dir_github(self):
        assert check_blocked_file(".github/README.md") is None

    def test_allowed_hidden_dir_vscode(self):
        assert check_blocked_file(".vscode/settings.json") is None

    def test_allowed_hidden_dir_claude(self):
        assert check_blocked_file(".claude/config.json") is None

    def test_normal_file_allowed(self):
        assert check_blocked_file("src/main.py") is None

    def test_normal_nested_file_allowed(self):
        assert check_blocked_file("lib/utils/helpers.js") is None

    def test_normal_readme_allowed(self):
        assert check_blocked_file("README.md") is None


# ── is_sensitive_path ───────────────────────────────────────────────


class TestIsSensitivePath:
    def test_github_workflows(self):
        assert is_sensitive_path(".github/workflows/ci.yml") is True

    def test_github_actions(self):
        assert is_sensitive_path(".github/actions/custom/action.yml") is True

    def test_husky_hook(self):
        assert is_sensitive_path(".husky/pre-commit") is True

    def test_circleci(self):
        assert is_sensitive_path(".circleci/config.yml") is True

    def test_gitlab_ci(self):
        assert is_sensitive_path(".gitlab/ci/deploy.yml") is True

    def test_git_hooks(self):
        assert is_sensitive_path(".git/hooks/pre-commit") is True

    def test_normal_github_file_not_sensitive(self):
        assert is_sensitive_path(".github/README.md") is False

    def test_normal_file_not_sensitive(self):
        assert is_sensitive_path("src/app.py") is False

    def test_backslash_normalization(self):
        assert is_sensitive_path(".github\\workflows\\ci.yml") is True


# ── validate_python_syntax ──────────────────────────────────────────


class TestValidatePythonSyntax:
    def test_valid_python(self):
        assert validate_python_syntax("print('hello')", "test.py") is None

    def test_invalid_python(self):
        result = validate_python_syntax("def foo(", "test.py")
        assert result is not None
        assert "Syntax error" in result

    def test_non_python_file_skipped(self):
        assert validate_python_syntax("not valid python {{{", "test.js") is None

    def test_empty_python_valid(self):
        assert validate_python_syntax("", "test.py") is None

    def test_multiline_valid(self):
        code = "def add(a, b):\n    return a + b\n"
        assert validate_python_syntax(code, "math.py") is None
