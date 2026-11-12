# Database

The Database panel is a multi-engine database explorer plus a query runner
with result grid, charts, profiling, history, and saved snippets. Open with
`Ctrl+Shift+D`.

## Supported engines

| Database type | Also used for | Default port |
|---|---|---|
| **SQLite** | — | a file |
| **PostgreSQL** | Amazon Aurora (PostgreSQL), RDS for PostgreSQL | 5432 |
| **MySQL** | MariaDB, Amazon Aurora (MySQL), RDS for MySQL / MariaDB | 3306 |
| **SQL Server** | RDS for SQL Server | 1433 |
| **Oracle** | RDS for Oracle | 1521 |

Together that is every engine Amazon RDS offers except Db2. Nothing
extra needs installing: SQL Server and Oracle work without FreeTDS, an
ODBC driver or an Oracle client on your machine. MariaDB and Aurora have
their own entries in the **Database type** list so you don't need to
know which protocol they speak.

SQL Server, Oracle and the connection options below arrived in 0.18.21.

## Adding a connection

Click **+** in the panel. For a server database you fill in:

- **Host**, **Port**, **Username**, **Password** and **Database**
  (for Oracle this field is the **Service name**, e.g. `ORCL`).
  Passwords can contain any character, including `@`, `/`, `:` and `#`.
- **Encryption (TLS)** — see below.
- **Connect** — directly, or through a tunnel if the database is in a
  private network — see below.
- **Read-only (recommended)** — on by default.

The password is stored in the OS keyring. The other settings are saved
with the connection's name; none of them is a secret.

If a connection fails, the reason is shown in the schema area and in the
status line's tooltip — for a tunnel it says what to fix.

### Encryption (TLS)

| Choice | What it does |
|---|---|
| **Use if available** | Encrypts if the server offers it, otherwise connects without. |
| **Required** | Encrypts, and refuses to connect if the server doesn't offer it. Does not check who answered. |
| **Required, and verify the server's certificate** | As above, and checks the certificate against a **Certificate authority file** (or the system's CAs if left blank). |
| **Off** | No encryption. |

- For RDS, verification needs Amazon's CA bundle
  (`global-bundle.pem`, from the RDS documentation), because RDS
  certificates aren't signed by a public authority.
- **SQL Server** can't verify the certificate, so that choice isn't
  offered for it; **Required** still encrypts the connection.
- **Oracle** has no "use if available": with encryption on, use the
  port of the TLS listener (2484 on RDS) rather than 1521.

### Databases in a private network

Most RDS databases are not publicly accessible: only machines inside
their network can reach them. Under **Connect** choose how to get in:

- **SSH tunnel through a bastion host** — host, port, user and
  optionally a key file. It uses your SSH agent, `~/.ssh/config` or the
  key file; it cannot ask for a password or a passphrase, and the
  bastion's host key must already be trusted (connect to it once from a
  terminal first).
- **AWS Session Manager through an EC2 instance** — pick or type the ID
  of an instance in the database's network that runs the SSM agent. No
  open SSH port and no key are needed, but the AWS CLI and its
  `session-manager-plugin` must be installed, and you choose the AWS
  profile and region to use.

The tunnel is started when you connect and stopped when you disconnect
or quit the app.

### Signing in with an AWS IAM token (RDS)

For PostgreSQL and MySQL, **Sign in with → AWS IAM token (RDS)** uses a
short-lived token made from your AWS profile instead of a stored
password. The database must have IAM authentication enabled, the
database user must be set up for it, and your AWS identity needs the
`rds-db:connect` permission. IAM connections are always encrypted, and
a new token is fetched whenever the connection has to be reopened.

### Connecting to an RDS database from the AWS panel

In the **[AWS panel](CI-Docker-Kubernetes#aws)**, right-click a database
under **RDS** and choose **Connect in Database panel…**. The dialog opens
with the engine, endpoint, port, master username, database name and
**Required** encryption filled in — you only add the password (or switch
to an IAM token). If the database isn't publicly accessible, the dialog
is set to Session Manager and lists the account's running EC2 instances
to connect through.

## Layout

The panel has a sidebar mode (compact tree) and an expanded mode (full
explorer window). Click the expand icon to open the full window.

### Sidebar mode
- Connection list at top
- Schema tree for the active connection
- Mini query editor at the bottom

### Expanded mode
- Connection list + schema tree on the left
- Tabbed query notebook in the middle
- Result grid + chart / profile / export on the bottom

## Schema browser

Click a connection to expand it. Under each connection:

- **Databases / schemas** (engine-dependent)
- **Tables** — double-click to see the first 100 rows (in the engine's
  own dialect: `LIMIT`, `TOP` or `FETCH FIRST`). SQL Server tables
  outside the `dbo` schema are listed as `schema.table`; Oracle lists
  the tables owned by the user you signed in as.
- **Views**
- **Indexes**
- **Routines** (procs/functions, where supported)

Right-click a table for quick actions:

- **SELECT \* LIMIT 100** — opens a new query cell with the SQL.
- **DDL** — shows the CREATE TABLE statement.
- **Row count** — runs `SELECT count(*) …` asynchronously.

## Query notebook

The query area is a notebook — multiple **cells**, each with its own SQL
and its own result grid. Cells are saved per connection.

### Running a query
- `Ctrl+Enter` runs the current cell.
- `Ctrl+Shift+Enter` runs all cells top to bottom.
- A streaming status shows at the bottom ("Running… 2.3s").

### Result grid
- Sortable columns.
- Copy selected cells or whole rows.
- **Export** → CSV.
- **Chart** toggle → bar / line / histogram / scatter using QtCharts.
- **Profile** → sidebar with row count, null %, distinct count, min/max for
  numeric columns, top categories for text columns.

### Query history

Every run is logged (last 50 per connection) with the SQL, duration, row
count, and timestamp. Reopen any query with one click.

### Saved snippets

Save frequently-used queries per connection as **named snippets**.
Right-click in the history or query list → **Save as snippet**.

## Destructive queries

Non-SELECT queries (`UPDATE`, `DELETE`, `DROP`, `TRUNCATE`) run through a
separate code path that pops a **confirmation dialog** showing the
statement. You must click **Run write query** explicitly.

> **Recommended**: enable **Read-only mode** per connection in the
> connection editor if you never want to mutate through the tool.

Read-only mode also refuses statements that start with `SELECT` but
aren't reads: `SELECT … INTO` (creates a table, or with MySQL's
`INTO OUTFILE` writes a file on the server) and SQL Server's
`OPENROWSET`, `OPENQUERY` and `OPENDATASOURCE`.

## AI integration

When a task is active and tagged **Explore**, the chat panel can inject
the current connection's schema into its context so you can ask natural-
language questions about your data.

A natural-language-to-SQL helper is on the roadmap — it will live in the
query cell toolbar as a `NL → SQL` button.

## Tips

- **Use snippets** instead of pasting the same join over and over.
- **Profile before you chart.** The profile sidebar tells you whether a
  column is worth charting (e.g. 99% nulls → probably not).
- **Never put credentials in the query text.** Use the connection editor;
  credentials go into the keyring.
