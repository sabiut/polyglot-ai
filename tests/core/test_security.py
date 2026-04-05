"""Tests for security — secret detection, error sanitization, file permissions, MCP validation."""

from pathlib import Path


from polyglot_ai.core.security import (
    check_no_symlinks_in_path,
    check_secure_file,
    is_secret_file,
    sanitize_error,
    scan_content_for_secrets,
    validate_mcp_command,
)


# ── is_secret_file ──────────────────────────────────────────────────


class TestIsSecretFile:
    def test_env_file(self):
        assert is_secret_file(Path(".env")) is True

    def test_env_local(self):
        assert is_secret_file(Path(".env.local")) is True

    def test_pem_file(self):
        assert is_secret_file(Path("server.pem")) is True

    def test_key_file(self):
        assert is_secret_file(Path("private.key")) is True

    def test_id_rsa(self):
        assert is_secret_file(Path("id_rsa")) is True

    def test_id_ed25519(self):
        assert is_secret_file(Path("id_ed25519")) is True

    def test_npmrc(self):
        assert is_secret_file(Path(".npmrc")) is True

    def test_pypirc(self):
        assert is_secret_file(Path(".pypirc")) is True

    def test_tfvars_extension(self):
        assert is_secret_file(Path("prod.tfvars")) is True

    def test_credentials_extension(self):
        assert is_secret_file(Path("app.credentials")) is True

    def test_secret_prefix(self):
        assert is_secret_file(Path("secret_config.yml")) is True

    def test_normal_python_file(self):
        assert is_secret_file(Path("app.py")) is False

    def test_normal_json_file(self):
        assert is_secret_file(Path("package.json")) is False

    def test_readme(self):
        assert is_secret_file(Path("README.md")) is False


# ── scan_content_for_secrets ────────────────────────────────────────


class TestScanContentForSecrets:
    def test_openai_key(self):
        content = 'api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"'
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_github_pat(self):
        content = "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_github_oauth(self):
        content = "token=gho_abcdefghijklmnopqrstuvwxyz1234567890AB"
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_gitlab_pat(self):
        content = "GITLAB_TOKEN=glpat-abcdefghij1234567890"
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_aws_access_key(self):
        content = "aws_key=AKIAIOSFODNN7EXAMPLE"
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_private_key_header(self):
        content = "-----BEGIN PRIVATE KEY-----\nMIIE..."
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_rsa_private_key(self):
        content = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_password_assignment(self):
        content = 'password = "supersecretpassword123"'
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_xai_key(self):
        content = "XAI_KEY=xai-abcdefghijklmnopqrstuvwx"
        findings = scan_content_for_secrets(content)
        assert len(findings) > 0

    def test_clean_content(self):
        content = "def hello():\n    print('hello world')\n"
        findings = scan_content_for_secrets(content)
        assert len(findings) == 0

    def test_max_scan_limit(self):
        content = "x" * 100_000 + 'password = "secret12345678"'
        findings = scan_content_for_secrets(content, max_scan=50_000)
        assert len(findings) == 0  # Secret is beyond scan limit


# ── sanitize_error ──────────────────────────────────────────────────


class TestSanitizeError:
    def test_bearer_token_redacted(self):
        msg = "Authorization failed: Bearer sk-abc123def456ghi789"
        result = sanitize_error(msg)
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result

    def test_sk_key_redacted(self):
        msg = "Error with key sk-abcdefghijklmnopqrstuvwx"
        result = sanitize_error(msg)
        assert "[REDACTED]" in result

    def test_ghp_token_redacted(self):
        msg = "Token ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
        result = sanitize_error(msg)
        assert "abcdefghij" not in result

    def test_truncation(self):
        msg = "x" * 500
        result = sanitize_error(msg, max_length=100)
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")

    def test_clean_message_unchanged(self):
        msg = "File not found: test.py"
        result = sanitize_error(msg)
        assert result == msg


# ── check_secure_file ───────────────────────────────────────────────


class TestCheckSecureFile:
    def test_nonexistent_file(self, tmp_path):
        secure, reason = check_secure_file(tmp_path / "missing.txt")
        assert secure is False
        assert "does not exist" in reason

    def test_secure_file(self, tmp_path):
        f = tmp_path / "secret.key"
        f.write_text("key data")
        f.chmod(0o600)
        secure, reason = check_secure_file(f)
        assert secure is True

    def test_insecure_permissions(self, tmp_path):
        f = tmp_path / "wide_open.key"
        f.write_text("key data")
        f.chmod(0o644)
        secure, reason = check_secure_file(f)
        assert secure is False
        assert "insecure permissions" in reason

    def test_group_readable_rejected(self, tmp_path):
        f = tmp_path / "group.key"
        f.write_text("key data")
        f.chmod(0o640)
        secure, reason = check_secure_file(f)
        assert secure is False

    def test_symlink_rejected(self, tmp_path):
        target = tmp_path / "real.key"
        target.write_text("key data")
        link = tmp_path / "link.key"
        link.symlink_to(target)
        secure, reason = check_secure_file(link)
        assert secure is False
        assert "symlink" in reason.lower()


# ── check_no_symlinks_in_path ───────────────────────────────────────


class TestCheckNoSymlinksInPath:
    def test_normal_path(self, tmp_path):
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        f = subdir / "file.txt"
        f.write_text("ok")
        safe, reason = check_no_symlinks_in_path(f, tmp_path)
        assert safe is True

    def test_symlink_in_path(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "file.txt").write_text("data")
        link = tmp_path / "link"
        link.symlink_to(real_dir)
        safe, reason = check_no_symlinks_in_path(link / "file.txt", tmp_path)
        assert safe is False
        assert "Symlink" in reason

    def test_outside_root(self, tmp_path):
        safe, reason = check_no_symlinks_in_path(Path("/etc/passwd"), tmp_path)
        assert safe is False


# ── validate_mcp_command ────────────────────────────────────────────


class TestValidateMcpCommand:
    def test_allowed_command_npx(self):
        allowed, _ = validate_mcp_command("npx", ["-y", "@mcp/server@1.0"])
        assert allowed is True

    def test_allowed_command_python(self):
        allowed, _ = validate_mcp_command("python3", ["-m", "mcp_server"])
        assert allowed is True

    def test_blocked_command_curl(self):
        allowed, _ = validate_mcp_command("curl", ["https://example.com"])
        assert allowed is False

    def test_blocked_command_bash(self):
        allowed, _ = validate_mcp_command("bash", ["-c", "echo hi"])
        assert allowed is False

    def test_shell_injection_in_args(self):
        allowed, reason = validate_mcp_command("npx", ["-y", "pkg; rm -rf /"])
        assert allowed is False
        assert "shell operator" in reason.lower()

    def test_pipe_in_args(self):
        allowed, _ = validate_mcp_command("npx", ["-y", "pkg | cat"])
        assert allowed is False

    def test_exec_flag_blocked(self):
        allowed, _ = validate_mcp_command("npx", ["--exec", "malicious"])
        assert allowed is False

    def test_e_flag_blocked(self):
        allowed, _ = validate_mcp_command("node", ["-e", "process.exit(1)"])
        assert allowed is False
