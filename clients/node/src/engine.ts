import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, readlinkSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { SqlGuardError } from "./index";
import digests from "./engine-digests.json";

export interface PlatformInfo { key: string; packageName: string; executable: string }

interface PlatformRow extends PlatformInfo { os: string; cpu: string; glibc: boolean }

/** Platform key -> platform package. Keep in step with npm/platforms.json (a test checks it). */
const PLATFORMS: PlatformRow[] = [
  { key: "linux-x64", packageName: "sqlguardllm-engine-linux-x64", executable: "sqlguardllm-engine", os: "linux", cpu: "x64", glibc: true },
  { key: "linux-arm64", packageName: "sqlguardllm-engine-linux-arm64", executable: "sqlguardllm-engine", os: "linux", cpu: "arm64", glibc: true },
  { key: "macos-arm64", packageName: "sqlguardllm-engine-macos-arm64", executable: "sqlguardllm-engine", os: "darwin", cpu: "arm64", glibc: false },
  { key: "macos-x64", packageName: "sqlguardllm-engine-macos-x64", executable: "sqlguardllm-engine", os: "darwin", cpu: "x64", glibc: false },
  { key: "windows-x64", packageName: "sqlguardllm-engine-windows-x64", executable: "sqlguardllm-engine.exe", os: "win32", cpu: "x64", glibc: false },
];

const REMOTE_HINT = "Use the remote mode instead: run `sqlguardllm serve` and create `new SqlGuard({ baseUrl })`.";

export interface EngineEnv {
  platform: string;
  arch: string;
  /** Linux only: true when the C library is glibc. Default: detected on this machine. */
  glibc?: boolean;
}

function hostGlibc(): boolean {
  const report = (process as { report?: { getReport(): unknown } }).report?.getReport() as
    | { header?: { glibcVersionRuntime?: string } }
    | undefined;
  return typeof report?.header?.glibcVersionRuntime === "string";
}

/** The platform package for this machine, or SqlGuardError("unsupported_platform") naming remote mode. */
export function detectPlatform(env?: EngineEnv): PlatformInfo {
  const { platform, arch } = env ?? { platform: process.platform, arch: process.arch };
  const found = PLATFORMS.find((p) => p.os === platform && p.cpu === arch);
  if (!found) {
    throw new SqlGuardError("unsupported_platform", `no embedded engine for ${platform}-${arch}. ${REMOTE_HINT}`);
  }
  if (found.glibc && !(env?.glibc ?? hostGlibc())) {
    throw new SqlGuardError("unsupported_platform", `no embedded engine for ${platform}-${arch} with musl libc. ${REMOTE_HINT}`);
  }
  return { key: found.key, packageName: found.packageName, executable: found.executable };
}

const sha = (data: string | Buffer): string => createHash("sha256").update(data).digest("hex");

/**
 * SHA-256 of an engine folder: the hash of the sorted lines "<sha256>  <relative path>\n" of every
 * file (a symlink counts as "link:<target>"), so the executable and its `_internal/` files are all
 * covered. Must stay identical to `digestTree` in npm/build.mjs.
 */
export function digestTree(root: string): string {
  const lines: string[] = [];
  const walk = (dir: string, prefix: string): void => {
    for (const name of readdirSync(dir).sort()) {
      const path = join(dir, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      const st = lstatSync(path);
      if (st.isDirectory()) walk(path, rel);
      else if (st.isSymbolicLink()) lines.push(`${sha(`link:${readlinkSync(path)}`)}  ${rel}\n`);
      else if (st.isFile()) lines.push(`${sha(readFileSync(path))}  ${rel}\n`);
      else throw new SqlGuardError("engine_digest_mismatch", `unexpected special file in the engine: ${rel}`);
    }
  };
  walk(root, "");
  lines.sort((a, b) => (a.slice(66) < b.slice(66) ? -1 : a.slice(66) > b.slice(66) ? 1 : 0));
  return sha(lines.join(""));
}

export interface ResolveEngineOptions {
  env?: EngineEnv;
  /** Digests built into the package; tests override them. */
  digests?: { version: string | null; platforms: Record<string, string | undefined> };
  /** Folder of a platform package; the default uses Node module resolution. */
  locate?: (packageName: string) => string;
}

function defaultLocate(packageName: string): string {
  return dirname(createRequire(import.meta.url).resolve(`${packageName}/package.json`));
}

/**
 * Path of this platform's engine executable, after checking its SHA-256 against the value built
 * into this package. FAIL CLOSED: a missing package, a missing digest or any difference throws
 * SqlGuardError and nothing is started.
 */
export function resolveEngine(opts: ResolveEngineOptions = {}): string {
  const info = detectPlatform(opts.env);
  const expected = (opts.digests ?? (digests as NonNullable<ResolveEngineOptions["digests"]>)).platforms[info.key];
  if (!expected) {
    throw new SqlGuardError("engine_not_found", `this package has no engine digest for ${info.key}. ${REMOTE_HINT}`);
  }
  let folder: string;
  try {
    folder = (opts.locate ?? defaultLocate)(info.packageName);
  } catch {
    throw new SqlGuardError(
      "engine_not_found",
      `the engine package ${info.packageName} is not installed (installed with --omit=optional?). ${REMOTE_HINT}`,
    );
  }
  const engineDir = join(folder, "engine");
  let actual: string;
  try {
    actual = digestTree(engineDir);
  } catch (e) {
    if (e instanceof SqlGuardError) throw e;
    throw new SqlGuardError("engine_not_found", `cannot read the engine in ${info.packageName}. ${REMOTE_HINT}`);
  }
  if (actual !== expected) {
    throw new SqlGuardError(
      "engine_digest_mismatch",
      `the engine in ${info.packageName} does not match the SHA-256 built into sqlguardllm; refusing to run it`,
    );
  }
  return join(engineDir, info.executable);
}
