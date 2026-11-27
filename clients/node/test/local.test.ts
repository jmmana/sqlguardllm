import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { afterAll, describe, expect, it } from "vitest";
import { SqlGuard, SqlGuardError, GuardViolation } from "../src/index";
import { compatibleVersion } from "../src/local";

const fake = join(__dirname, "fake-engine.mjs");
const tmp = mkdtempSync(join(tmpdir(), "sqlguard-local-"));
afterAll(() => rmSync(tmp, { recursive: true, force: true }));

const policy = { dialect: "postgres", tables: ["t"] };
const local = (mode = "ok", extra: Record<string, unknown> = {}) =>
  SqlGuard.local({ policy, engine: [process.execPath, fake, mode], ...extra });
const code = async (p: Promise<unknown>) => (await p.then(() => undefined, (e) => e)) as SqlGuardError | undefined;

describe("generated types", () => {
  it("are up to date with schema/", () => {
    execFileSync(process.execPath, [join(__dirname, "../scripts/gen-types.mjs"), "--check"], { stdio: "pipe" });
  });
});

describe("version rule", () => {
  it("matches major, and minor before 1.0", () => {
    expect(compatibleVersion("0.5.0a3", "0.5.0-alpha.3")).toBe(true);
    expect(compatibleVersion("0.5.9", "0.5.0")).toBe(true);
    expect(compatibleVersion("0.6.0", "0.5.0")).toBe(false);
    expect(compatibleVersion("1.2.0", "1.0.0")).toBe(true);
    expect(compatibleVersion("2.0.0", "1.0.0")).toBe(false);
    expect(compatibleVersion(undefined, "0.5.0")).toBe(false);
  });
});

describe("local mode", () => {
  it("checks, enforces and explains like remote mode", async () => {
    const g = await local();
    const r = await g.check("SELECT 1");
    expect(r).toMatchObject({ ok: true, sql: "SELECT 1", violations: [] });
    expect(await g.enforce("SELECT 1")).toBe("SELECT 1");
    const bad = await g.check("DROP TABLE t");
    expect(bad.ok).toBe(false);
    await expect(g.enforce("DROP TABLE t")).rejects.toBeInstanceOf(GuardViolation);
    expect(await g.explain("SELECT 1")).toMatchObject({ ok: true });
    await g.close();
  });

  it("multiplexes concurrent calls by id", async () => {
    const g = await local();
    const rs = await Promise.all([
      g.check("SELECT SLOW"),
      g.check("SELECT 2"),
      g.check("DROP x"),
      g.check("SELECT 4"),
    ]);
    expect(rs.map((r) => r.sql)).toEqual(["SELECT SLOW", "SELECT 2", null, "SELECT 4"]);
    await g.close();
  });

  it("refuses an engine of another version", async () => {
    const g = await local("badversion");
    expect((await code(g.check("SELECT 1")))?.code).toBe("engine_version_mismatch");
    await g.close();
  });

  it("times out a silent engine and recovers after the backoff", async () => {
    const g = await local("ok", { timeoutMs: 300 });
    expect((await g.check("SELECT 1")).ok).toBe(true);
    expect((await code(g.check("SELECT HANG")))?.code).toBe("timeout");
    await new Promise((r) => setTimeout(r, 250));
    expect((await g.check("SELECT 2")).ok).toBe(true);
    await g.close();
  });

  it("fails closed when the engine crashes, then restarts with backoff", async () => {
    const g = await local();
    expect((await g.check("SELECT 1")).ok).toBe(true);
    const [a, b] = await Promise.all([code(g.check("SELECT CRASH")), code(g.check("SELECT HANG"))]);
    expect(a?.code).toBe("engine_crashed");
    expect(b?.code).toBe("engine_crashed");
    // Inside the backoff window new calls fail closed at once.
    expect((await code(g.check("SELECT 1")))?.code).toBe("engine_restarting");
    await new Promise((r) => setTimeout(r, 250));
    expect((await g.check("SELECT 1")).ok).toBe(true);
    await g.close();
  });

  it("fails closed when the engine cannot start", async () => {
    const g = await SqlGuard.local({ policy, engine: join(tmp, "missing-engine") });
    expect((await code(g.check("SELECT 1")))?.code).toBe("engine_crashed");
    await g.close();
  });

  it("reports an invalid policy as an error", async () => {
    const g = await SqlGuard.local({ policy: [] as never, engine: [process.execPath, fake] });
    expect((await code(g.check("SELECT 1")))?.code).toBe("invalid_policy");
  });

  it("loads policyPath and sends policy sets with a role", async () => {
    const file = join(tmp, "policy.json");
    writeFileSync(file, JSON.stringify(policy));
    const g = await SqlGuard.local({ policyPath: file, engine: [process.execPath, fake] });
    expect((await g.check("SELECT 1")).ok).toBe(true);
    await g.close();
    const set = await SqlGuard.local({ policy: { roles: {} }, role: "analyst", engine: [process.execPath, fake] });
    expect((await set.check("SELECT 1")).ok).toBe(true);
    await set.close();
  });

  it("needs exactly one of policy and policyPath", async () => {
    await expect(SqlGuard.local({} as never)).rejects.toBeInstanceOf(TypeError);
  });

  it("rejects calls after close()", async () => {
    const g = await local();
    await g.check("SELECT 1");
    await g.close();
    await g.close();
    expect((await code(g.check("SELECT 1")))?.code).toBe("closed");
  });

  it("supports Symbol.asyncDispose", async () => {
    const g = await local();
    await g.check("SELECT 1");
    const dispose = (g as unknown as Record<symbol, () => Promise<void>>)[Symbol.asyncDispose ?? Symbol.for("Symbol.asyncDispose")]!;
    await dispose.call(g);
    expect((await code(g.check("SELECT 1")))?.code).toBe("closed");
  });

  it("does not keep the process alive when close() is forgotten", () => {
    const script = `
      const { SqlGuard } = await import(${JSON.stringify(join(__dirname, "../dist/index.js"))});
      const g = await SqlGuard.local({ policy: {}, engine: [process.execPath, ${JSON.stringify(fake)}] });
      console.log((await g.check("SELECT 1")).ok);`;
    const file = join(tmp, "exit.mjs");
    writeFileSync(file, script);
    let built = true;
    try { execFileSync(process.execPath, ["-e", `require("fs").accessSync(${JSON.stringify(join(__dirname, "../dist/index.js"))})`]); } catch { built = false; }
    if (!built) return; // needs `npm run build`; CI builds first
    const out = execFileSync(process.execPath, [file], { stdio: "pipe", timeout: 20000 }).toString();
    expect(out.trim()).toBe("true");
  });

  it("a program that ends with a top-level `await guard.close()` finishes normally (no unsettled await)", () => {
    const script = `
      const { SqlGuard } = await import(${JSON.stringify(join(__dirname, "../dist/index.js"))});
      const g = await SqlGuard.local({ policy: {}, engine: [process.execPath, ${JSON.stringify(fake)}] });
      console.log((await g.check("SELECT 1")).ok);
      await g.close();
      console.log("closed");`;
    const file = join(tmp, "close.mjs");
    writeFileSync(file, script);
    let built = true;
    try { execFileSync(process.execPath, ["-e", `require("fs").accessSync(${JSON.stringify(join(__dirname, "../dist/index.js"))})`]); } catch { built = false; }
    if (!built) return; // needs `npm run build`; CI builds first
    const out = execFileSync(process.execPath, [file], { stdio: "pipe", timeout: 20000 }).toString();
    expect(out.trim().split("\n")).toEqual(["true", "closed"]);
  });
});
