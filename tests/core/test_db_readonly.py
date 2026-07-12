"""Tests for SQL read-only detection.

The auto-approve gate for ``db_query`` runs every statement through
``is_readonly_query`` first. False negatives (a write that looks
read-only) are catastrophic; false positives (a SELECT marked as a
write) only cost an extra approval click. These tests pin both
directions so neither slips during refactors.
"""

from polyglot_ai.core.ai.tools.db_tools import is_readonly_query


class TestIsReadOnlyQuery:
    # ── True read-only ─────────────────────────────────────────────

    def test_simple_select_is_readonly(self):
        assert is_readonly_query("SELECT * FROM users") is True

    def test_select_with_trailing_semicolon_is_readonly(self):
        assert is_readonly_query("SELECT 1;") is True

    def test_pure_cte_select_is_readonly(self):
        sql = "WITH u AS (SELECT id FROM users) SELECT * FROM u"
        assert is_readonly_query(sql) is True

    def test_keyword_inside_string_literal_does_not_trigger(self):
        # The historical concern: a write keyword embedded in a
        # string literal must not be flagged as a write.
        for sql in (
            "SELECT 'DROP TABLE foo' AS msg",
            "SELECT '\"; DROP TABLE users--' AS x",
            'SELECT "DROP" FROM t',
            "SELECT 'a''DELETE FROM b' AS y",
        ):
            assert is_readonly_query(sql) is True, sql

    def test_keyword_inside_block_comment_does_not_trigger(self):
        assert is_readonly_query("SELECT 1 /* DELETE FROM users */") is True

    def test_keyword_inside_line_comment_does_not_trigger(self):
        assert is_readonly_query("SELECT 1 -- DROP TABLE x") is True

    # ── True writes ────────────────────────────────────────────────

    def test_insert_is_write(self):
        assert is_readonly_query("INSERT INTO users (id) VALUES (1)") is False

    def test_update_is_write(self):
        assert is_readonly_query("UPDATE users SET name = 'x'") is False

    def test_delete_is_write(self):
        assert is_readonly_query("DELETE FROM users") is False

    def test_data_modifying_cte_is_write(self):
        # Postgres allows writes inside CTEs — must not be auto-approved.
        sql = "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x"
        assert is_readonly_query(sql) is False

    def test_stacked_statements_are_rejected(self):
        # Multi-statement injection — even if both halves look safe.
        sql = "SELECT 1; SELECT 2"
        assert is_readonly_query(sql) is False

    def test_empty_query_is_not_readonly(self):
        assert is_readonly_query("") is False
        assert is_readonly_query("   ") is False


class TestPragma:
    def test_pragma_query_form_is_readonly(self):
        assert is_readonly_query("PRAGMA foreign_keys") is True
        assert is_readonly_query("PRAGMA database_list") is True

    def test_pragma_function_form_conservatively_needs_approval(self):
        # ``PRAGMA table_info(users)`` is genuinely read-only, but the
        # conservative '(' rule marks it non-readonly. That only costs an
        # extra approval click — the safe direction to err in.
        assert is_readonly_query("PRAGMA table_info(users)") is False

    def test_pragma_assignment_is_write(self):
        assert is_readonly_query("PRAGMA journal_mode=WAL") is False
        assert is_readonly_query("PRAGMA foreign_keys=OFF") is False
        assert is_readonly_query("PRAGMA writable_schema=ON") is False

    def test_pragma_assignment_with_spaces_is_write(self):
        assert is_readonly_query("PRAGMA journal_mode = DELETE") is False
