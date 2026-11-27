export { detectPlatform, digestTree, resolveEngine } from "./engine";
export type { EngineEnv, PlatformInfo, ResolveEngineOptions } from "./engine";
import { LocalEngine, type LocalOptions } from "./local";
export type { LocalOptions } from "./local";
export type * from "./generated";

export type Fetch =(input: string, init?: RequestInit) => Promise<Response>;

export interface Violation { code: string; message: string; detail: string }
export interface CheckResult {
  ok: boolean;
  sql: string | null;
  tables: string[];
  appliedFilters: string[];
  violations: Violation[];
}
export interface Explanation extends Record<string, unknown> {
  ok?: boolean;
  sql?: string | null;
  tables?: string[];
  violations?: Violation[];
}
export interface SqlGuardOptions {
  baseUrl: string;
  token?: string;
  /** Per-request timeout in ms. Default 5000. */
  timeoutMs?: number;
  fetch?: Fetch;
}
export type Context = Record<string, unknown>;

export type SqlGuardErrorCode =
  | "network" | "timeout" | "bad_response" | "http_error"
  | (string & {});

/** Transport or protocol failure. Never contains the token or the context. */
export class SqlGuardError extends Error {
  readonly code: string;
  readonly status?: number;
  constructor(code: string, message: string, status?: number) {
    super(message);
    this.name = "SqlGuardError";
    this.code = code;
    if (status !== undefined) this.status = status;
  }
}

/** Thrown by enforce() when the query is rejected. */
export class GuardViolation extends Error {
  readonly violations: Violation[];
  constructor(violations: Violation[]) {
    super(violations.length ? violations.map((v) => `${v.code}: ${v.message}`).join("; ") : "query rejected");
    this.name = "GuardViolation";
    this.violations = violations;
  }
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const isStrArr = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
const bad = (what: string) => new SqlGuardError("bad_response", `unexpected server response: ${what}`);

function parseViolations(v: unknown): Violation[] {
  if (!Array.isArray(v)) throw bad("violations");
  return v.map((x) => {
    if (!isObj(x) || typeof x.code !== "string" || typeof x.message !== "string") throw bad("violation");
    return { code: x.code, message: x.message, detail: typeof x.detail === "string" ? x.detail : "" };
  });
}

function parseCheck(d: unknown): CheckResult {
  if (!isObj(d) || typeof d.ok !== "boolean") throw bad("missing ok");
  if (!(d.sql === null || typeof d.sql === "string")) throw bad("sql");
  if (!isStrArr(d.tables)) throw bad("tables");
  const filters = d.applied_filters ?? [];
  if (!isStrArr(filters)) throw bad("applied_filters");
  const violations = parseViolations(d.violations);
  // Fail closed on inconsistent answers.
  if (d.ok && (d.sql === null || violations.length > 0)) throw bad("inconsistent ok result");
  return { ok: d.ok, sql: d.sql, tables: d.tables, appliedFilters: filters, violations };
}

// Symbol.asyncDispose is missing before Node 20: use the registered symbol so `await using` works where it exists.
const asyncDispose: typeof Symbol.asyncDispose = (Symbol as { asyncDispose?: symbol }).asyncDispose as typeof Symbol.asyncDispose
  ?? (Symbol.for("Symbol.asyncDispose") as typeof Symbol.asyncDispose);

export class SqlGuard {
  private readonly baseUrl: string = "";
  private readonly token: string | undefined;
  private readonly timeoutMs: number = 0;
  private readonly fetchFn: Fetch = () => Promise.reject(new SqlGuardError("network", "no HTTP transport"));
  private engine: LocalEngine | undefined;

  /**
   * Local mode: runs the embedded engine as a child process (no server, no Python). The engine
   * starts on the first call. Same API and results as remote mode; failures throw SqlGuardError.
   */
  static async local(opts: LocalOptions): Promise<SqlGuard> {
    return new SqlGuard(new LocalEngine(opts));
  }

  constructor(opts: SqlGuardOptions);
  /** @internal */
  constructor(engine: LocalEngine);
  constructor(opts: SqlGuardOptions | LocalEngine) {
    if (opts instanceof LocalEngine) {
      this.engine = opts;
      return;
    }
    if (!opts || typeof opts.baseUrl !== "string" || !opts.baseUrl) throw new TypeError("baseUrl is required");
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.token = opts.token;
    this.timeoutMs = opts.timeoutMs ?? 5000;
    const f = opts.fetch ?? (globalThis.fetch as Fetch | undefined);
    if (!f) throw new TypeError("no global fetch; pass opts.fetch");
    this.fetchFn = opts.fetch ? f : (i, init) => (globalThis.fetch as Fetch)(i, init);
  }

  private async request(method: "GET" | "POST", path: string, body?: unknown): Promise<unknown> {
    const headers: Record<string, string> = { accept: "application/json" };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (this.token) headers.authorization = `Bearer ${this.token}`;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), this.timeoutMs);
    try {
      let res: Response;
      try {
        const init: RequestInit = { method, headers, signal: ctl.signal };
        if (body !== undefined) init.body = JSON.stringify(body);
        res = await this.fetchFn(this.baseUrl + path, init);
      } catch (e) {
        if (ctl.signal.aborted) throw new SqlGuardError("timeout", `request timed out after ${this.timeoutMs} ms`);
        // Do not forward e.message: it could echo request details.
        throw new SqlGuardError("network", `cannot reach the sqlguardllm server (${(e as Error)?.name ?? "error"})`);
      }
      let text: string;
      try {
        text = await res.text();
      } catch {
        if (ctl.signal.aborted) throw new SqlGuardError("timeout", `request timed out after ${this.timeoutMs} ms`);
        throw new SqlGuardError("network", "connection lost while reading the response");
      }
      let data: unknown;
      try { data = JSON.parse(text); } catch { data = undefined; }
      if (!res.ok) {
        const err = isObj(data) && isObj(data.error) ? data.error : undefined;
        const code = typeof err?.code === "string" ? err.code : "http_error";
        const msg = typeof err?.message === "string" ? err.message : `HTTP ${res.status}`;
        throw new SqlGuardError(code, `server error ${res.status}: ${msg}`, res.status);
      }
      if (data === undefined) throw bad("not JSON");
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Check a query. FAIL CLOSED: if the server is unreachable, times out or
   * answers something unexpected this THROWS SqlGuardError; it never returns ok=true.
   * A rejected query is not an error: it returns ok=false.
   */
  async check(sql: string, context?: Context | null): Promise<CheckResult> {
    const raw = this.engine
      ? await this.engine.call("check", sql, context ?? null)
      : await this.request("POST", "/v1/check", { sql, context: context ?? null });
    return parseCheck(raw);
  }

  /** Returns the safe (rewritten) SQL, or throws GuardViolation. */
  async enforce(sql: string, context?: Context | null): Promise<string> {
    const r = await this.check(sql, context);
    if (!r.ok || r.sql === null) throw new GuardViolation(r.violations);
    return r.sql;
  }

  async explain(sql: string, context?: Context | null): Promise<Explanation> {
    const d = this.engine
      ? await this.engine.call("explain", sql, context ?? null)
      : await this.request("POST", "/v1/explain", { sql, context: context ?? null });
    if (!isObj(d)) throw bad("explain");
    return d as Explanation;
  }

  /** Local mode: stop the engine; later calls fail closed. Remote mode: nothing to release. */
  async close(): Promise<void> {
    await this.engine?.close();
  }

  async [asyncDispose](): Promise<void> {
    await this.close();
  }

  async health(): Promise<{ status: string; version: string }> {
    if (this.engine) return { status: "ok", version: await this.engine.version() };
    const d = await this.request("GET", "/v1/health");
    if (!isObj(d) || d.status !== "ok" || typeof d.version !== "string") throw bad("health");
    return { status: d.status, version: d.version };
  }

  /** Wrap a query executor: the SQL is enforced first, then passed to `execute`. */
  wrap<A extends unknown[], R>(
    execute: (sql: string, ...rest: A) => R | Promise<R>,
    contextFn?: () => Context | null | undefined | Promise<Context | null | undefined>,
  ): (sql: string, ...rest: A) => Promise<R> {
    return wrap(this, execute, contextFn);
  }
}

/** Like Python's `guard.wrap`. The context comes from `contextFn`, i.e. your app. */
export function wrap<A extends unknown[], R>(
  guard: SqlGuard,
  execute: (sql: string, ...rest: A) => R | Promise<R>,
  contextFn?: () => Context | null | undefined | Promise<Context | null | undefined>,
): (sql: string, ...rest: A) => Promise<R> {
  return async (sql, ...rest) => {
    const ctx = contextFn ? await contextFn() : undefined;
    return execute(await guard.enforce(sql, ctx), ...rest);
  };
}
