// Runnable demo: `node run.mjs` (Node 22.5+, SQLite, no network). Needs `npm install` here first.
//
// With DATABASE_URL set it runs the same queries on PostgreSQL through `pg` instead; create the
// demo tables there first with the SCRIPT of demo-data.mjs (adapted to the dialect, policy dialect "postgres").
import { SqlGuard } from "sqlguardllm";
import { ALLOWED_SQL, CONTEXT, HIDDEN_COLUMN_SQL, POLICY, WRITE_SQL, createDemoDatabase, sqliteRunner } from "./demo-data.mjs";
import { guardedQuery, pgRunner } from "./guarded-tools.mjs";

// Local mode: the embedded engine, no server. For a server use `new SqlGuard({ baseUrl, token })`.
// SQLGUARD_ENGINE_CMD (e.g. "uv run sqlguardllm engine --stdio") runs another engine, such as a source checkout.
const engine = process.env.SQLGUARD_ENGINE_CMD?.split(" ");
const guard = await SqlGuard.local({ policy: process.env.DATABASE_URL ? { ...POLICY, dialect: "postgres" } : POLICY, engine });
let run;
let pool;
if (process.env.DATABASE_URL) {
  const { default: pg } = await import("pg");
  pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
  run = pgRunner(pool);
} else {
  run = sqliteRunner(createDemoDatabase());
}

const query = guardedQuery(guard, run, () => CONTEXT);
for (const sql of [ALLOWED_SQL, HIDDEN_COLUMN_SQL, WRITE_SQL]) {
  console.log(sql, "->", JSON.stringify(await query(sql)));
}
await guard.close();
await pool?.end();
