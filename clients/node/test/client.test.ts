import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { SqlGuard, SqlGuardError, GuardViolation, wrap } from "../src/index";

let srv: Server; let url: string; let last: { auth?: string; body?: any } = {};
beforeAll(async () => {
  srv = createServer((req, res) => {
    let b = ""; req.on("data", (c) => (b += c));
    req.on("end", () => {
      const j = (s: number, o: unknown) => { res.writeHead(s, { "content-type": "application/json" }); res.end(typeof o === "string" ? o : JSON.stringify(o)); };
      last = { auth: req.headers.authorization, body: b ? JSON.parse(b) : undefined };
      if (req.url === "/v1/health") return j(200, { status: "ok", version: "1.0" });
      if (req.headers.authorization === "Bearer bad") return j(401, { error: { code: "unauthorized", message: "no" } });
      const sql: string = last.body?.sql ?? "";
      if (sql === "slow") return void setTimeout(() => j(200, {}), 500);
      if (sql === "garbage") return j(200, "<html>");
      if (sql === "liar") return j(200, { ok: true, sql: null, tables: [], applied_filters: [], violations: [] });
      if (req.url === "/v1/explain") return j(200, { ok: true, sql, tables: ["t"], violations: [], steps: [1] });
      if (sql.startsWith("DROP")) return j(200, { ok: false, sql: null, tables: [], applied_filters: [], violations: [{ code: "NOT_SELECT", message: "only select", detail: "d" }] });
      j(200, { ok: true, sql: `${sql} LIMIT 10`, tables: ["t"], applied_filters: ["tenant"], violations: [] });
    });
  });
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
  url = `http://127.0.0.1:${(srv.address() as AddressInfo).port}/`;
});
afterAll(() => srv.close());

describe("SqlGuard", () => {
  const g = () => new SqlGuard({ baseUrl: url, token: "secret", timeoutMs: 200 });
  it("health", async () => expect(await g().health()).toEqual({ status: "ok", version: "1.0" }));
  it("check ok sends token and context", async () => {
    const r = await g().check("SELECT 1", { tenant: 3 });
    expect(r).toEqual({ ok: true, sql: "SELECT 1 LIMIT 10", tables: ["t"], appliedFilters: ["tenant"], violations: [] });
    expect(last.auth).toBe("Bearer secret");
    expect(last.body).toEqual({ sql: "SELECT 1", context: { tenant: 3 } });
  });
  it("context defaults to null", async () => { await g().check("SELECT 1"); expect(last.body.context).toBeNull(); });
  it("rejected is ok=false, enforce throws GuardViolation", async () => {
    expect((await g().check("DROP x")).violations[0]?.code).toBe("NOT_SELECT");
    const e = await g().enforce("DROP x").catch((x) => x);
    expect(e).toBeInstanceOf(GuardViolation);
    expect(e.violations).toHaveLength(1);
  });
  it("enforce returns safe sql", async () => expect(await g().enforce("SELECT 1")).toBe("SELECT 1 LIMIT 10"));
  it("explain", async () => expect((await g().explain("SELECT 1")).steps).toEqual([1]));
  it("401 -> SqlGuardError with code, no token leak", async () => {
    const e = await new SqlGuard({ baseUrl: url, token: "bad" }).check("SELECT 1").catch((x) => x);
    expect(e).toBeInstanceOf(SqlGuardError); expect(e.code).toBe("unauthorized"); expect(e.status).toBe(401);
    expect(e.message).not.toContain("bad");
  });
  it("fails closed: timeout", async () => expect((await g().check("slow").catch((x) => x)).code).toBe("timeout"));
  it("fails closed: garbage and inconsistent bodies", async () => {
    expect((await g().check("garbage").catch((x) => x)).code).toBe("bad_response");
    expect((await g().check("liar").catch((x) => x)).code).toBe("bad_response");
  });
  it("fails closed: unreachable, message has no context", async () => {
    const e = await new SqlGuard({ baseUrl: "http://127.0.0.1:1", timeoutMs: 500 }).check("SELECT 1", { pw: "hunter2" }).catch((x) => x);
    expect(e.code).toBe("network"); expect(e.message).not.toContain("hunter2");
  });
  it("wrap", async () => {
    const run = wrap(g(), async (sql: string, n: number) => `${sql}|${n}`, () => ({ tenant: 1 }));
    expect(await run("SELECT 1", 7)).toBe("SELECT 1 LIMIT 10|7");
    await expect(run("DROP x", 1)).rejects.toBeInstanceOf(GuardViolation);
  });
});
