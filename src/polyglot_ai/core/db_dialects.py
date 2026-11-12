"""What differs between database engines, in one place.

The Database panel talks to SQLite, PostgreSQL, MySQL/MariaDB, SQL Server
and Oracle — which between them cover every engine Amazon RDS offers
except Db2. This module holds the per-engine facts (default port, how to
quote a name, how to say "first 100 rows"), the connection-URL format the
panel stores in the keyring, and the mapping from an RDS engine name to
one of ours.

No Qt and no drivers here, so everything is unit-testable.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit


@dataclass(frozen=True)
class Engine:
    key: str  # the ``db_type`` stored with a connection
    label: str
    default_port: int
    default_user: str
    #: What the "database" field means for this engine.
    database_label: str = "Database"
    default_database: str = ""
    #: Module that must be importable, and what to tell the user if not.
    driver: str = ""
    #: Can the driver check the server's certificate? pymssql has no way
    #: to be given a CA or told to validate, so SQL Server can't.
    verifies_certificate: bool = True


ENGINES: dict[str, Engine] = {
    "postgresql": Engine("postgresql", "PostgreSQL", 5432, "postgres", driver="asyncpg"),
    "mysql": Engine("mysql", "MySQL", 3306, "admin", driver="aiomysql"),
    "mssql": Engine(
        "mssql",
        "SQL Server",
        1433,
        "admin",
        default_database="master",
        driver="pymssql",
        verifies_certificate=False,
    ),
    "oracle": Engine(
        "oracle",
        "Oracle",
        1521,
        "admin",
        database_label="Service name",
        default_database="ORCL",
        driver="oracledb",
    ),
}

#: The choices in the "Database type" list: (label, db_type). MariaDB and
#: Aurora speak the MySQL / PostgreSQL protocols, so they share a type —
#: they're listed so nobody has to know that to find their engine.
ENGINE_CHOICES: list[tuple[str, str]] = [
    ("SQLite", "sqlite"),
    ("PostgreSQL", "postgresql"),
    ("MySQL", "mysql"),
    ("MariaDB", "mysql"),
    ("Amazon Aurora (PostgreSQL)", "postgresql"),
    ("Amazon Aurora (MySQL)", "mysql"),
    ("SQL Server", "mssql"),
    ("Oracle", "oracle"),
]

#: Encryption choices: (label, mode).
SSL_MODES: list[tuple[str, str]] = [
    ("Use if available", "prefer"),
    ("Required", "require"),
    ("Required, and verify the server's certificate", "verify"),
    ("Off", "disable"),
]


def ssl_modes_for(db_type: str) -> list[tuple[str, str]]:
    """The encryption choices this engine can honour.

    "Verify" is left out where the driver can't do it: offering it would
    promise a check that never happens.
    """
    engine = ENGINES.get(db_type)
    if engine is not None and not engine.verifies_certificate:
        return [choice for choice in SSL_MODES if choice[1] != "verify"]
    return list(SSL_MODES)


def db_type_for_rds_engine(engine: str) -> str | None:
    """Our ``db_type`` for an RDS ``Engine`` value, or None if unsupported.

    RDS names: ``postgres``, ``mysql``, ``mariadb``, ``aurora-postgresql``,
    ``aurora-mysql``, ``sqlserver-ex|web|se|ee``, ``oracle-ee|se2[-cdb]``,
    ``db2-se|ae``, and ``custom-…`` variants of SQL Server and Oracle.
    """
    name = (engine or "").lower().removeprefix("custom-")
    if name in ("postgres", "aurora-postgresql"):
        return "postgresql"
    if name in ("mysql", "mariadb", "aurora-mysql", "aurora"):
        return "mysql"
    if name.startswith("sqlserver"):
        return "mssql"
    if name.startswith("oracle"):
        return "oracle"
    return None


# ── Connection URLs ──────────────────────────────────────────────────


@dataclass
class DbTarget:
    host: str = "localhost"
    port: int = 0
    user: str = ""
    password: str = ""
    database: str = ""


def build_url(
    db_type: str, host: str, port: int | str, user: str, password: str, database: str
) -> str:
    """``scheme://user:password@host:port/database`` with every part escaped.

    The password used to be pasted in as typed, so one containing ``@``,
    ``/``, ``:`` or ``#`` — which generated RDS passwords usually do —
    produced a URL that parsed into the wrong host.
    """
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    return f"{db_type}://{auth}{host}:{port}/{quote(database, safe='')}"


def parse_url(url: str, db_type: str = "", *, encoded: bool = True) -> DbTarget:
    """The parts of a stored connection URL.

    ``encoded=False`` is for connections saved before escaping existed:
    their parts are taken literally, so a password that happens to
    contain ``%41`` isn't silently turned into ``A``.
    """
    parts = urlsplit(url)
    decode = unquote if encoded else (lambda text: text)
    engine = ENGINES.get(db_type or parts.scheme)
    try:
        port = parts.port
    except ValueError:
        port = None
    return DbTarget(
        host=parts.hostname or "localhost",
        port=port or (engine.default_port if engine else 0),
        user=decode(parts.username or ""),
        password=decode(parts.password or ""),
        database=decode(parts.path.lstrip("/")) if parts.path else "",
    )


def form_values(
    name: str,
    db_type: str,
    connection_string: str,
    options: dict | None = None,
    *,
    read_only: bool = True,
) -> dict:
    """A saved connection as the fields of the connection dialog.

    The inverse of what the dialog saves, so a connection can be opened
    for editing. Keys are the ones ``AddConnectionDialog.prefill`` takes.
    A connection saved before encryption was a choice shows the mode it
    has actually been using: none for MySQL, "if available" for the rest.
    """
    options = options or {}
    values: dict = {"name": name, "db_type": db_type, "read_only": read_only}
    if options.get("engine_label"):
        values["engine_label"] = options["engine_label"]
    if db_type == "sqlite":
        values["path"] = connection_string
        return values

    target = parse_url(connection_string, db_type, encoded=bool(options.get("url_encoded")))
    values.update(
        host=target.host,
        port=target.port,
        user=target.user,
        password=target.password,
        database=target.database,
        ssl=options.get("ssl") or ("disable" if db_type == "mysql" else "prefer"),
        ca_file=options.get("ca_file", ""),
        route="direct",
    )
    iam = options.get("iam")
    if iam is not None:
        values.update(auth="iam", profile=iam.get("profile", ""), region=iam.get("region", ""))
    tunnel = options.get("tunnel") or {}
    if tunnel.get("kind") == "ssh":
        values.update(
            route="ssh",
            ssh_host=tunnel.get("host", ""),
            ssh_port=tunnel.get("port", 22),
            ssh_user=tunnel.get("user", ""),
            ssh_key=tunnel.get("identity_file", ""),
        )
    elif tunnel.get("kind") == "ssm":
        values.update(route="ssm", ssm_target=tunnel.get("target", ""))
        # One profile/region pair in the form, shared with IAM sign-in.
        values["profile"] = values.get("profile") or tunnel.get("profile", "")
        values["region"] = values.get("region") or tunnel.get("region", "")
    return values


def describe_target(db_type: str, connection_string: str, options: dict | None = None) -> str:
    """Where a connection points, for display: ``user@host:port/database``.

    Never the password. For SQLite, the file.
    """
    if db_type == "sqlite":
        return connection_string
    options = options or {}
    target = parse_url(connection_string, db_type, encoded=bool(options.get("url_encoded")))
    host = f"[{target.host}]" if ":" in target.host else target.host
    text = f"{host}:{target.port}" if target.port else host
    if target.user:
        text = f"{target.user}@{text}"
    if target.database:
        text = f"{text}/{target.database}"
    return text


# ── SQL dialect ──────────────────────────────────────────────────────


def quote_identifier(db_type: str, name: str) -> str:
    """One table or column name, quoted the way this engine wants."""
    if db_type == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if db_type == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def quote_table(db_type: str, table: str, database: str = "") -> str:
    """A table name as the schema browser lists it, ready to use in SQL.

    SQL Server tables outside ``dbo`` are listed as ``schema.table``.
    ``database`` names another database on the same server — which only
    MySQL and SQL Server can reach from the session they have, so it is
    ignored for the rest.
    """
    if db_type == "mssql":
        schema, _, name = table.partition(".") if "." in table else ("", "", table)
        parts = [schema, name] if schema else [name]
        if database:
            parts = [database, schema or "dbo", name]
        return ".".join(quote_identifier(db_type, part) for part in parts)
    if db_type == "mysql" and database:
        return f"{quote_identifier(db_type, database)}.{quote_identifier(db_type, table)}"
    return quote_identifier(db_type, table)


def preview_query(db_type: str, table: str, limit: int = 100, database: str = "") -> str:
    """``SELECT`` the first rows of a table in this engine's dialect."""
    target = quote_table(db_type, table, database)
    if db_type == "mssql":
        return f"SELECT TOP {limit} * FROM {target}"
    if db_type == "oracle":
        return f"SELECT * FROM {target} FETCH FIRST {limit} ROWS ONLY"
    return f"SELECT * FROM {target} LIMIT {limit}"


# ── TLS ──────────────────────────────────────────────────────────────


def make_ssl_context(
    mode: str, ca_file: str = "", *, check_hostname: bool = True
) -> ssl.SSLContext | None:
    """An SSL context for ``require`` / ``verify``; None for the other modes.

    ``require`` encrypts without checking who answered — enough to satisfy
    a server that insists on TLS. ``verify`` checks the certificate against
    ``ca_file`` (RDS certificates are signed by Amazon's own RDS CAs, which
    aren't in the system store, so a bundle is normally needed) or, without
    one, the system store. ``check_hostname`` is turned off when connecting
    through a tunnel, where the address dialled is 127.0.0.1.
    """
    if mode == "require":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if mode == "verify":
        context = ssl.create_default_context(cafile=ca_file or None)
        context.check_hostname = check_hostname
        return context
    return None
