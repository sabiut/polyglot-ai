"""SQL Server and Oracle drivers, and the connection layer that runs them.

No database: ``pymssql`` / ``oracledb`` are replaced by recording fakes.
"""

from __future__ import annotations

import sys
import types

import pytest

from polyglot_ai.core import db_drivers, db_explorer
from polyglot_ai.core.db_dialects import DbTarget
from polyglot_ai.core.db_drivers import DriverMissing, MssqlDriver, OracleDriver, describe_error
from polyglot_ai.core.db_explorer import DatabaseConnection


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql):
        self._conn.executed.append(sql)
        result = self._conn.results.get(sql.strip().split()[0].upper(), None)
        if callable(result):
            result = result(sql)
        if result is None:
            self.description, self._rows, self.rowcount = None, [], 3
        else:
            columns, rows = result
            self.description = [(c,) for c in columns]
            self._rows = list(rows)

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n):
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.executed: list[str] = []
        self.results: dict = {"SELECT": (["1"], [(1,)])}
        self.closed = False
        self.autocommit = False

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pymssql(monkeypatch):
    module = types.ModuleType("pymssql")
    module.connections = []

    def connect(**kwargs):
        conn = _Conn(kwargs)
        module.connections.append(conn)
        return conn

    module.connect = connect
    monkeypatch.setitem(sys.modules, "pymssql", module)
    return module


@pytest.fixture
def fake_oracledb(monkeypatch):
    module = types.ModuleType("oracledb")
    module.connections = []
    module.defaults = types.SimpleNamespace(fetch_lobs=True)

    def connect(**kwargs):
        conn = _Conn(kwargs)
        module.connections.append(conn)
        return conn

    module.connect = connect
    monkeypatch.setitem(sys.modules, "oracledb", module)
    return module


TARGET = DbTarget(host="db.example.com", port=1433, user="cmadmin", password="p@ss", database="")


class TestMssqlDriver:
    def test_connects_the_way_rds_sql_server_expects(self, fake_pymssql):
        driver = MssqlDriver(TARGET, ssl_mode="require")
        assert driver.connect() == "Connected"
        kwargs = fake_pymssql.connections[0].kwargs
        assert kwargs["server"] == "db.example.com" and kwargs["port"] == "1433"
        assert (kwargs["user"], kwargs["password"]) == ("cmadmin", "p@ss")
        assert kwargs["database"] == "master"  # RDS SQL Server reports no database name
        assert kwargs["encryption"] == "require" and kwargs["login_timeout"] == 10

    @pytest.mark.parametrize(
        "mode, expected",
        [("disable", "off"), ("prefer", "request"), ("require", "require")],
    )
    def test_encryption_modes(self, fake_pymssql, mode, expected):
        MssqlDriver(TARGET, ssl_mode=mode).connect()
        assert fake_pymssql.connections[0].kwargs["encryption"] == expected

    def test_verify_is_refused_not_quietly_downgraded(self, fake_pymssql):
        """pymssql can't check a certificate. Mapping "verify" to plain
        "require" told the user the server had been verified."""
        from polyglot_ai.core.db_drivers import UnsupportedOption

        with pytest.raises(UnsupportedOption, match="can't verify"):
            MssqlDriver(TARGET, ssl_mode="verify").connect()
        assert fake_pymssql.connections == []  # nothing was dialled

    def test_schema_qualifies_tables_outside_dbo(self, fake_pymssql):
        driver = MssqlDriver(TARGET)
        driver.connect()
        fake_pymssql.connections[0].results["SELECT"] = (
            ["s", "t", "c", "d", "n", "pk"],
            [
                ("dbo", "Orders", "Id", "int", 0, 1),
                ("dbo", "Orders", "Note", "nvarchar", 1, 0),
                ("sales", "Leads", "Id", "uniqueidentifier", 0, 1),
            ],
        )
        tables = driver.schema()
        assert [t.name for t in tables] == ["Orders", "sales.Leads"]
        orders = tables[0].columns
        assert (orders[0].name, orders[0].primary_key, orders[0].nullable) == ("Id", True, False)
        assert (orders[1].data_type, orders[1].nullable) == ("nvarchar", True)

    def test_execute_rows_and_writes(self, fake_pymssql):
        import datetime
        import decimal

        driver = MssqlDriver(TARGET)
        driver.connect()
        conn = fake_pymssql.connections[0]
        conn.results["SELECT"] = (
            ["id", "total", "at", "blob", "note"],
            [
                (i, decimal.Decimal("9.50"), datetime.date(2026, 11, 12), b"\x01\x02", None)
                for i in range(5)
            ],
        )
        result = driver.execute("SELECT TOP 5 * FROM [Orders]", max_rows=3)
        assert result.columns == ["id", "total", "at", "blob", "note"]
        assert result.row_count == 3  # capped
        assert result.rows[0] == [0, "9.50", "2026-11-12", "0x0102", None]

        write = driver.execute("UPDATE [Orders] SET Note = 'x'", max_rows=10)
        assert write.columns == [] and write.affected_rows == 3

    def test_close(self, fake_pymssql):
        driver = MssqlDriver(TARGET)
        driver.connect()
        driver.close()
        assert fake_pymssql.connections[0].closed
        driver.close()  # harmless twice

    def test_missing_package_is_explained(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pymssql", None)  # import raises ImportError
        with pytest.raises(DriverMissing, match="pymssql"):
            MssqlDriver(TARGET).connect()

    def test_login_failure_reads_as_a_sentence(self):
        raw = (
            18456,
            b"Login failed for user 'cmadmin'.DB-Lib error message 20018, severity 14:\nGeneral SQL Server error",
        )
        exc = Exception(raw)
        assert describe_error(exc) == "Login failed for user 'cmadmin'. (error 18456)"
        assert describe_error(Exception("ORA-01017: invalid credentials\nHelp: https://x")) == (
            "ORA-01017: invalid credentials"
        )


class TestOracleDriver:
    def test_plain_connection(self, fake_oracledb):
        target = DbTarget(
            host="ora.example.com", port=1521, user="admin", password="pw", database=""
        )
        OracleDriver(target, ssl_mode="prefer").connect()
        kwargs = fake_oracledb.connections[0].kwargs
        assert kwargs["host"] == "ora.example.com" and kwargs["port"] == 1521
        assert kwargs["service_name"] == "ORCL"  # RDS Oracle's default
        assert "protocol" not in kwargs
        assert fake_oracledb.defaults.fetch_lobs is False
        assert fake_oracledb.connections[0].autocommit is True

    def test_tls_uses_the_tcps_listener(self, fake_oracledb):
        target = DbTarget(
            host="ora.example.com", port=2484, user="admin", password="pw", database="PROD"
        )
        OracleDriver(target, ssl_mode="verify").connect()
        kwargs = fake_oracledb.connections[0].kwargs
        assert kwargs["protocol"] == "tcps" and kwargs["service_name"] == "PROD"
        assert kwargs["ssl_server_dn_match"] is True and kwargs["ssl_context"].check_hostname

        OracleDriver(target, ssl_mode="verify", tunnelled=True).connect()
        tunnelled = fake_oracledb.connections[1].kwargs
        assert tunnelled["ssl_server_dn_match"] is False  # the address is 127.0.0.1

    @pytest.mark.parametrize(
        "sql, sent",
        [
            ("SELECT 1 FROM dual;", "SELECT 1 FROM dual"),
            ("  SELECT 1 FROM dual ;  ", "SELECT 1 FROM dual"),
            ("SELECT 1 FROM dual", "SELECT 1 FROM dual"),
            ("BEGIN NULL; END;", "BEGIN NULL; END;"),  # a PL/SQL block keeps its own
        ],
    )
    def test_trailing_semicolon(self, sql, sent):
        assert OracleDriver._statement(sql) == sent


# ── The connection layer with these drivers ──────────────────────────


def _mssql_connection(**options) -> DatabaseConnection:
    return DatabaseConnection(
        "rds",
        "mssql",
        "mssql://cmadmin:p%40ss%2Fw@cm-ecs-au.example.rds.amazonaws.com:1433/",
        read_only=True,
        options={"url_encoded": True, "ssl": "require", **options},
    )


async def test_sql_server_connection_end_to_end(fake_pymssql):
    conn = _mssql_connection()
    assert not conn.is_connected
    ok, message = await conn.connect()
    assert ok and message == "Connected" and conn.is_connected
    kwargs = fake_pymssql.connections[0].kwargs
    assert kwargs["server"] == "cm-ecs-au.example.rds.amazonaws.com"
    assert kwargs["password"] == "p@ss/w"  # decoded from the stored URL

    result = await conn.execute_query("SELECT 1")
    assert result.error is None and result.rows == [[1]]

    # Read-only is enforced before the driver sees the statement.
    blocked = await conn.execute_query("SELECT * INTO backup_users FROM users")
    assert blocked.error and "read-only" in blocked.error
    blocked = await conn.execute_query("EXEC xp_cmdshell 'dir'")
    assert blocked.error and "read-only" in blocked.error
    assert all(
        "INTO" not in sql and "EXEC" not in sql for sql in fake_pymssql.connections[0].executed
    )

    await conn.disconnect()
    assert not conn.is_connected and fake_pymssql.connections[0].closed
    assert (await conn.execute_query("SELECT 1")).error == "Not connected"


async def test_driver_errors_become_messages(fake_pymssql, monkeypatch):
    def refuse(**kwargs):
        raise Exception((18456, b"Login failed for user 'cmadmin'.DB-Lib error message 20018"))

    monkeypatch.setattr(fake_pymssql, "connect", refuse)
    ok, message = await _mssql_connection().connect()
    assert not ok and message == "Login failed for user 'cmadmin'. (error 18456)"


class _FakeTunnel:
    instances: list = []
    fail_with: str = ""

    def __init__(self, spec, host, port):
        self.spec, self.remote, self.stopped = spec, (host, port), False
        _FakeTunnel.instances.append(self)

    def start(self):
        if _FakeTunnel.fail_with:
            from polyglot_ai.core.db_tunnel import ConnectionSetupError

            raise ConnectionSetupError(_FakeTunnel.fail_with)
        return 15999

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_tunnel(monkeypatch):
    from polyglot_ai.core import db_tunnel

    _FakeTunnel.instances, _FakeTunnel.fail_with = [], ""
    monkeypatch.setattr(db_tunnel, "Tunnel", _FakeTunnel)
    return _FakeTunnel


async def test_private_instance_is_dialled_through_the_tunnel(fake_pymssql, fake_tunnel):
    conn = _mssql_connection(tunnel={"kind": "ssm", "target": "i-0abc123456789"})
    assert conn.route == "Session Manager via i-0abc123456789"
    ok, message = await conn.connect()
    assert ok and message == "Connected — Session Manager via i-0abc123456789"
    tunnel = fake_tunnel.instances[0]
    assert tunnel.remote == ("cm-ecs-au.example.rds.amazonaws.com", 1433)
    kwargs = fake_pymssql.connections[0].kwargs
    assert (kwargs["server"], kwargs["port"]) == ("127.0.0.1", "15999")

    await conn.disconnect()
    assert tunnel.stopped


async def test_tunnel_failure_is_the_error_and_nothing_is_left_running(fake_pymssql, fake_tunnel):
    fake_tunnel.fail_with = "The bastion refused the login."
    ok, message = await _mssql_connection(tunnel={"kind": "ssh", "host": "b"}).connect()
    assert not ok and message == "The bastion refused the login."
    assert fake_pymssql.connections == []


async def test_driver_failure_closes_the_tunnel(fake_pymssql, fake_tunnel, monkeypatch):
    monkeypatch.setattr(
        fake_pymssql, "connect", lambda **kw: (_ for _ in ()).throw(Exception("boom"))
    )
    ok, message = await _mssql_connection(tunnel={"kind": "ssh", "host": "b"}).connect()
    assert not ok and message == "boom"
    assert fake_tunnel.instances[0].stopped


class _Pool:
    def __init__(self):
        self.closed = False

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def fetchval(self, sql):
        return 1

    def cursor(self, *a):
        pool = self

        class _Cur:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                return False

        return _Cur()

    async def execute(self, sql):
        return None

    async def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def test_postgres_saved_before_options_still_gets_its_dsn_untouched(monkeypatch):
    import asyncpg

    seen = {}

    async def create_pool(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return _Pool()

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    dsn = "postgresql://u:p@localhost:5432/app?sslmode=require"
    conn = DatabaseConnection("old", "postgresql", dsn)  # no options: a legacy connection
    ok, _ = await conn.connect()
    assert ok and seen["args"] == (dsn,)
    assert "host" not in seen["kwargs"]


async def test_postgres_with_iam_and_tunnel(monkeypatch, fake_tunnel):
    import asyncpg

    from polyglot_ai.core import db_tunnel

    seen = {}

    async def create_pool(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return _Pool()

    tokens = []

    def token(host, port, user, profile, region):
        tokens.append((host, port, user, profile, region))
        return f"token-{len(tokens)}"

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(db_tunnel, "iam_auth_token", token)
    conn = DatabaseConnection(
        "pg",
        "postgresql",
        "postgresql://app_user@mydb.example.rds.amazonaws.com:5432/app",
        options={
            "url_encoded": True,
            "ssl": "prefer",
            "iam": {"profile": "work", "region": "eu-west-1"},
            "tunnel": {"kind": "ssh", "host": "bastion"},
        },
    )
    ok, _ = await conn.connect()
    assert ok and seen["args"] == ()
    kwargs = seen["kwargs"]
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 15999)
    assert kwargs["user"] == "app_user" and kwargs["database"] == "app"
    assert kwargs["ssl"] == "require"  # an IAM token is only accepted over TLS
    # The token is signed for the real endpoint, never for 127.0.0.1 …
    assert tokens[0] == ("mydb.example.rds.amazonaws.com", 5432, "app_user", "work", "eu-west-1")
    # … and the pool gets a callable, so later connections get fresh tokens.
    assert await kwargs["password"]() == "token-2"
    assert tokens[1][0] == "mydb.example.rds.amazonaws.com"


async def test_mysql_tls_modes(monkeypatch):
    import ssl

    from polyglot_ai.core import db_mysql

    calls = []

    async def open_session(**kwargs):
        calls.append(kwargs)
        if kwargs["ssl_context"] is not None and refuse_tls:
            raise Exception("2026, 'SSL connection error: server doesn't support SSL'")
        return _Pool()

    monkeypatch.setattr(db_mysql, "open_session", open_session)

    def conn(**options):
        return DatabaseConnection(
            "my", "mysql", "mysql://admin:p%40ss@db.example.com:3306/app", options=options
        )

    refuse_tls = False
    ok, _ = await conn(url_encoded=True, ssl="require").connect()
    assert ok and isinstance(calls[-1]["ssl_context"], ssl.SSLContext)
    assert calls[-1]["password"] == "p@ss" and calls[-1]["host"] == "db.example.com"
    # "Required" and "verify" must not log in to a server that offers no
    # TLS; the driver on its own would, without saying so.
    assert calls[-1]["require_tls"] is True and calls[-1]["password_factory"] is None
    ok, _ = await conn(url_encoded=True, ssl="verify").connect()
    assert ok and calls[-1]["require_tls"] is True
    assert calls[-1]["ssl_context"].verify_mode == ssl.CERT_REQUIRED

    # "Use if available": TLS first, plain only if the server can't.
    refuse_tls = True
    calls.clear()
    ok, _ = await conn(url_encoded=True, ssl="prefer").connect()
    assert ok and [c["ssl_context"] is not None for c in calls] == [True, False]
    assert [c["require_tls"] for c in calls] == [False, False]

    # "Required" never falls back.
    calls.clear()
    ok, message = await conn(url_encoded=True, ssl="require").connect()
    assert not ok and "SSL" in message and len(calls) == 1

    # Saved before TLS was offered: plain, exactly as before.
    refuse_tls = False
    calls.clear()
    legacy = DatabaseConnection("old", "mysql", "mysql://root:abc%41@localhost/app")
    ok, _ = await legacy.connect()
    assert ok and calls[-1]["ssl_context"] is None and calls[-1]["require_tls"] is False
    assert calls[-1]["password"] == "abc%41"


async def test_mysql_refusal_reaches_the_user_as_a_failed_connection(monkeypatch):
    from polyglot_ai.core import db_mysql

    async def open_session(**kwargs):
        raise db_mysql.TlsNotOffered(2026, "The server did not offer TLS, and this connection…")

    monkeypatch.setattr(db_mysql, "open_session", open_session)
    conn = DatabaseConnection(
        "my", "mysql", "mysql://admin:pw@db.example.com/app", options={"ssl": "require"}
    )
    ok, message = await conn.connect()
    assert not ok and "did not offer TLS" in message and not conn.is_connected


async def test_mysql_with_iam_renews_the_token_and_insists_on_tls(monkeypatch):
    from polyglot_ai.core import db_mysql, db_tunnel

    calls = []

    async def open_session(**kwargs):
        calls.append(kwargs)
        return _Pool()

    tokens = []

    def token(host, port, user, profile, region):
        tokens.append((host, port, user, profile, region))
        return f"token-{len(tokens)}"

    monkeypatch.setattr(db_mysql, "open_session", open_session)
    monkeypatch.setattr(db_tunnel, "iam_auth_token", token)
    conn = DatabaseConnection(
        "my",
        "mysql",
        "mysql://app_user@mydb.example.rds.amazonaws.com:3306/app",
        options={
            "url_encoded": True,
            "ssl": "prefer",  # as the dialog saves it when left alone
            "iam": {"profile": "work", "region": "eu-west-1"},
        },
    )
    ok, _ = await conn.connect()
    assert ok and len(calls) == 1
    kwargs = calls[0]
    # A token goes over the wire whole, so the server must offer TLS.
    assert kwargs["require_tls"] is True and kwargs["ssl_context"] is not None
    assert kwargs["password"] == "token-1"
    # Every later login asks for a new one, signed for the real endpoint.
    assert await kwargs["password_factory"]() == "token-2"
    assert tokens[1] == ("mydb.example.rds.amazonaws.com", 3306, "app_user", "work", "eu-west-1")


class _SlowTunnel:
    """Stands in for a tunnel whose process takes a while to exit."""

    def __init__(self, seconds: float = 0.3) -> None:
        self.seconds = seconds
        self.stopped_on = None

    def stop(self) -> None:
        import threading
        import time

        time.sleep(self.seconds)
        self.stopped_on = threading.current_thread()


async def test_disconnect_stops_the_tunnel_without_blocking_the_event_loop():
    """Stopping waits for the tunnel's process to exit — up to three
    seconds per signal. Done inline, that froze the window."""
    import asyncio
    import threading
    import time

    conn = DatabaseConnection("ms", "mssql", "mssql://admin:pw@db.example.com/app")
    tunnel = conn._tunnel = _SlowTunnel()

    longest_gap = 0.0

    async def heartbeat():
        nonlocal longest_gap
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            longest_gap = max(longest_gap, now - last)
            last = now

    beat = asyncio.ensure_future(heartbeat())
    await asyncio.sleep(0.05)
    await conn.disconnect()
    beat.cancel()

    assert tunnel.stopped_on is not None and conn._tunnel is None
    assert tunnel.stopped_on is not threading.main_thread()
    assert longest_gap < tunnel.seconds / 2  # the loop kept turning throughout


async def test_disconnect_all_reaches_every_connection_even_if_one_fails():
    import time

    from polyglot_ai.core.db_explorer import DatabaseManager

    manager = DatabaseManager()
    tunnels = []
    for name in ("a", "b", "c"):
        conn = await manager.add_connection(name, "mssql", "mssql://admin:pw@db.example.com/app")
        conn._tunnel = _SlowTunnel(0.3)
        tunnels.append(conn._tunnel)

    async def broken():
        raise RuntimeError("driver exploded")

    manager._connections["b"].disconnect = broken

    start = time.monotonic()
    await manager.disconnect_all()
    elapsed = time.monotonic() - start

    assert tunnels[0].stopped_on is not None and tunnels[2].stopped_on is not None
    assert elapsed < 0.55  # together, not one after another


def test_manager_passes_options_through():
    manager = db_explorer.DatabaseManager()
    conn = manager.add_connection_sync(
        "x", "oracle", "oracle://u:p@h:1521/ORCL", options={"url_encoded": True, "ssl": "prefer"}
    )
    assert conn.options == {"url_encoded": True, "ssl": "prefer"} and conn.route == ""
    assert set(db_drivers.DRIVERS) == {"mssql", "oracle"}
