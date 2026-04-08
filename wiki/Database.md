# Database

The Database panel is a multi-engine database explorer plus a query runner
with result grid, charts, profiling, history, and saved snippets. Open with
`Ctrl+Shift+D`.

## Supported engines

- **PostgreSQL** (asyncpg)
- **MySQL / MariaDB** (aiomysql)
- **SQLite** (aiosqlite)

Connections are configured in **Settings → Database → Connections** or
via the **+** button in the panel. Credentials are stored in the OS
keyring.

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
- **Tables** — double-click to see the first 100 rows
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
