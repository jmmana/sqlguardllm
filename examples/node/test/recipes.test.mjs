// The Node recipes fed fake model output, on SQLite, without network.
//
// The guard is the real client talking to a stand-in server (a fake `fetch`) that applies the demo
// policy in miniature, so no engine binary is needed. Framework recipes are skipped when the framework
// isn't installed; SQLGUARDLLM_RECIPES=1 turns a missing framework into a failure. PostgreSQL runs
// when DATABASE_URL is set.
import assert from "node:assert/strict";
import { test } from "node:test";
import { SqlGuard } from "sqlguardllm";
import { ALLOWED_SQL, CONTEXT, HIDDEN_COLUMN_SQL, WRITE_SQL, createDemoDatabase, sqliteRunner } from "../demo-data.mjs";
import { guardedQuery, langchainSqlTool, mysql2Runner, pgRunner, prismaRunner, vercelSqlTool } from "../guarded-tools.mjs";

const violation = (code, message) => ({ code, message, detail: "" });

/** Miniature of the demo policy: SELECT only, no customers.email, invoices limited to the user's branches. */
function fakeServer(seen) {
  return async (_url, init) => {
    const { sql, context } = JSON.parse(init.body);
    seen.push({ sql, context });
    const violations = [];
    if (!/^\s*SELECT\b/i.test(sql)) violations.push(violation("write_statement", "only SELECT is allowed"));
    if (/\bemail\b/i.test(sql)) violations.push(violation("hidden_column", "customers.email is a hidden column"));
    const ok = violations.length === 0;
    const branches = context?.user?.branches?.join(", ");
    const safe = /\binvoices\b/i.test(sql) ? `${sql} WHERE branch_id IN (${branches})` : sql;
    const body = { ok, sql: ok ? safe : null, tables: [], applied_filters: [], violations };
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  };
}

function setup() {
  const seen = [];
  const guard = new SqlGuard({ baseUrl: "http://guard.test", fetch: fakeServer(seen) });
  const executed = [];
  const db = createDemoDatabase();
  const run = (sql) => (executed.push(sql), sqliteRunner(db)(sql));
  return { seen, guard, executed, run, query: guardedQuery(guard, run, () => CONTEXT) };
}

async function need(name) {
  try {
    return await import(name);
  } catch (e) {
    if (process.env.SQLGUARDLLM_RECIPES === "1") throw e;
    return null;
  }
}

test("an allowed query runs the rewritten SQL with the user's context", async () => {
  const { seen, executed, query } = setup();
  const res = await query(ALLOWED_SQL);
  assert.deepEqual(res, { ok: true, rows: [{ revenue: 350 }] });
  assert.deepEqual(seen[0].context, CONTEXT);
  assert.equal(executed.length, 1);
  assert.match(executed[0], /branch_id IN \(1, 2\)/);
});

test("rejected queries never reach the database and the model gets the reason", async () => {
  const { executed, query } = setup();
  const hidden = await query(HIDDEN_COLUMN_SQL);
  assert.equal(hidden.ok, false);
  assert.match(hidden.feedback, /customers\.email is a hidden column/);
  assert.equal((await query(WRITE_SQL)).ok, false);
  assert.deepEqual(executed, []);
});

test("an unreachable guard fails closed: it throws and nothing runs", async () => {
  const executed = [];
  const guard = new SqlGuard({ baseUrl: "http://guard.test", fetch: async () => { throw new Error("down"); } });
  const query = guardedQuery(guard, (sql) => executed.push(sql), () => CONTEXT);
  await assert.rejects(query(ALLOWED_SQL), { name: "SqlGuardError", code: "network" });
  assert.deepEqual(executed, []);
});

test("pg, mysql2 and Prisma runners execute only what they are given", async () => {
  const calls = [];
  const rows = [{ revenue: 350 }];
  const pool = { query: async (sql) => (calls.push(sql), { rows }) };
  const my = { query: async (sql) => (calls.push(sql), [rows, []]) };
  const prisma = { $queryRawUnsafe: async (sql) => (calls.push(sql), rows) };
  const { guard } = setup();
  for (const runner of [pgRunner(pool), mysql2Runner(my), prismaRunner(prisma)]) {
    const query = guardedQuery(guard, runner, () => CONTEXT);
    assert.deepEqual(await query(ALLOWED_SQL), { ok: true, rows });
    assert.equal((await query(WRITE_SQL)).ok, false);
  }
  assert.equal(calls.length, 3);
  assert.ok(calls.every((sql) => /branch_id IN \(1, 2\)/.test(sql)));
});

test("LangChain.js tool", async (t) => {
  const core = await need("@langchain/core/tools");
  const zod = await need("zod");
  if (!core || !zod) return t.skip("@langchain/core or zod is not installed");
  const { guard, executed, run } = setup();
  const sqlTool = langchainSqlTool({ tool: core.tool, z: zod.z }, guard, run, () => CONTEXT);
  assert.deepEqual(JSON.parse(await sqlTool.invoke({ sql: ALLOWED_SQL })), { ok: true, rows: [{ revenue: 350 }] });
  assert.equal(JSON.parse(await sqlTool.invoke({ sql: WRITE_SQL })).ok, false);
  assert.equal(executed.length, 1);
});

test("Vercel AI SDK tool", async (t) => {
  const ai = await need("ai");
  const zod = await need("zod");
  if (!ai || !zod) return t.skip("ai or zod is not installed");
  const { guard, executed, run } = setup();
  const sqlTool = vercelSqlTool({ tool: ai.tool, z: zod.z }, guard, run, () => CONTEXT);
  assert.deepEqual(await sqlTool.execute({ sql: ALLOWED_SQL }, { toolCallId: "1", messages: [] }), { ok: true, rows: [{ revenue: 350 }] });
  assert.equal((await sqlTool.execute({ sql: HIDDEN_COLUMN_SQL }, { toolCallId: "2", messages: [] })).ok, false);
  assert.equal(executed.length, 1);
});

test("PostgreSQL through pg", async (t) => {
  const url = process.env.DATABASE_URL;
  if (!url) return t.skip("DATABASE_URL is not set");
  const pg = (await import("pg")).default;
  const pool = new pg.Pool({ connectionString: url });
  try {
    const { guard } = setup();
    const query = guardedQuery(guard, pgRunner(pool), () => CONTEXT);
    const res = await query("SELECT 1 AS one");
    assert.equal(res.ok, true);
    assert.deepEqual(res.rows, [{ one: 1 }]);
    assert.equal((await query(WRITE_SQL)).ok, false);
  } finally {
    await pool.end();
  }
});
