// Guarded SQL tools for LangChain.js and the Vercel AI SDK, plus recipes for pg, mysql2 and Prisma.
//
// Every query goes through guard.enforce() first. The frameworks are not dependencies of sqlguardllm:
// install the one you use. The context comes from your authenticated user, never from the model.
import { GuardViolation } from "sqlguardllm";

/**
 * Check `sql`, then run it. A rejected query is not thrown at the model: the violation messages come
 * back as text so it can repair its query. Transport failures (SqlGuardError) still throw: fail closed.
 *
 * @param {import("sqlguardllm").SqlGuard} guard created once and reused
 * @param {(sql: string) => unknown | Promise<unknown>} run executes SQL on a read-only database user
 * @param {() => Record<string, unknown> | Promise<Record<string, unknown>>} getContext the signed-in user
 */
export function guardedQuery(guard, run, getContext) {
  return async (sql) => {
    try {
      const safeSql = await guard.enforce(sql, await getContext());
      return { ok: true, rows: await run(safeSql) };
    } catch (e) {
      if (e instanceof GuardViolation) return { ok: false, feedback: e.violations.map((v) => v.message).join("; ") };
      throw e;
    }
  };
}

const DESCRIPTION = "Run one read-only SQL query and return the rows. Rejected queries come back with the reason.";

/** LangChain.js: a `sql_db_query` tool. `tool` and `z` come from `@langchain/core/tools` and `zod`. */
export function langchainSqlTool({ tool, z }, guard, run, getContext) {
  const query = guardedQuery(guard, run, getContext);
  return tool(async ({ sql }) => JSON.stringify(await query(sql)), {
    name: "sql_db_query",
    description: DESCRIPTION,
    schema: z.object({ sql: z.string().describe("A single SELECT statement") }),
  });
}

/** Vercel AI SDK: a `tool()` that checks the SQL and only then runs it. `tool` comes from `ai`; `z` from `zod`. */
export function vercelSqlTool({ tool, z }, guard, run, getContext) {
  const query = guardedQuery(guard, run, getContext);
  return tool({
    description: DESCRIPTION,
    inputSchema: z.object({ sql: z.string().describe("A single SELECT statement") }),
    execute: async ({ sql }) => query(sql),
  });
}

/** pg: `pool` is a `pg.Pool` (or Client) connected as a read-only user. */
export function pgRunner(pool) {
  return async (sql) => (await pool.query(sql)).rows;
}

/** mysql2: `pool` is `mysql2/promise` `createPool(...)` as a read-only user. */
export function mysql2Runner(pool) {
  return async (sql) => (await pool.query(sql))[0];
}

/** Prisma: the one place `$queryRawUnsafe` is acceptable, because the string was just enforced. */
export function prismaRunner(prisma) {
  return (sql) => prisma.$queryRawUnsafe(sql);
}
