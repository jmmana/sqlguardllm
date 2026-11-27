// A stand-in for `sqlguardllm-engine --stdio` used by local.test.ts. argv[2] picks a mode.
import { createInterface } from "node:readline";

const mode = process.argv[2] ?? "ok";
const version = mode === "badversion" ? "9.9.9" : "0.5.0a3";
const out = (m) => process.stdout.write(JSON.stringify(m) + "\n");
const result = (sql) => ({
  ok: !/DROP/.test(sql),
  sql: /DROP/.test(sql) ? null : sql,
  tables: [],
  applied_filters: [],
  violations: /DROP/.test(sql) ? [{ code: "ddl", message: "no", detail: "" }] : [],
});

createInterface({ input: process.stdin }).on("line", (line) => {
  const m = JSON.parse(line);
  if (m.type === "hello") return out({ id: m.id, type: "hello", protocol: 1, engine_version: version, dialects: [] });
  if (m.type === "shutdown") { out({ id: m.id, type: "shutdown" }); process.exit(0); }
  if (m.type === "load_policy") {
    if (!m.policy && !m.policy_set) return out({ id: m.id, type: "error", allowed: false, error: { code: "invalid_policy", message: "x" } });
    return out({ id: m.id, type: "load_policy", handle: "p1" });
  }
  if (m.sql.includes("CRASH")) process.exit(3);
  if (m.sql.includes("HANG")) return;
  const delay = m.sql.includes("SLOW") ? 60 : 0;
  setTimeout(() => out({ id: m.id, type: m.type, result: result(m.sql) }), delay);
});
