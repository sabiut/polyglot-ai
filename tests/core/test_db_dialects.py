"""Engine facts, connection URLs and SQL dialect differences."""

from __future__ import annotations

import ssl

import pytest

from polyglot_ai.core.db_dialects import (
    ENGINE_CHOICES,
    ENGINES,
    build_url,
    db_type_for_rds_engine,
    make_ssl_context,
    parse_url,
    preview_query,
    quote_identifier,
    quote_table,
)


class TestConnectionUrls:
    def test_password_with_url_characters_round_trips(self):
        """It used to be pasted in unescaped: this password made the host "ss"."""
        password = "p@ss/w:rd#1?x%41 é"
        url = build_url("mysql", "db.example.com", 3306, "cm admin", password, "my/app")
        target = parse_url(url, "mysql")
        assert (target.host, target.port) == ("db.example.com", 3306)
        assert (target.user, target.password, target.database) == ("cm admin", password, "my/app")
        assert password not in url  # nothing left unescaped

    @pytest.mark.parametrize("db_type", ["postgresql", "mysql", "mssql", "oracle"])
    def test_every_engine_gets_its_scheme_and_default_port(self, db_type):
        url = build_url(db_type, "h", ENGINES[db_type].default_port, "u", "p", "d")
        assert url.startswith(f"{db_type}://u:p@h:")
        assert parse_url(f"{db_type}://u@h/d", db_type).port == ENGINES[db_type].default_port

    def test_no_user_and_no_password(self):
        assert build_url("postgresql", "h", 5432, "", "", "d") == "postgresql://h:5432/d"
        assert build_url("postgresql", "h", 5432, "u", "", "d") == "postgresql://u@h:5432/d"

    def test_ipv6_host(self):
        url = build_url("postgresql", "::1", 5432, "u", "p", "d")
        assert url == "postgresql://u:p@[::1]:5432/d"
        assert parse_url(url).host == "::1"

    def test_connections_saved_before_escaping_are_read_literally(self):
        legacy = "mysql://root:abc%41def@localhost:3306/app"
        assert parse_url(legacy, "mysql", encoded=False).password == "abc%41def"
        assert parse_url(legacy, "mysql", encoded=True).password == "abcAdef"


class TestRdsEngines:
    @pytest.mark.parametrize(
        "engine, expected",
        [
            ("postgres", "postgresql"),
            ("aurora-postgresql", "postgresql"),
            ("mysql", "mysql"),
            ("mariadb", "mysql"),
            ("aurora-mysql", "mysql"),
            ("sqlserver-ex", "mssql"),
            ("sqlserver-web", "mssql"),
            ("sqlserver-se", "mssql"),
            ("sqlserver-ee", "mssql"),
            ("custom-sqlserver-ee", "mssql"),
            ("oracle-ee", "oracle"),
            ("oracle-se2-cdb", "oracle"),
            ("custom-oracle-ee", "oracle"),
            ("db2-se", None),
            ("", None),
        ],
    )
    def test_mapping(self, engine, expected):
        assert db_type_for_rds_engine(engine) == expected

    def test_every_listed_type_is_a_known_engine(self):
        for _label, db_type in ENGINE_CHOICES:
            assert db_type == "sqlite" or db_type in ENGINES


class TestDialect:
    def test_quoting(self):
        assert quote_identifier("postgresql", 'we"ird') == '"we""ird"'
        assert quote_identifier("sqlite", "users") == '"users"'
        assert quote_identifier("oracle", "USERS") == '"USERS"'
        # In MySQL a double-quoted name is a string literal.
        assert quote_identifier("mysql", "or`der") == "`or``der`"
        assert quote_identifier("mssql", "we]ird") == "[we]]ird]"

    def test_schema_qualified_sql_server_tables(self):
        assert quote_table("mssql", "sales.Orders") == "[sales].[Orders]"
        assert quote_table("mssql", "Orders") == "[Orders]"
        assert quote_table("postgresql", "a.b") == '"a.b"'  # a dot is just a character there

    def test_first_rows(self):
        assert preview_query("postgresql", "users") == 'SELECT * FROM "users" LIMIT 100'
        assert preview_query("mysql", "users") == "SELECT * FROM `users` LIMIT 100"
        assert preview_query("mssql", "sales.Orders", 5) == "SELECT TOP 5 * FROM [sales].[Orders]"
        assert preview_query("oracle", "USERS") == 'SELECT * FROM "USERS" FETCH FIRST 100 ROWS ONLY'


class TestTls:
    def test_modes(self):
        assert make_ssl_context("disable") is None
        assert make_ssl_context("prefer") is None
        require = make_ssl_context("require")
        assert require.verify_mode == ssl.CERT_NONE and not require.check_hostname
        verify = make_ssl_context("verify")
        assert verify.verify_mode == ssl.CERT_REQUIRED and verify.check_hostname
        # Through a tunnel the address dialled is 127.0.0.1.
        tunnelled = make_ssl_context("verify", check_hostname=False)
        assert tunnelled.verify_mode == ssl.CERT_REQUIRED and not tunnelled.check_hostname

    def test_missing_ca_file_is_an_error_not_a_silent_downgrade(self, tmp_path):
        with pytest.raises((OSError, ssl.SSLError)):
            make_ssl_context("verify", str(tmp_path / "nope.pem"))


def test_only_engines_that_can_verify_offer_it():
    from polyglot_ai.core.db_dialects import ENGINES, SSL_MODES, ssl_modes_for

    for db_type in ("postgresql", "mysql", "oracle", "sqlite"):
        assert ssl_modes_for(db_type) == SSL_MODES
    sql_server = [mode for _label, mode in ssl_modes_for("mssql")]
    assert sql_server == ["prefer", "require", "disable"]  # pymssql can't check a certificate
    assert ENGINES["mssql"].verifies_certificate is False
