import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { StringDecoder } from "node:string_decoder";
import pkg from "../package.json";
import { resolveEngine } from "./engine";
import type { Policy, WireRequest } from "./generated";
import { SqlGuardError } from "./index";

const PROTOCOL = 1;
const MAX_MESSAGE_BYTES = 1024 * 1024;
const BACKOFF_START_MS = 100;
const BACKOFF_MAX_MS = 5000;

export interface LocalOptions {
  /** A policy object (`policy`) or a policy set (`policy` plus `role`). */
  policy?: Policy | Record<string, unknown>;
  /** A JSON file with a policy, or with a policy set when `role` is given. */
  policyPath?: string;
  /** Role of the policy set. */
  role?: string;
  /** Language of the violation messages. */
  language?: "en" | "es";
  /** Per-call timeout in ms. Default 5000. It also bounds the start of the engine. */
  timeoutMs?: number;
  /** Engine command and arguments, replacing the packaged engine and its `--stdio` flag. */
  engine?: string | string[];
}

type Pending = { resolve: (m: Record<string, unknown>) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };

const fail = (code: string, message: string) => new SqlGuardError(code, message);

/** Same major version; before 1.0 the same minor too ("0.5.0a3" and "0.5.0-alpha.3" are both 0.5). */
export function compatibleVersion(engine: unknown, client: string): boolean {
  const parse = (v: unknown) => (typeof v === "string" ? /^(\d+)\.(\d+)/.exec(v) : null);
  const e = parse(engine);
  const c = parse(client);
  if (!e || !c) return false;
  return e[1] === c[1] && (e[1] !== "0" || e[2] === c[2]);
}

/**
 * One embedded engine child process speaking the stdio protocol. Starts on the first call, answers
 * concurrent calls by request id and fails closed: any problem rejects the call, never allows it.
 */
export class LocalEngine {
  private readonly timeoutMs: number;
  private readonly load: () => Record<string, unknown>;
  private readonly command: () => [string, string[]];
  private proc: ChildProcess | undefined;
  private starting: Promise<string> | undefined;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private failures = 0;
  private retryAt = 0;
  private closed = false;
  private readonly onExit = () => { this.proc?.kill(); };

  constructor(opts: LocalOptions) {
    if (!opts || (opts.policy === undefined) === (opts.policyPath === undefined)) {
      throw new TypeError("give exactly one of policy and policyPath");
    }
    this.timeoutMs = opts.timeoutMs ?? 5000;
    const { policy, policyPath, role, language } = opts;
    this.load = () => {
      let doc: unknown = policy;
      if (policyPath !== undefined) {
        try {
          doc = JSON.parse(readFileSync(policyPath, "utf8"));
        } catch {
          throw fail("invalid_policy", `cannot read ${policyPath} as a JSON policy`);
        }
      }
      if (typeof doc !== "object" || doc === null || Array.isArray(doc)) throw fail("invalid_policy", "the policy must be an object");
      const body = role === undefined ? { policy: doc } : { policy_set: doc, role };
      return language ? { ...body, language } : body;
    };
    const engine = opts.engine;
    this.command = () => {
      if (engine === undefined) return [resolveEngine(), ["--stdio"]];
      const [cmd, ...args] = typeof engine === "string" ? [engine, "--stdio"] : engine;
      if (!cmd) throw new TypeError("engine must not be empty");
      return [cmd, args];
    };
  }

  /** Resolves with the policy handle once the engine is up, checked and loaded. */
  private ready(): Promise<string> {
    if (this.closed) return Promise.reject(fail("closed", "the engine was closed"));
    if (this.starting) return this.starting;
    const wait = this.retryAt - Date.now();
    if (wait > 0) return Promise.reject(fail("engine_restarting", `the engine crashed; retrying in ${wait} ms`));
    const starting = this.start().catch((e) => {
      this.kill(e instanceof Error ? e : fail("engine_error", "engine failed to start"));
      throw e;
    });
    this.starting = starting;
    starting.then(() => { if (this.starting === starting) this.failures = 0; }, () => {});
    return starting;
  }

  private async start(): Promise<string> {
    const [cmd, args] = this.command();
    const body = this.load();
    const proc = spawn(cmd, args, { stdio: ["pipe", "pipe", "ignore"], windowsHide: true });
    this.proc = proc;
    const decoder = new StringDecoder("utf8");
    let buf = "";
    proc.stdout!.on("data", (chunk: Buffer) => {
      buf += decoder.write(chunk);
      let i: number;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (this.proc === proc) this.dispatch(line);
      }
      if (buf.length > MAX_MESSAGE_BYTES) this.crashed(proc, "the engine sent an oversized message");
    });
    proc.stdin!.on("error", () => {});
    proc.on("error", () => this.crashed(proc, "cannot start the engine"));
    proc.on("exit", () => this.crashed(proc, "the engine exited"));
    // Do not keep the application alive for an idle engine; a pending call keeps its own timer.
    proc.unref();
    (proc.stdin as unknown as { unref?: () => void }).unref?.();
    (proc.stdout as unknown as { unref?: () => void }).unref?.();
    process.once("exit", this.onExit);

    const hello = await this.send({ type: "hello" });
    if (hello.type !== "hello" || hello.protocol !== PROTOCOL || !compatibleVersion(hello.engine_version, pkg.version)) {
      throw fail("engine_version_mismatch", `engine ${String(hello.engine_version)} (protocol ${String(hello.protocol)}) does not match sqlguardllm ${pkg.version} (protocol ${PROTOCOL})`);
    }
    const loaded = await this.send({ type: "load_policy", ...body });
    if (loaded.type !== "load_policy" || typeof loaded.handle !== "string") throw fail("bad_response", "unexpected engine response to load_policy");
    return loaded.handle;
  }

  private dispatch(line: string): void {
    let msg: unknown;
    try { msg = JSON.parse(line); } catch { return; }
    if (typeof msg !== "object" || msg === null) return;
    const m = msg as Record<string, unknown>;
    const p = typeof m.id === "number" ? this.pending.get(m.id) : undefined;
    if (!p) return;
    this.pending.delete(m.id as number);
    clearTimeout(p.timer);
    if (m.type === "error") {
      const err = (m.error ?? {}) as { code?: unknown; message?: unknown };
      p.reject(fail(typeof err.code === "string" ? err.code : "engine_error", typeof err.message === "string" ? err.message : "engine error"));
    } else {
      p.resolve(m);
    }
  }

  /** The engine is gone: fail every pending call and schedule the backoff. */
  private crashed(proc: ChildProcess, why: string): void {
    if (this.proc !== proc) return;
    this.kill(fail("engine_crashed", why));
  }

  private kill(reason: Error): void {
    const proc = this.proc;
    this.proc = undefined;
    this.starting = undefined;
    process.removeListener("exit", this.onExit);
    if (proc) {
      proc.removeAllListeners("exit");
      proc.on("error", () => {});
      proc.kill();
      if (!this.closed) {
        this.retryAt = Date.now() + Math.min(BACKOFF_START_MS * 2 ** this.failures, BACKOFF_MAX_MS);
        this.failures++;
      }
    }
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(reason);
    }
    this.pending.clear();
  }

  private send(req: Omit<WireRequest, "id"> | Record<string, unknown>): Promise<Record<string, unknown>> {
    const proc = this.proc;
    if (!proc?.stdin?.writable) return Promise.reject(fail("engine_crashed", "the engine is not running"));
    const id = this.nextId++;
    const line = JSON.stringify({ ...req, id }) + "\n";
    if (Buffer.byteLength(line) > MAX_MESSAGE_BYTES) return Promise.reject(fail("message_too_large", "the request is over 1 MiB"));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        // A silent engine may be stuck: drop it so later calls get a fresh one.
        this.pending.delete(id);
        reject(fail("timeout", `engine did not answer within ${this.timeoutMs} ms`));
        this.crashed(proc, "the engine timed out");
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      proc.stdin!.write(line, (err) => {
        if (err && this.pending.delete(id)) {
          clearTimeout(timer);
          reject(fail("engine_crashed", "cannot write to the engine"));
        }
      });
    });
  }

  /** Raw `result` of a `check` or `explain` call; throws SqlGuardError on any failure. */
  async call(type: "check" | "explain", sql: string, context: Record<string, unknown> | null): Promise<unknown> {
    const handle = await this.ready();
    const res = await this.send({ type, handle, sql, context });
    if (res.type !== type || typeof res.result !== "object" || res.result === null) throw fail("bad_response", `unexpected engine response to ${type}`);
    return res.result;
  }

  /** Version the engine reported; starts it if needed. */
  async version(): Promise<string> {
    await this.ready();
    return pkg.version;
  }

  /** Stop the engine; later calls fail. Safe to call twice. */
  async close(): Promise<void> {
    if (this.closed) return;
    const proc = this.proc;
    const exited = proc && proc.exitCode === null ? new Promise<void>((r) => proc.once("exit", () => r())) : undefined;
    if (proc?.stdin?.writable) {
      // Polite shutdown; the engine also exits on stdin EOF.
      proc.stdin.end(JSON.stringify({ id: 0, type: "shutdown" }) + "\n");
    }
    this.closed = true;
    const timer = exited ? setTimeout(() => proc?.kill(), 1000) : undefined;
    this.kill(fail("closed", "the engine was closed"));
    if (exited) {
      await exited;
      clearTimeout(timer);
    }
  }
}
