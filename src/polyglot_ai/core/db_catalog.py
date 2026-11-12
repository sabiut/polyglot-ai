"""Which databases a server has, and the tables of one that isn't connected.

The schema browser shows every database the login can see with its
tables underneath. The connected database's tables come from
``DatabaseConnection.get_schema()`` as they always have; this module
answers the two questions that go beyond it. Both are called with the
connection's lock held.

What "another database" takes depends on the engine:

* MySQL / MariaDB and SQL Server read any database's catalogue from the
  session they already have.
* PostgreSQL sessions are bound to one database, so a second, short
  connection is opened to the other one — same server, login, TLS and
  tunnel as the first.
* Oracle sessions and SQLite files have exactly one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from polyglot_ai.core.db_explorer import DatabaseConnection, DatabaseInfo, TableInfo

logger = logging.getLogger(__name__)

_MYSQL_SYSTEM = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
#: Amazon's own on RDS; nobody but ``rdsadmin`` may connect to it.
_POSTGRES_SYSTEM = frozenset({"rdsadmin"})


def sorted_for_display(databases: list[DatabaseInfo]) -> list[DatabaseInfo]:
    """The connected database first, then the user's, then the engine's own."""
    return sorted(databases, key=lambda d: (not d.current, d.system, d.name.lower()))


def can_query_across(db_type: str) -> bool:
    """Can a query on this engine name a table in another database?"""
    return db_type in ("mysql", "mssql")


async def list_databases(conn: DatabaseConnection) -> list[DatabaseInfo]:
    if conn.db_type == "sqlite":
        if conn._sqlite_conn is None:
            return []
        location = conn._connection_string
        memory = location in ("", ":memory:")
        return [DatabaseInfo("in memory" if memory else Path(location).name, current=True)]

    if conn._driver is not None:  # SQL Server, Oracle
        from polyglot_ai.core.async_utils import run_blocking

        rows = await run_blocking(conn._driver.databases)
        return sorted_for_display([DatabaseInfo(*row) for row in rows])

    if conn.db_type == "postgresql" and conn._pg_pool is not None:
        async with conn._pg_pool.acquire() as pg:
            rows = await pg.fetch(
                "SELECT datname, datname = current_database() AS current "
                "FROM pg_database "
                "WHERE NOT datistemplate AND datallowconn "
                "  AND has_database_privilege(datname, 'CONNECT') "
                "ORDER BY datname"
            )
        return sorted_for_display(
            [
                DatabaseInfo(r["datname"], bool(r["current"]), r["datname"] in _POSTGRES_SYSTEM)
                for r in rows
            ]
        )

    if conn.db_type == "mysql" and conn._mysql_pool is not None:
        async with conn._mysql_pool.acquire() as my:
            async with my.cursor() as cursor:
                await cursor.execute(
                    "SELECT SCHEMA_NAME, SCHEMA_NAME <=> DATABASE() "
                    "FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME"
                )
                rows = await cursor.fetchall()
        return sorted_for_display(
            [
                DatabaseInfo(name, bool(current), name.lower() in _MYSQL_SYSTEM)
                for name, current in rows
            ]
        )
    return []


async def tables_of(conn: DatabaseConnection, database: str) -> list[TableInfo]:
    """Tables and columns of ``database``, which need not be the connected one.

    Raises with the server's reason when it can't be read, so the
    browser can show it under that database.
    """
    if conn._driver is not None:
        from polyglot_ai.core.async_utils import run_blocking

        return await run_blocking(conn._driver.schema, database)

    if conn.db_type == "mysql" and conn._mysql_pool is not None:
        return await conn._schema_mysql(database)

    if conn.db_type == "postgresql" and conn._pg_pool is not None and conn._pg_connect:
        import asyncpg

        args, kwargs = conn._pg_connect
        other = await asyncpg.connect(*args, **{**kwargs, "database": database, "timeout": 10})
        try:
            return await conn._pg_schema_on(other)
        finally:
            await other.close()
    return []
