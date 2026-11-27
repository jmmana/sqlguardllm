# Recipes

Put sqlguardllm in front of the SQL execution of your stack. Every recipe is a runnable file in [`examples/`](../examples), tested in CI with fake model output against SQLite, without network. No framework is a dependency of sqlguardllm: install the one you use.

| Stack | Recipe | Targets | Integration point |
|---|---|---|---|
| Any DB-API driver | [`generic_wrapper.py`](../examples/generic_wrapper.py) | Python 3.10+ (sqlite3, psycopg, pyodbc, oracledb…) | `guard.wrap(execute)` |
| SQLAlchemy | [`sqlalchemy_wrapper.py`](../examples/sqlalchemy_wrapper.py) | SQLAlchemy 2.x | `guard.enforce()` before `exec_driver_sql()` |
| LangChain | [`langchain_sql.py`](../examples/langchain_sql.py) | langchain-core 1.x, `create_agent` from langchain 1.x | your own `sql_db_query` tool |
| LangChain (community toolkit) | [`langchain_community_sql.py`](../examples/langchain_community_sql.py) | langchain-community 0.4 | `SQLDatabase.run()` |
| LlamaIndex | [`llamaindex_sql.py`](../examples/llamaindex_sql.py) | llama-index-core 0.14 | `SQLDatabase.run_sql()` |
| Vanna | [`vanna_runner.py`](../examples/vanna_runner.py) | vanna 2.0 | a `SqlRunner` for `RunSqlTool` |
| MCP | [docs/MCP.md](MCP.md) | sqlguardllm 0.4 | the built-in server, `sqlguardllm mcp` |
| .NET | [`examples/dotnet`](../examples/dotnet) | net8.0+ (ASP.NET Core, EF Core 8, Dapper, Semantic Kernel, Microsoft.Extensions.AI) | [`AddSqlGuard`, interceptor, filter, `AIFunction`](#net) |

Versions checked on 2026-11-26 with langchain 1.5.2, langchain-core 1.8.5, langchain-community 0.4.2, llama-index-core 0.14.26, vanna 2.0.2 and SQLAlchemy 2.1.9. The [Recipes workflow](../.github/workflows/recipes.yml) runs them against these ranges.

The examples share a demo database and policy, [`examples/demo_data.py`](../examples/demo_data.py): a branch manager who may see branches 1 and 2, with `customers.email` hidden. Run any recipe from the repository root, for example `python examples/generic_wrapper.py`.

Three rules hold in every recipe:

1. **Create the `Guard` once** and reuse it: the policy is compiled when the guard is created.
2. **The context comes from your authenticated user**, never from the model: `{"user": {"branches": [1, 2]}}` fills `{user.branches}` in the row filters.
3. **Return the violation messages to the model** so it can repair its query. They say what was wrong without revealing hidden data, e.g. `customers.email is a hidden column`.

And keep the layers under it: a read-only database user and a statement timeout ([threat model](THREAT_MODEL.md)).

## Generic `execute()` wrapper (DB-API)

Any function whose first argument is SQL can be wrapped. `guard.wrap` checks and rewrites the SQL with the current user's context, and raises `GuardViolation` instead of calling the function when the query isn't allowed.

```python
from sqlguardllm import Guard, GuardViolation, Policy

guard = Guard(Policy.from_file("policy.yaml"))

def execute(sql: str) -> list[tuple]:
    with conn.cursor() as cursor:          # any PEP 249 driver, read-only user
        cursor.execute(sql)
        return cursor.fetchall()

run_sql = guard.wrap(execute, context_fn=lambda: {"user": {"branches": current_user.branch_ids}})

try:
    rows = run_sql(sql_from_model)
except GuardViolation as exc:
    feedback = "; ".join(v.message for v in exc.violations)   # send this back to the model
```

## SQLAlchemy

```python
from sqlalchemy import create_engine

engine = create_engine(DATABASE_URL)      # a read-only database user

def run_sql(sql: str, context) -> list:
    safe_sql = guard.enforce(sql, context)
    with engine.connect() as conn:
        return conn.exec_driver_sql(safe_sql).all()
```

Use `exec_driver_sql()`, not `text()`: `text()` treats `:word` as a bind parameter, even inside a string literal the model wrote. On PostgreSQL you can also open the connection with `conn.execution_options(postgresql_readonly=True)` for one more read-only layer.

## LangChain

LangChain's current [SQL agent tutorial](https://docs.langchain.com/oss/python/langchain/sql-agent) gives `create_agent` its own `sql_db_query` tool. Put the guard inside it and return the violations as the tool's answer:

```python
from langchain.agents import create_agent
from langchain_core.tools import tool

@tool
def sql_db_query(query: str) -> str:
    """Run a read-only SQL query and return the rows. If the query is rejected, the
    error says why: rewrite the query and try again."""
    try:
        safe_sql = guard.enforce(query, current_user_context())
    except GuardViolation as exc:
        return f"Error: {exc}"
    return repr(execute(safe_sql))

agent = create_agent(model, [sql_db_query, ...])
```

The test drives a real `create_agent` loop with a fake model that first asks for `customers.email`, gets `Error: customers.email is a hidden column`, and repairs its query. Don't give the agent a schema tool that reads sample rows: they'd show hidden columns and other users' rows.

### langchain-community's `SQLDatabaseToolkit`

For existing code on `SQLDatabase`, `SQLDatabaseToolkit` or `QuerySQLDatabaseTool`. langchain-community 0.4 was [archived in 2026](https://github.com/langchain-ai/langchain-community/issues/674), so prefer the tool above for new code. Subclass `SQLDatabase` so every tool that runs SQL goes through the guard:

```python
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

class GuardedSQLDatabase(SQLDatabase):
    def __init__(self, *args, guard, context_fn, **kwargs):
        super().__init__(*args, **kwargs)
        self.guard, self.context_fn = guard, context_fn

    def run(self, command, *args, **kwargs):
        if not isinstance(command, str):            # a SQLAlchemy statement can't be checked
            raise TypeError("GuardedSQLDatabase only runs SQL strings")
        return super().run(self.guard.enforce(command, self.context_fn()), *args, **kwargs)

    def run_no_throw(self, command, *args, **kwargs):
        try:
            return super().run_no_throw(command, *args, **kwargs)
        except GuardViolation as exc:
            return f"Error: {exc}"

db = GuardedSQLDatabase.from_uri(
    DATABASE_URL, guard=guard, context_fn=current_user_context,
    include_tables=sorted(policy.tables), sample_rows_in_table_info=0,
)
agent = create_agent(model, SQLDatabaseToolkit(db=db, llm=model).get_tools())
```

`sample_rows_in_table_info=0` matters: `sql_db_schema` reads sample rows straight from the tables, outside `run()`, and would show hidden columns and rows of other users to the model.

## LlamaIndex

`NLSQLTableQueryEngine`, `SQLTableRetrieverQueryEngine` and `NLSQLRetriever` all execute through `SQLDatabase.run_sql()`:

```python
from llama_index.core import SQLDatabase
from llama_index.core.query_engine import NLSQLTableQueryEngine

class GuardedSQLDatabase(SQLDatabase):
    def __init__(self, *args, guard, context_fn, **kwargs):
        super().__init__(*args, **kwargs)
        self.guard, self.context_fn = guard, context_fn

    def run_sql(self, command: str):
        return super().run_sql(self.guard.enforce(command, self.context_fn()))

db = GuardedSQLDatabase(engine, guard=guard, context_fn=current_user_context,
                        include_tables=sorted(policy.tables), sample_rows_in_table_info=0)
query_engine = NLSQLTableQueryEngine(sql_database=db, tables=sorted(policy.tables), llm=llm)
```

A rejected query becomes the answer `Error: <reasons>` (the engines' default `handle_sql_errors=True`). Don't pass `schema=` to `SQLDatabase`: LlamaIndex then edits the SQL after the guard checked it. Use the policy's `default_schema` instead.

## Vanna

Vanna 2.0 runs SQL through a `SqlRunner` given to `RunSqlTool`. Wrap the runner you already use; the row-filter context comes from the user your `UserResolver` authenticated:

```python
from vanna.capabilities.sql_runner import RunSqlToolArgs, SqlRunner
from vanna.integrations.sqlite import SqliteRunner
from vanna.tools import RunSqlTool

class GuardedSqlRunner(SqlRunner):
    def __init__(self, runner: SqlRunner, guard):
        self.runner, self.guard = runner, guard

    async def run_sql(self, args, context):
        user = {"id": context.user.id, **context.user.metadata}   # e.g. {"branches": [1, 2]}
        safe_sql = self.guard.enforce(args.sql, {"user": user})
        return await self.runner.run_sql(RunSqlToolArgs(sql=safe_sql), context)

tools.register_local_tool(
    RunSqlTool(sql_runner=GuardedSqlRunner(SqliteRunner("sales.db"), guard)),
    access_groups=["user"],
)
```

`RunSqlTool` returns a rejected query to the model as `Error executing query: <reasons>`. Without the guard, Vanna's built-in runners commit whatever isn't a `SELECT`. Vanna 0.x (`vanna.legacy`): wrap the function it executes with, `vn.run_sql = guard.wrap(vn.run_sql, context_fn)`, after `vn.connect_to_…()`.

## Node.js

The Node recipes live in [`examples/node/`](../examples/node) and use the [`sqlguardllm` npm package](../clients/node/README.md), in local mode (the embedded engine, no server) or remote mode. They need Node 22.5+ for the SQLite demo. Run `npm install` in that folder, then `npm start` (SQLite) or `DATABASE_URL=postgres://… npm start` (PostgreSQL through `pg`). Tests: `npm test`; frameworks that aren't installed are skipped (`SQLGUARDLLM_RECIPES=1` makes that a failure) and the PostgreSQL test runs only with `DATABASE_URL`.

Everything is in [`guarded-tools.mjs`](../examples/node/guarded-tools.mjs). `guardedQuery(guard, run, getContext)` calls `guard.enforce()` and only then `run()`. A rejected query comes back as `{ ok: false, feedback }` for the model to repair; a failure to reach the guard throws, so nothing runs (fail closed).

```js
import { SqlGuard } from "sqlguardllm";
import { guardedQuery, pgRunner } from "./guarded-tools.mjs";

const guard = await SqlGuard.local({ policy });            // once; or new SqlGuard({ baseUrl, token })
const query = guardedQuery(guard, pgRunner(pool), () => ({ user: { branches: user.branchIds } }));
```

| Stack | Helper | Notes |
|---|---|---|
| LangChain.js | `langchainSqlTool({ tool, z }, guard, run, getContext)` | a `sql_db_query` tool for `createAgent` / LangGraph; `tool` from `@langchain/core/tools` |
| Vercel AI SDK | `vercelSqlTool({ tool, z }, guard, run, getContext)` | `tool()` from `ai` 5; pass it in `generateText({ tools })` |
| `pg` | `pgRunner(pool)` | `pool.query(sql)` as a read-only user |
| `mysql2` | `mysql2Runner(pool)` | `mysql2/promise`; `pool.query`, not `execute`, since the SQL is already final |
| Prisma | `prismaRunner(prisma)` | `$queryRawUnsafe(sql)` is safe here only because the string was just enforced; never pass it model output directly |

## MCP

sqlguardllm ships its own MCP server, so Claude Desktop, Cursor, VS Code or any MCP client get a database tool that can't write, see hidden data or escape row filters:

```bash
uvx --from "sqlguardllm[mcp]" sqlguardllm mcp --policy policy.yaml --db "$DATABASE_URL"
```

Client configuration, roles and the user context are in [docs/MCP.md](MCP.md). Writing your own MCP server instead? Call `guard.enforce()` in its query tool with the context of the authenticated caller, as in the generic wrapper.

## .NET

Three NuGet packages on top of `SqlGuardLlm`; [`examples/dotnet`](../examples/dotnet) runs all of them against SQLite, tested with a fake guard (no server, no network).

```csharp
// ASP.NET Core: options bound from configuration ("SqlGuard": { "BaseUrl": "...", "Token": "..." })
builder.Services.AddSqlGuard(builder.Configuration.GetSection("SqlGuard"));
builder.Services.AddSqlGuardContext(sp => new Dictionary<string, object?> { ["tenant_id"] = sp.GetRequiredService<ICurrentUser>().TenantId });
```

| Integration | Package | How |
|---|---|---|
| Dapper / ADO.NET | `SqlGuardLlm.Extensions` | `conn.QueryGuardedAsync<T>(guard, sql)`, `command.GuardAsync(guard)`, `conn.CreateGuardedCommandAsync(guard, sql)` |
| EF Core | `SqlGuardLlm.EntityFrameworkCore` | `options.UseSqlGuard(guard)`: `FromSql`, `SqlQuery` and `ExecuteSql` go through the guard and run the rewritten SQL; LINQ and `SaveChanges` are untouched unless `EnforceAll = true` |
| Microsoft.Extensions.AI | `SqlGuardLlm.Extensions` | `new GuardedSqlFunction(guard, (sql, ct) => run(sql), context)`: an `AIFunction` that runs only guard-approved SQL and returns the violations to the model |
| Semantic Kernel | `SqlGuardLlm.SemanticKernel` | `kernel.FunctionInvocationFilters.Add(new SqlGuardFunctionFilter(guard))`: checks the `sql` argument of any function and rewrites it |

A rejected query never runs; a guard failure (server down, timeout) throws `SqlGuardException` and nothing runs either. The EF Core interceptor's synchronous paths block on the guard call.

---

Using sqlguardllm with another stack? Open a PR with a recipe in `examples/` and a test in `tests/test_examples.py`.
