# MCP server

`sqlguardllm mcp` gives Claude Desktop, Cursor, VS Code or any [Model Context Protocol](https://modelcontextprotocol.io) client a database tool that **can't write, can't see hidden data and can't escape row filters**. Every query the model writes goes through the same guard as the library, then runs in a read-only transaction with a timeout.

| Tool | What the model gets |
|---|---|
| `list_tables` | The tables it may query with their usable columns, the SQL dialect and the row limit, plus masked columns, aggregate-only tables and required filters when the policy has them |
| `describe_table` | Columns and types, primary key, foreign keys to other allowed tables, masked columns (with their strategy), whether rows are filtered for the user, required filters, aggregate-only (with its minimum group size) and whether `SELECT *` works |
| `explain_query` | [`Guard.explain()`](DESIGN.md#richer-policies) without running anything: whether the query is allowed, the tables and columns it reads, the masks, group sizes, row filters (as written in the policy, with their `{user.…}` placeholders) and limit it gets, or the violations |
| `query` | `Guard.enforce()`, then the safe SQL in a read-only transaction: `{"columns": [...], "rows": [[...]], "row_count": n, "limit_reached": bool}`, at most `max_rows` rows |

A rejected query comes back as a tool error with one `code: message` line per violation, such as `hidden_column: customers.email is a hidden column`, so the model can repair it.

## 1. Install

You need [uv](https://docs.astral.sh/uv/) (`uvx`) or [pipx](https://pipx.pypa.io/). Nothing else: the client starts the server with one of these, in its own environment.

```bash
uvx --from "sqlguardllm[mcp]" sqlguardllm mcp --help
pipx run --spec "sqlguardllm[mcp]" sqlguardllm mcp --help
```

Until a release on PyPI includes the `mcp` extra, install it from GitHub: `uvx --from "sqlguardllm[mcp] @ git+https://github.com/jmmana/sqlguardllm" sqlguardllm mcp --help`, and use that `--from` in the configurations below.

The `mcp` extra brings the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [SQLAlchemy](https://www.sqlalchemy.org/) and PyYAML. SQLite works out of the box; for other databases add the driver (see [Your own database](#your-own-database)).

```text
sqlguardllm mcp --policy FILE [--role NAME] [--db URL] [--context JSON|@file]
                [--language en|es] [--timeout SECONDS]
                [--max-cost N] [--max-plan-rows N]
```

| Option | |
|---|---|
| `--policy` | A policy file (`.yaml` or `.json`), or a policy set with `--role` |
| `--role` | The role to use from a policy set (`base` and `roles`, see [`examples/roles.yaml`](../examples/roles.yaml)) |
| `--db` | A [SQLAlchemy URL](https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls). Defaults to the `DATABASE_URL` environment variable, which keeps a password out of the command line |
| `--context` | The values for row filters, as JSON or `@file.json`. Set by you, never by the model |
| `--language` | Language of violation messages: `en` (default) or `es` |
| `--timeout` | Seconds a query may run before it is cancelled (default 30) |
| `--max-cost` | Cost guard: refuse queries whose planner cost is above this (PostgreSQL and MySQL only) |
| `--max-plan-rows` | Cost guard: refuse queries whose planner row estimate is above this (PostgreSQL and MySQL only) |

### Cost guard

With `--max-cost` or `--max-plan-rows` (or `max_cost=` / `max_plan_rows=` in `create_server`), a query the guard allowed is first sent to the engine as `EXPLAIN (FORMAT JSON)` on PostgreSQL (`Total Cost` and `Plan Rows` of the top node) or `EXPLAIN FORMAT=JSON` on MySQL 8 (`query_cost` and the largest `rows_examined_per_scan` / `rows_produced_per_join`), inside the same read-only transaction. It never uses `EXPLAIN ANALYZE`, which would run the query. If an estimate is above its threshold the query is not run and the model gets an error with the estimate, the limit and a hint to add a filter or select fewer columns. `explain_query` also returns the `estimate`.

- The units are the engine's own, so pick thresholds by trying typical queries. The cost is an estimate from table statistics (run `ANALYZE`).
- It fails closed: if `EXPLAIN` fails or its output can't be read, the query is refused instead of run blind.
- On other engines (SQLite, SQL Server, Oracle, ...) the options are ignored, with a warning at startup.

It exits with code 2 and a message on stderr when the extra isn't installed, the policy is invalid, the database URL is wrong or its driver is missing.

## 2. Try it with the demo database

[`examples/mcp/`](../examples/mcp/) has a small SQLite sales database and a policy set with two roles. A branch manager only sees the invoices of their branches; customer emails and tax ids are hidden, phones come back masked, and the `api_keys` table doesn't exist as far as the model can tell.

```bash
git clone https://github.com/jmmana/sqlguardllm && cd sqlguardllm
python -c "import sqlite3; sqlite3.connect('demo.db').executescript(open('examples/mcp/demo.sql').read())"
```

The server for a manager of branch 1 (the context is in [`examples/mcp/context.json`](../examples/mcp/context.json): `{"user": {"branches": [1]}}`):

```bash
uvx --from "sqlguardllm[mcp]" sqlguardllm mcp \
  --policy examples/mcp/roles.yaml --role manager \
  --context @examples/mcp/context.json --db sqlite:///demo.db
```

It waits for a client on stdin, so it looks like nothing happens: stop it with Ctrl+C and register it in your client below, with **absolute paths** (`$(pwd)` prints the folder). To click through the tools without a client, use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector): `npx @modelcontextprotocol/inspector` followed by the same command.

Then ask the assistant something like *"Which branch sold the most this month?"* and watch the tool calls:

- `list_tables` shows `branches`, `customers` (without `email` and `tax_id`) and `invoices`.
- `SELECT b.name, SUM(i.total) … GROUP BY b.name` returns only Centro: the guard added `branch_id IN (1)` to every read of `invoices`.
- `SELECT secret FROM api_keys` comes back as `table_not_allowed`, `SELECT email FROM customers` as `hidden_column`, and `DELETE FROM invoices` as `not_a_query`.

With `--role auditor` the same questions see every branch, without customer names.

## 3. Claude Desktop

Open **Settings → Developer → Edit Config**, or the file itself:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "sales-db": {
      "command": "uvx",
      "args": [
        "--from", "sqlguardllm[mcp]",
        "sqlguardllm", "mcp",
        "--policy", "/Users/you/sqlguardllm/examples/mcp/roles.yaml",
        "--role", "manager",
        "--context", "@/Users/you/sqlguardllm/examples/mcp/context.json",
        "--db", "sqlite:////Users/you/sqlguardllm/demo.db"
      ]
    }
  }
}
```

With pipx, use `"command": "pipx"` and start `args` with `"run", "--spec", "sqlguardllm[mcp]", "sqlguardllm", "mcp"`.

- Paths must be absolute. A SQLite URL to an absolute path has four slashes: `sqlite:////Users/you/demo.db` (`sqlite:///C:/Users/you/demo.db` on Windows).
- Quit Claude Desktop completely and open it again after editing the file.
- If the server doesn't start because `uvx` isn't found, put its full path in `command` (`which uvx` prints it): apps started from the Dock don't get your shell's `PATH`.
- The server's stderr goes to `mcp-server-sales-db.log` in `~/Library/Logs/Claude` (macOS), `%APPDATA%\Claude\logs` (Windows) or `~/.config/Claude/logs` (Linux).

For a database with a password, put the URL in `env` instead of `args`:

```json
"env": { "DATABASE_URL": "postgresql+psycopg://reader:secret@db.example.com/sales" }
```

and put the driver first in `args`: `"--with", "psycopg[binary]", "--from", "sqlguardllm[mcp]", …` (see [Your own database](#your-own-database)). Without `--db`, the server reads `DATABASE_URL`.

## 4. Cursor

Add the server to `.cursor/mcp.json` in your project, or to `~/.cursor/mcp.json` for every project. Cursor expands `${workspaceFolder}` (the folder that contains `.cursor/`), `${userHome}` and `${env:NAME}` in `command`, `args` and `env`:

```json
{
  "mcpServers": {
    "sales-db": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "sqlguardllm[mcp]",
        "sqlguardllm", "mcp",
        "--policy", "${workspaceFolder}/examples/mcp/roles.yaml",
        "--role", "manager",
        "--context", "@${workspaceFolder}/examples/mcp/context.json",
        "--db", "sqlite:///${workspaceFolder}/demo.db"
      ]
    }
  }
}
```

For a database with a password, read it from your environment instead of writing it in the file: `"env": { "DATABASE_URL": "${env:SALES_DATABASE_URL}" }`, without `--db`. Cursor also takes `"envFile": "${workspaceFolder}/.env"`. Server logs are in the Output panel (Cmd/Ctrl+Shift+U), under **MCP Logs**.

## 5. VS Code

GitHub Copilot reads MCP servers from `~/.copilot/mcp-config.json` (every workspace) or `.mcp.json` at the root of the workspace, with the same `mcpServers` format. Environment references are written `${NAME}`:

```json
{
  "mcpServers": {
    "sales-db": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "sqlguardllm[mcp]",
        "sqlguardllm", "mcp",
        "--policy", "/Users/you/sqlguardllm/examples/mcp/roles.yaml",
        "--role", "manager",
        "--context", "@/Users/you/sqlguardllm/examples/mcp/context.json"
      ],
      "env": { "DATABASE_URL": "${SALES_DATABASE_URL}" }
    }
  }
}
```

The editor-managed format still works in `.vscode/mcp.json` (VS Code documents it as deprecated with the Local harness, to be removed in February 2027). Its top-level key is `servers`, and it can prompt for the database URL once and store it as a secret:

```json
{
  "inputs": [
    { "type": "promptString", "id": "sales-db-url", "description": "Sales database URL", "password": true }
  ],
  "servers": {
    "sales-db": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "sqlguardllm[mcp]",
        "sqlguardllm", "mcp",
        "--policy", "${workspaceFolder}/examples/mcp/roles.yaml",
        "--role", "manager",
        "--context", "@${workspaceFolder}/examples/mcp/context.json"
      ],
      "env": { "DATABASE_URL": "${input:sales-db-url}" }
    }
  }
}
```

The same format from the command line adds it to your user profile:

```bash
code --add-mcp '{"name":"sales-db","type":"stdio","command":"uvx","args":["--from","sqlguardllm[mcp]","sqlguardllm","mcp","--policy","/Users/you/policy.yaml","--context","@/Users/you/context.json","--db","sqlite:////Users/you/demo.db"]}'
```

Run **MCP: List Servers** and choose **Show Output** to see the server's log.

## Your own database

Any database SQLAlchemy supports can work. Add its driver to the `uvx` command with `--with`, and write the policy's `dialect` for that database. With pipx, install once (`pipx install "sqlguardllm[mcp]"`, then `pipx inject sqlguardllm "psycopg[binary]"`) and use `sqlguardllm` as the `command`.

| Database | `--db` URL | uvx | Policy `dialect` |
|---|---|---|---|
| SQLite | `sqlite:////absolute/path/app.db` | — | `sqlite` |
| PostgreSQL | `postgresql+psycopg://reader@host/db` | `--with "psycopg[binary]"` | `postgres` |
| MySQL, MariaDB | `mysql+pymysql://reader@host/db` | `--with pymysql` | `mysql` |
| SQL Server | `mssql+pyodbc://reader@dsn` | `--with pyodbc` | `tsql` |
| Oracle | `oracle+oracledb://reader@host/?service_name=db` | `--with oracledb` | `oracle` |
| DuckDB | `duckdb:////absolute/path/app.duckdb` | `--with duckdb-engine` | `duckdb` |

For example, `"command": "uvx", "args": ["--with", "psycopg[binary]", "--from", "sqlguardllm[mcp]", "sqlguardllm", "mcp", …]`. The server warns on stderr when the policy writes SQL for a different database than the URL points to. SQLite runs in the test suite and PostgreSQL (psycopg 3 and psycopg2) was tried end to end; the other rows use the same code with their own read-only and timeout settings, described below.

**Connect with a read-only user** that can only read the tables in the policy. For PostgreSQL:

```sql
CREATE ROLE sqlguard_reader LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE sales TO sqlguard_reader;
GRANT USAGE ON SCHEMA public TO sqlguard_reader;
GRANT SELECT ON branches, customers, invoices TO sqlguard_reader;
ALTER ROLE sqlguard_reader SET default_transaction_read_only = on;
ALTER ROLE sqlguard_reader SET statement_timeout = '30s';
```

## How it protects you

- **The model only writes SQL; the guard decides what runs.** Every query goes through `Guard.enforce()`: one `SELECT`, allowlisted tables, no hidden columns, no denied functions, masks and minimum group sizes applied, the user's row filters injected into every read of a filtered table, and the row limit. What runs is SQL regenerated from the checked tree, never the model's string. The [attack gallery](ATTACK_GALLERY.md) lists what that stops.
- **Read-only twice.** The guard rejects writes, and the server also runs each query in a transaction that is always rolled back and, where the database supports it, read-only: `SET TRANSACTION READ ONLY` on PostgreSQL and Oracle, `START TRANSACTION READ ONLY` on MySQL and MariaDB, `PRAGMA query_only` on SQLite. SQL Server has no read-only transactions, so there the database user is what stops writes.
- **Bounded in time and size.** `--timeout` becomes `statement_timeout` on PostgreSQL, `max_execution_time` on MySQL, `max_statement_time` on MariaDB, a progress handler on SQLite, `call_timeout` on Oracle and the query timeout of pyodbc on SQL Server. Other databases rely on the timeout of their database user. At most `max_rows` rows come back (1000 when the policy has no limit).
- **The context comes from you, never from the model.** It is fixed when the server starts (`--context`), and no tool takes it as a parameter, so a prompt injection can't change whose rows it sees. One server process serves one user: start one per person or role. The tools never return the context: `explain_query` shows row filters as written in the policy (`branch_id IN ({user.branches})`) and leaves out the rewritten SQL, which has the user's values bound in it.
- **No secrets in output or logs.** The database URL never appears in tool results, error messages or logs; neither does the context. Database errors come back as their first line (`no such column: nope`), without the SQL. Put a password in the client's `env` block (`DATABASE_URL`), not in `args`, where any process listing shows it.
- **The model only learns what it may use.** `list_tables` and `describe_table` show allowed tables and visible columns only; foreign keys to other tables or hidden columns are left out, and `describe_table` on any other table answers `table_not_allowed`.

What it doesn't do (yet): the optional cost guard that runs the engine's `EXPLAIN` before a query ([design](DESIGN.md#mcp-server)), and several users in one server process; for those, use the library inside your own application ([integrations](INTEGRATIONS.md)). The [threat model](THREAT_MODEL.md) covers the rest.
