"""The MySQL connection's two guarantees, against a fake server.

No database is needed: the server below speaks just enough of the MySQL
protocol to greet a client, accept any login and answer OK to whatever
follows — and it records what arrived unencrypted.
"""

from __future__ import annotations

import asyncio
import ssl
import struct

import pytest

pytest.importorskip("aiomysql")

from pymysql.constants import CLIENT  # noqa: E402

from polyglot_ai.core import db_mysql  # noqa: E402

_BASE_CAPS = (
    CLIENT.PROTOCOL_41
    | CLIENT.SECURE_CONNECTION
    | CLIENT.PLUGIN_AUTH
    | CLIENT.LONG_PASSWORD
    | CLIENT.CONNECT_WITH_DB
)


def _packet(seq: int, body: bytes) -> bytes:
    return struct.pack("<I", len(body))[:3] + bytes([seq]) + body


def _greeting(caps: int) -> bytes:
    return _packet(
        0,
        b"\x0a8.0.36\x00"
        + struct.pack("<I", 7)
        + b"12345678\x00"
        + struct.pack("<H", caps & 0xFFFF)
        + b"\x21"
        + struct.pack("<H", 2)
        + struct.pack("<H", (caps >> 16) & 0xFFFF)
        + b"\x15"
        + b"\x00" * 10
        + b"123456789012\x00"
        + b"mysql_native_password\x00",
    )


_OK = b"\x00\x00\x00\x02\x00\x00\x00"  # OK, autocommit on


class FakeServer:
    def __init__(self, *, offers_tls: bool) -> None:
        self.caps = _BASE_CAPS | (CLIENT.SSL if offers_tls else 0)
        self.logins: list[bytes] = []  # login packets received in the clear
        self.left_without_a_word = 0
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None
        self.port = 0

    async def __aenter__(self) -> FakeServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self.drop_clients()
        self._server.close()
        await self._server.wait_closed()

    def drop_clients(self) -> None:
        """What an idle timeout or a failover looks like to the client."""
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def _read(self, reader) -> bytes:
        header = await reader.readexactly(4)
        return await reader.readexactly(int.from_bytes(header[:3], "little"))

    async def _handle(self, reader, writer) -> None:
        self._writers.append(writer)
        try:
            writer.write(_greeting(self.caps))
            await writer.drain()
            try:
                login = await self._read(reader)
            except asyncio.IncompleteReadError:
                self.left_without_a_word += 1
                return
            self.logins.append(login)
            writer.write(_packet(2, _OK))
            while True:
                command = await self._read(reader)
                if command[:1] == b"\x01":  # COM_QUIT
                    return
                writer.write(_packet(1, _OK))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def _unverified_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def _open(server: FakeServer, **kwargs) -> db_mysql.MySQLSession:
    return await db_mysql.open_session(
        host="127.0.0.1",
        port=server.port,
        user="app_user",
        password=kwargs.pop("password", "s3cret"),
        database="orders",
        connect_timeout=5,
        **kwargs,
    )


async def _until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


async def test_required_tls_refuses_before_sending_the_login():
    """aiomysql alone logs in unencrypted here: an SSL context only takes
    effect if the server's (unencrypted, forgeable) greeting offers TLS."""
    async with FakeServer(offers_tls=False) as server:
        with pytest.raises(db_mysql.TlsNotOffered, match="did not offer TLS"):
            await _open(server, ssl_context=_unverified_context(), require_tls=True)
        assert await _until(lambda: server.left_without_a_word == 1)
        assert server.logins == []  # no user name, database or password proof left the machine


async def test_plain_aiomysql_would_have_sent_it():
    """The behaviour the guard exists for, pinned so a driver upgrade that
    changes it is noticed (the guard can then go)."""
    import aiomysql

    async with FakeServer(offers_tls=False) as server:
        connection = await aiomysql.connect(
            host="127.0.0.1",
            port=server.port,
            user="app_user",
            password="s3cret",
            db="orders",
            ssl=_unverified_context(),
            connect_timeout=5,
        )
        connection.close()
        assert len(server.logins) == 1 and b"app_user" in server.logins[0]


async def test_use_if_available_still_connects_to_a_server_without_tls():
    async with FakeServer(offers_tls=False) as server:
        session = await _open(server, ssl_context=_unverified_context(), require_tls=False)
        async with session.acquire() as connection:
            assert connection is not None
        session.close()
        assert len(server.logins) == 1 and b"orders" in server.logins[0]


async def test_reconnect_logs_in_with_a_fresh_password():
    """An IAM token is valid for fifteen minutes. A pool reuses the one it
    was created with whenever it replaces a dropped connection."""
    issued: list[str] = []

    async def fresh_token() -> str:
        issued.append(f"token-{len(issued) + 2}")
        return issued[-1]

    async with FakeServer(offers_tls=False) as server:
        session = await _open(
            server,
            password="token-1",
            ssl_context=None,
            require_tls=False,
            password_factory=fresh_token,
        )
        assert issued == []  # the first login uses the token it was handed
        async with session.acquire():
            pass
        assert issued == [] and len(server.logins) == 1  # a healthy connection is reused

        server.drop_clients()
        async with session.acquire() as connection:
            assert connection._password == "token-2"
        assert issued == ["token-2"] and len(server.logins) == 2

        server.drop_clients()
        async with session.acquire() as connection:
            assert connection._password == "token-3"
        session.close()


async def test_a_reconnect_keeps_refusing_a_server_without_tls():
    """The guard is on every login, not only the first."""
    async with FakeServer(offers_tls=False) as server:
        session = await _open(server, ssl_context=_unverified_context(), require_tls=False)
        session._connection._require_tls = True  # as if the first server had offered TLS
        server.drop_clients()
        with pytest.raises(db_mysql.TlsNotOffered):
            async with session.acquire():
                pass
        assert len(server.logins) == 1


async def test_connection_runs_with_autocommit():
    """One long-lived connection without it would keep reading the snapshot
    its first SELECT started."""
    async with FakeServer(offers_tls=False) as server:
        session = await _open(server, ssl_context=None, require_tls=False)
        assert session._connection.get_autocommit() is True
        session.close()
