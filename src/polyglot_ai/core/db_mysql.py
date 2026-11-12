"""The Database panel's MySQL connection, with two gaps in aiomysql closed.

* **TLS that can't be skipped.** Given an SSL context, aiomysql only
  starts TLS if the server's greeting says it supports it — otherwise it
  logs in over the plain connection without a word. The greeting itself
  travels unencrypted, so anything on the path can clear that flag.
  "Required" and "verify" therefore have to refuse *before* the login
  packet (user name, database, password proof — or an IAM token in full)
  is written.

* **IAM tokens that are fresh for every login.** A token is valid for
  fifteen minutes and only matters while logging in. A pool keeps the
  arguments it was created with, so the connection it opens to replace
  one the server dropped an hour later would present the expired token.

The explorer runs one statement at a time under its own lock, so it needs
one connection, not a pool: it is checked, and reopened if necessary,
before each use. That also lets it run with autocommit on, like the SQL
Server and Oracle connections — with it off, a long-lived connection
keeps reading from the snapshot its first SELECT started.

Importing this module needs aiomysql; the explorer imports it lazily and
falls back to MCP when the driver isn't installed.
"""

from __future__ import annotations

import contextlib
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable

import aiomysql
from pymysql.constants import CLIENT


class TlsNotOffered(aiomysql.OperationalError):
    """The server's greeting didn't offer TLS and the connection requires it."""


class _Connection(aiomysql.Connection):
    """An aiomysql connection that can insist on TLS and renew its password."""

    def __init__(
        self,
        *args,
        require_tls: bool = False,
        password_factory: Callable[[], Awaitable[str]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._require_tls = require_tls
        self._password_factory = password_factory
        self._opened_before = False

    async def _connect(self) -> None:
        # Also the path ``ping(reconnect=True)`` takes. The first login uses
        # the password it was given (the caller has just fetched it).
        if self._opened_before and self._password_factory is not None:
            self._password = await self._password_factory()
        await super()._connect()
        self._opened_before = True

    async def _request_authentication(self) -> None:
        # Called once the greeting has been read and before anything is
        # sent back; the parent starts TLS here, or silently doesn't.
        if self._require_tls and not (self.server_capabilities & CLIENT.SSL):
            raise TlsNotOffered(
                2026,
                "The server did not offer TLS, and this connection requires encryption. "
                "Nothing was sent. Choose “Use if available” only if an unencrypted "
                "connection is acceptable.",
            )
        await super()._request_authentication()


class MySQLSession:
    """One connection, used the way the explorer used a pool."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiomysql.Connection]:
        # Reopens the connection if the server has dropped it (idle
        # timeout, failover, a tunnel that went away) — through
        # ``_connect`` above, so with TLS enforced and a fresh token.
        await self._connection.ping(reconnect=True)
        yield self._connection

    def close(self) -> None:
        self._connection.close()

    async def wait_closed(self) -> None:
        return None


async def open_session(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    ssl_context: ssl.SSLContext | None,
    require_tls: bool,
    password_factory: Callable[[], Awaitable[str]] | None = None,
    connect_timeout: int = 10,
) -> MySQLSession:
    """Connect and log in. ``require_tls`` refuses a server that offers none."""
    connection = _Connection(
        host=host,
        port=port,
        user=user,
        password=password,
        db=database,
        ssl=ssl_context,
        connect_timeout=connect_timeout,
        autocommit=True,
        require_tls=require_tls,
        password_factory=password_factory,
    )
    await connection._connect()
    return MySQLSession(connection)
