"""Secrets must not reach the AI provider through the side doors found in review.

1. Saving a file re-indexed it with none of the full build's filters, so
   ``.env`` entered the TF-IDF index and got pasted into prompts.
2. ``git_show_file`` (auto-approved) returned committed secret files.
3. Redaction missed common formats and stopped scanning after 50k chars.
4. Tool results were only redacted where each tool remembered to.
"""

from __future__ import annotations

import asyncio

import pytest

from polyglot_ai.core.ai.tools import ToolRegistry
from polyglot_ai.core.ai.tools.git_tools import git_show_file
from polyglot_ai.core.indexer import ProjectIndexer
from polyglot_ai.core.security import redact_secrets_in_content, scan_content_for_secrets

PLACEHOLDER = "[SECRET_REDACTED]"

# A fake AWS temporary key, assembled at runtime. Written out as one
# literal it has exactly the shape of a real one, and GitHub secret
# scanning raised an alert for it (a false positive, but noisy).
FAKE_AWS_SESSION_KEY = "ASIA" + "ABCDEFGHIJKLMNOP"


class TestRedactionPatterns:
    @pytest.mark.parametrize(
        "text, secret",
        [
            ("STRIPE=sk_live_" + "a" * 24, "sk_live_" + "a" * 24),
            (f"key {FAKE_AWS_SESSION_KEY} here", FAKE_AWS_SESSION_KEY),
            ("DATABASE_URL=postgres://admin:S3cretPass@db:5432/app", "S3cretPass"),
            ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtn"),
            ("DB_PASSWORD=hunter2hunter2", "hunter2hunter2"),
            ("export API_TOKEN=abcdef123456", "abcdef123456"),
            ("SECRET_KEY = 'django-insecure-abcdef'", "django-insecure-abcdef"),
            ("github_pat_" + "A" * 50, "github_pat_" + "A" * 50),
            ("-----BEGIN OPENSSH PRIVATE KEY-----", "BEGIN OPENSSH PRIVATE KEY"),
            ('"Env": ["PATH=/usr/bin", "DB_PASSWORD=hunter2hunter2"]', "hunter2hunter2"),
            ("running with DB_PASSWORD=hunter2hunter2 now", "hunter2hunter2"),
        ],
    )
    def test_common_formats_are_redacted(self, text, secret):
        out = redact_secrets_in_content(text)
        assert secret not in out
        assert PLACEHOLDER in out
        assert scan_content_for_secrets(text)

    def test_key_names_survive_so_the_model_knows_what_was_there(self):
        out = redact_secrets_in_content("DB_PASSWORD=hunter2hunter2\nDEBUG=true\n")
        assert out == "DB_PASSWORD=[SECRET_REDACTED]\nDEBUG=true\n"
        url = redact_secrets_in_content("postgres://admin:S3cretPass@db:5432/app")
        assert url == "postgres://[SECRET_REDACTED]@db:5432/app"

    def test_ordinary_source_code_is_left_alone(self):
        code = "token = get_token()\npassword_field = form.password\nAPI_TOKEN_HEADER = h\n"
        assert redact_secrets_in_content(code) == code

    def test_secrets_past_50k_characters_are_redacted(self):
        text = "x" * 60_000 + "\nDB_PASSWORD=hunter2hunter2\n"
        assert "hunter2hunter2" not in redact_secrets_in_content(text)


class TestIndexerOnSave:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "app.py").write_text("def connect(): pass\n")
        return tmp_path

    def _indexed(self, project) -> ProjectIndexer:
        idx = ProjectIndexer()
        idx._project_root = project
        idx._build_sync(project)
        return idx

    def test_saving_env_does_not_index_it(self, project):
        idx = self._indexed(project)
        env = project / ".env"
        env.write_text("DATABASE_URL=postgres://admin:S3cretPass@prod/app\n")
        idx.update_file(env)
        assert ".env" not in idx._files
        assert all(rel != ".env" for rel, _ in idx.query("postgres prod database url"))

    def test_saving_huge_or_skipped_files_does_not_index_them(self, project):
        idx = self._indexed(project)
        (project / "node_modules").mkdir()
        nm = project / "node_modules" / "lib.js"
        nm.write_text("module.exports = 1\n")
        big = project / "big.py"
        big.write_text("x = 1\n" * 200_000)
        idx.update_file(nm)
        idx.update_file(big)
        assert "node_modules/lib.js" not in idx._files
        assert "big.py" not in idx._files

    def test_saving_normal_code_still_indexes_it(self, project):
        idx = self._indexed(project)
        f = project / "util.py"
        f.write_text("def frobnicate_widgets(): pass\n")
        idx.update_file(f)
        assert "util.py" in idx._files

    def test_file_that_becomes_unindexable_is_dropped(self, project):
        idx = self._indexed(project)
        f = project / "app.py"
        f.write_text("x = 1\n" * 200_000)  # now over the size limit
        idx.update_file(f)
        assert "app.py" not in idx._files


class _NoRunSandbox:
    def validate_command(self, command):
        raise AssertionError("git must not run for a secret path")


@pytest.mark.parametrize("path", [".env", "config/.env.production", "id_rsa", "certs/server.pem"])
def test_git_show_file_refuses_secret_files(path):
    result = asyncio.run(git_show_file(_NoRunSandbox(), {"path": path, "ref": "HEAD~3"}))
    assert result.startswith("Error") and "secrets" in result


def test_every_registry_result_is_redacted(monkeypatch):
    registry = ToolRegistry()

    async def leaky(tool_name, arguments):
        return f"aws output: {FAKE_AWS_SESSION_KEY} and DB_PASSWORD=hunter2hunter2"

    monkeypatch.setattr(registry, "_execute_unredacted", leaky)
    out = asyncio.run(registry.execute("aws_cli", "{}"))
    assert FAKE_AWS_SESSION_KEY not in out and "hunter2hunter2" not in out
