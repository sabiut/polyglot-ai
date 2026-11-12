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

## Editing a connection

Select the connection in the list and click the **pencil** next to it.
The same dialog opens with everything the connection holds — host, port,
user, database, encryption, tunnel, IAM settings, read-only — and the
saved password in the (masked) password field. Change what you need and
click **Save**.

- Changing only the **name** or **Read-only** keeps the connection as it
  is: if it was open it stays open. Its query history and saved snippets
  move to the new name.
- Changing anything else closes the connection and, if it was open,
  reopens it with the new details. An open SQL editor window carries on
  with the edited connection and keeps the SQL you had typed.
- A connection can't be renamed to a name another connection already
  has.

Editing a saved connection is available from 0.18.22. In earlier
versions, add a connection with the same name to replace it.

## Deleting a connection

Select the connection in the list and click the **bin** next to the
pencil. You are asked first, and **Cancel** is the default.

- The connection's details and its saved password are removed from this
  computer. The database itself isn't touched.
- If the connection is open it is closed, along with a tunnel it was
  using and a SQL editor window that was working on it.
- Its query history and saved snippets are kept unless you tick **Also
  delete its query history and saved snippets**. Kept ones come back if
  you later add a connection with the same name.

- If the connection list can't be saved — the keyring is locked, the
  disk is full — nothing is deleted and the connection stays as it was.

The red **✕** only disconnects; it never removes a saved connection.
Deleting a connection is available from 0.18.23.

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

### What the SQL editor window shows (from 0.18.22)

- **Header** — the connection's name, its engine, where it points
  (`user@host:port/database`, plus the tunnel if there is one — never the
  password) and a **READ-ONLY** or **WRITE ACCESS** badge, so a
  production window can't be mistaken for a development one.
- **Schema** — the server's databases, the tables of each and the
  columns of each table (see [Schema browser](#schema-browser)). The type
  is shown beside every column and primary keys are marked `PK`. Hover a
  column for its full type and whether it can be null. The **↻** button
  reloads the list after a `CREATE` or `ALTER`, and reopens the
  connection if the server has dropped it.
- **SQL query** — a fixed-width editor with syntax colouring. The example
  in the empty editor is written for the connection's engine.
- **History** — each run on two lines: the SQL, then the time, row count
  and duration. A query that failed shows the reason there in red, and
  the full error in its tooltip.

## Schema browser

Connecting fills the tree, in the sidebar and in the SQL editor window
alike, with three levels (from 0.18.22; earlier versions list the
connected database's tables only):

- **Databases** — every database on the server that your login may open.
  The one the connection is signed in to comes first, in bold, already
  open. The engine's own (`master`, `tempdb`, `information_schema`, …)
  are greyed and marked *system*.
- **Tables** — under each database, with a column count. Another
  database's tables are read the first time you open it; if the server
  refuses, the reason is shown under that database.
- **Columns** — under each table, with the type and `PK` for primary
  keys.

Click a table (double-click in the sidebar) to see its first 100 rows, in
the engine's own dialect: `LIMIT`, `TOP` or `FETCH FIRST`.

What "every database" means depends on the engine:

| Engine | Databases listed | Tables in another database |
|---|---|---|
| SQL Server | all online databases you have access to | browse and query — the preview uses the full `[database].[schema].[table]` name |
| MySQL / MariaDB | all schemas you can see | browse and query — the preview uses `` `database`.`table` `` |
| PostgreSQL | all databases you may connect to | browse only. A PostgreSQL session is tied to one database, so to *query* another, add a connection with that name in its **Database** field |
| Oracle | the one you signed in to | — |
| SQLite | the file | — |

SQL Server tables outside the `dbo` schema are listed as `schema.table`.
PostgreSQL lists the tables of the `public` schema. Oracle lists the
tables owned by the user you signed in as.

If the list of databases can't be read (a login without permission to
see the catalogue), the tree shows the connected database's tables on
their own, as it did before.

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

## Backing up and restoring (from 0.18.22)

The **Backup** button at the bottom of the Database panel has two
entries, both for the connection selected in the list. The connection
doesn't have to be open: a backup or restore makes its own connection,
through the same tunnel, TLS settings and IAM sign-in, so you can keep
working in the SQL editor meanwhile.

### Back up…

Choose where to save the file and click **Back up**. What you get
depends on the engine and on what is installed:

| Engine | How | What is in the file |
|---|---|---|
| SQLite | an exact copy of the database file | everything; open it like any SQLite database. Safe while the database is in use, and the copy is checked afterwards |
| PostgreSQL | `pg_dump`, when it is installed | everything `pg_dump` writes — tables, data, views, functions, sequences, indexes, constraints — without ownership and grants, so it loads into any server. Name the file `.dump` for the compressed custom format |
| MySQL / MariaDB | `mysqldump`, when it is installed | tables, data, views, routines and triggers |
| PostgreSQL, MySQL / MariaDB, SQL Server, Oracle | the **built-in export** — always available, nothing to install | every table and its rows as a `.sql` file, with primary keys (and for MySQL the complete table definition). See below for what it leaves out |

When the vendor's tool is installed it is offered first, and the
built-in export stays available as the second choice. When it isn't, the
dialog says which package provides it (`postgresql-client`,
`mariadb-client`).

The **built-in export** is a data backup, not a full copy of the server:

- It does **not** include views, stored procedures and functions,
  triggers, users and permissions. For PostgreSQL, SQL Server and Oracle
  it also leaves out indexes, foreign keys, column defaults and
  sequences — tables come back with their columns, primary key and rows.
  The exact list for your engine is in the dialog and at the top of the
  file.
- PostgreSQL, MySQL (InnoDB) and Oracle are read as of a single moment,
  whatever is written meanwhile. SQL Server tables are read one after
  another.
- Oracle: binary values over 2,000 bytes can't be written as SQL; they
  are saved as `NULL` and the dialog and the file both say so.
- The file starts with what it is, when it was taken and how to load it
  with the engine's own tools (`psql`, `mysql`, `sqlcmd -I`, `sqlplus`),
  so it is useful without Polyglot AI too.

For every method:

- Tick **Compress** (or end the name in `.gz`) to gzip a `.sql` file.
- The file is readable by you only, and is written under a temporary
  name until it is complete: a backup that fails or is cancelled leaves
  nothing behind, and never replaces an earlier backup with half a one.
- Passwords are never put on a command line.
- A read-only connection can be backed up — a backup only reads.

> For an Amazon RDS database, a **snapshot** is the complete,
> restorable copy — take one from the
> [AWS panel](CI-Docker-Kubernetes#actions-right-click). Use Back up
> when you want the data as a file.

### Restore from a backup…

Pick a file. The dialog tells you what it is (it looks at the contents,
not the name), how it will be loaded, and whether it fits this
connection; then tick **Load this backup into …** and click **Restore**.

| File | Loaded into | How |
|---|---|---|
| SQLite database file | a SQLite connection | the connection's file is replaced with the backup. The database as it was is saved next to it first, as `<name>.before-restore-<date>-<time>`, so the restore can be undone |
| Built-in export | the same engine it was made from | its statements are run in order. **PostgreSQL**: one transaction — if anything fails, nothing is changed. **MySQL, SQL Server, Oracle**: each statement is committed as it runs; if one fails the load stops and tells you how many were applied |
| `pg_dump` file (`.sql` or `.dump`) | PostgreSQL | with `psql` / `pg_restore`, in one transaction |
| `mysqldump` file | MySQL / MariaDB | with the `mysql` client |
| any `.sql` script | SQL Server | run in `GO` batches, as `sqlcmd` would |

Things to know:

- **Restore into an empty database.** Nothing is dropped or replaced
  first: if the database already has tables with the same names, the
  load stops at the first one.
- The connection must not be **Read-only** — untick it with the pencil
  first. This is deliberate: a restore writes a lot.
- A backup made from one engine is refused for another, and a built-in
  export that was cut short (it has no closing line) is refused before
  anything is run.
- Try a restore into a scratch database once before you rely on a
  backup.

AWS Database Migration Service (DMS) is not part of the app. To move a
database between engines or accounts, use DMS from the AWS console or
CLI.

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
