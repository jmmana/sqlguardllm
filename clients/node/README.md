# sqlguardllm (Node.js client)

Official TypeScript client for the [sqlguardllm](https://github.com/jmmana/sqlguardllm) SQL firewall. The engine runs in `sqlguardllm serve` (one engine, many clients); this package is a thin, typed HTTP client with **zero runtime dependencies** (global `fetch`: Node 18+, Deno, Bun, edge runtimes). ESM and CJS.

```sh
npm install sqlguardllm
```

## Usage

```ts
import { SqlGuard, GuardViolation } from "sqlguardllm";

const guard = new SqlGuard({ baseUrl: "http://localhost:8080", token: process.env.SQLGUARD_TOKEN });

// context comes from YOUR app (session, JWT), never from the model.
const safeSql = await guard.enforce(modelSql, { tenant_id: session.tenantId });
// or: const r = await guard.check(modelSql, ctx);  // r.ok, r.sql, r.tables, r.appliedFilters, r.violations
```

`enforce` returns the rewritten safe SQL or throws `GuardViolation` (with `.violations`). `explain(sql, ctx)` and `health()` are also available. Options: `baseUrl`, `token?`, `timeoutMs?` (default 5000), `fetch?`.

### pg

```ts
import pg from "pg";
import { SqlGuard, wrap } from "sqlguardllm";
const pool = new pg.Pool();
const guard = new SqlGuard({ baseUrl: "http://localhost:8080" });
const query = wrap(guard, (sql: string) => pool.query(sql), () => ({ tenant_id: currentTenant() }));
const { rows } = await query(modelSql);
```

### mysql2

```ts
import mysql from "mysql2/promise";
const conn = await mysql.createConnection(process.env.DATABASE_URL!);
const query = guard.wrap((sql: string) => conn.query(sql), () => ({ tenant_id: currentTenant() }));
const [rows] = await query(modelSql);
```

## Fail closed

**If the server is unreachable, times out, or answers something unexpected, `check`/`enforce` throw `SqlGuardError` (`code`: `network`, `timeout`, `bad_response`, or the server's error code such as `unauthorized`). They never report `ok`.** Do not catch the error and run the query anyway. A rejected query is not an error: `check` returns `ok: false` with `violations`.

Error messages never include the token or the context.

## Context comes from your app

Row-filter values (tenant, user id) must come from your authenticated session, never from text the model produced. Otherwise the model could pick its own tenant.

## Development

`npm ci && npm run build && npm test` in `clients/node`. Tests run against an in-process fake server that follows the `/v1` contract.
