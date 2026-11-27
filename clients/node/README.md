# sqlguardllm (Node.js client)

Official TypeScript client for the [sqlguardllm](https://github.com/jmmana/sqlguardllm) SQL firewall. The engine runs in `sqlguardllm serve` (one engine, many clients); this package is a thin, typed HTTP client with **zero runtime dependencies** (global `fetch`: Node 18+, Deno, Bun, edge runtimes). ESM and CJS.

> **Today this package needs a running `sqlguardllm serve`.** An embedded engine, so that `npm install sqlguardllm` works with no Python and no server, is planned in [#85](https://github.com/jmmana/sqlguardllm/issues/85).

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

## Embedded engine packages

The engine binary ships as one npm package per platform, `sqlguardllm-engine-<platform>` (`linux-x64`, `linux-arm64`, `macos-arm64`, `macos-x64`, `windows-x64`), declared as `optionalDependencies` with `os`/`cpu` so npm installs only yours. There is no `postinstall` and no download: it works offline, with `--ignore-scripts` and from a lockfile. `resolveEngine()` recomputes the SHA-256 of the engine folder and compares it with the value built into `sqlguardllm`; on any difference, or if the package is missing, it throws `SqlGuardError` (`engine_digest_mismatch`, `engine_not_found`) and nothing is started. On other platforms (including musl Linux such as Alpine) it throws `unsupported_platform`, and the message points to the remote mode (`sqlguardllm serve` and `new SqlGuard({ baseUrl })`).

Maintainers: `python scripts/fetch-engine.py <version> --out engines`, then `npm run build:engines`, which writes the platform packages to `npm/dist/` and the digests to `src/engine-digests.json`. Publish the platform packages first and `sqlguardllm` last, with `npm/dist/optional-dependencies.json` merged into its `optionalDependencies`.

## Local mode

```ts
import { SqlGuard } from "sqlguardllm";

const guard = await SqlGuard.local({ policyPath: "policy.json" });   // or { policy }, or { policy | policyPath, role } for a policy set
try {
  const sql = await guard.enforce("SELECT id FROM invoices", { tenant_id: 7 });
} finally {
  await guard.close();                                              // also: `await using guard = await SqlGuard.local(...)`
}
```

`check`, `enforce`, `explain` and their results are the same as in remote mode. The embedded engine runs as a child process (no server, no Python):

- It starts on the first call, answers `hello`, and must report protocol 1 and the same version (before 1.0, the same minor) as this package; otherwise every call fails.
- Concurrent calls are multiplexed by request id. `timeoutMs` (default 5000) applies to each call; a timeout drops the engine.
- If the engine crashes or times out, pending calls and calls during the backoff (100 ms doubling up to 5 s) throw `SqlGuardError` (`engine_crashed`, `timeout`, `engine_restarting`): fail closed, never `ok`. The next call after the backoff starts a new engine.
- The engine does not keep Node alive and is killed when the process exits. `close()` stops it.
- `policyPath` must be a JSON file. Options: `language` (`en`/`es`), `engine` (command to run instead of the packaged engine).

Types for policies, results and protocol messages (`Policy`, `WireCheckResult`, `WireRequest`...) are generated from `schema/` into `src/generated.ts` (`npm run gen:types`); the unit tests fail when it is stale.

Runtimes: Node 18, 20 and 22, as ESM and CommonJS; Bun best effort. Deno, browsers and edge runtimes cannot start processes: use the remote mode there.

## Development

`npm ci && npm run build && npm test` in `clients/node`. Tests run against an in-process fake server that follows the `/v1` contract.
