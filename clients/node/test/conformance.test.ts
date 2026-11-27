// Runs conformance/cases.json (the attack corpus) against a real `sqlguardllm serve`.
// Skips when no server executable exists, unless SQLGUARD_REQUIRE_CONFORMANCE=1. See docs/CONFORMANCE.md.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SqlGuard, resolveEngine } from "../src/index";

const root = resolve(__dirname, "../../..");
const required = process.env.SQLGUARD_REQUIRE_CONFORMANCE === "1";
// SQLGUARD_SERVER_CMD overrides the launcher, e.g. "sqlguardllm" or "python -m sqlguardllm".
const launcher = (process.env.SQLGUARD_SERVER_CMD ?? `uv run --project ${root} --extra serve sqlguardllm`).split(" ");
const available = spawnSync(launcher[0]!, [...launcher.slice(1), "--version"], { cwd: root }).status === 0;
if (!available && required) throw new Error(`SQLGUARD_REQUIRE_CONFORMANCE=1 but "${launcher.join(" ")}" does not run`);
if (!available) console.warn(`conformance skipped: "${launcher.join(" ")}" is not available (set SQLGUARD_REQUIRE_CONFORMANCE=1 to fail instead)`);

interface Case { id: string; sql: string; context: Record<string, unknown>; expect: string; codes: string[]; expected: { ok: boolean; sql: string | null; codes: string[] } }
interface Group { name: string; policy: unknown; cases: Case[] }
const groups: Group[] = JSON.parse(readFileSync(join(root, "conformance/cases.json"), "utf8")).groups;
const dir = mkdtempSync(join(tmpdir(), "sqlguard-conf-"));

function start(group: Group): Promise<{ proc: ChildProcess; url: string }> {
  const file = join(dir, `${group.name}.json`);
  writeFileSync(file, JSON.stringify(group.policy));
  const proc = spawn(launcher[0]!, [...launcher.slice(1), "serve", "--policy", file, "--port", "0"], { cwd: root });
  return new Promise((ok, fail) => {
    let err = "";
    proc.stderr!.on("data", (c) => {
      err += c;
      const m = /listening on (http:\/\/\S+)/.exec(err);
      if (m) ok({ proc, url: m[1]! });
    });
    proc.on("exit", (code) => fail(new Error(`server exited ${code}: ${err}`)));
  });
}

// Local mode: SQLGUARD_ENGINE_CMD is the engine command (e.g. "uv run sqlguardllm engine --stdio"); without it the
// packaged platform engine is used, if installed.
function localEngine(): string[] | undefined {
  if (process.env.SQLGUARD_ENGINE_CMD) return process.env.SQLGUARD_ENGINE_CMD.split(" ");
  try {
    return [resolveEngine(), "--stdio"];
  } catch {
    // No packaged engine: use the Python engine through the same launcher as the server.
    return available ? [...launcher, "engine", "--stdio"] : undefined;
  }
}
const engine = localEngine();
if (!engine && required) throw new Error("SQLGUARD_REQUIRE_CONFORMANCE=1 but no local engine is available");

describe.skipIf(!engine)("conformance (local mode)", () => {
  for (const group of groups) {
    it(`${group.name} (${group.cases.length} cases)`, async () => {
      const client = await SqlGuard.local({ policy: group.policy as Record<string, unknown>, engine: engine! });
      try {
        for (const c of group.cases) {
          const r = await client.check(c.sql, c.context);
          const codes = [...new Set(r.violations.map((v) => v.code))].sort();
          expect({ ok: r.ok, sql: r.sql, codes }, c.id).toEqual(c.expected);
          expect(r.ok, c.id).toBe(c.expect === "allow");
        }
      } finally {
        await client.close();
      }
    }, 60_000);
  }
});

describe.skipIf(!available)("conformance", () => {
  for (const group of groups) {
    it(`${group.name} (${group.cases.length} cases)`, async () => {
      const { proc, url } = await start(group);
      try {
        const client = new SqlGuard({ baseUrl: url });
        for (const c of group.cases) {
          const r = await client.check(c.sql, c.context);
          const codes = [...new Set(r.violations.map((v) => v.code))].sort();
          expect({ ok: r.ok, sql: r.sql, codes }, c.id).toEqual(c.expected);
          expect(r.ok, c.id).toBe(c.expect === "allow");
        }
      } finally {
        proc.removeAllListeners("exit");
        proc.kill();
      }
    }, 60_000);
  }
});
