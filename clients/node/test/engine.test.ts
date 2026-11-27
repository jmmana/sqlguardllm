import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { SqlGuardError, detectPlatform, digestTree, resolveEngine } from "../src/index";
// @ts-expect-error plain ESM script without types
import { digestTree as buildDigestTree, mainOptionalDependencies, platformManifest } from "../npm/build.mjs";

const platforms = JSON.parse(readFileSync(join(__dirname, "../npm/platforms.json"), "utf8")) as Record<
  string,
  { package: string; os: string; cpu: string; libc?: string; executable: string }
>;
const tmp = mkdtempSync(join(tmpdir(), "sqlguard-engine-"));
afterAll(() => rmSync(tmp, { recursive: true, force: true }));

function fakePackage(name: string, body = "binary"): string {
  const folder = join(tmp, name);
  mkdirSync(join(folder, "engine", "_internal"), { recursive: true });
  writeFileSync(join(folder, "engine", "sqlguardllm-engine"), body);
  writeFileSync(join(folder, "engine", "_internal", "lib.so"), "lib");
  return folder;
}

const linux = { platform: "linux", arch: "x64", glibc: true };

describe("platform detection", () => {
  it("maps every platform of npm/platforms.json", () => {
    for (const [key, p] of Object.entries(platforms)) {
      const info = detectPlatform({ platform: p.os, arch: p.cpu, glibc: true });
      expect(info).toEqual({ key, packageName: p.package, executable: p.executable });
    }
  });
  it("unsupported platform names remote mode", () => {
    for (const env of [{ platform: "freebsd", arch: "x64" }, { platform: "linux", arch: "ia32" }, { platform: "linux", arch: "x64", glibc: false }]) {
      const e = (() => { try { detectPlatform(env); } catch (x) { return x as SqlGuardError; } })();
      expect(e).toBeInstanceOf(SqlGuardError);
      expect(e?.code).toBe("unsupported_platform");
      expect(e?.message).toContain("remote mode");
      expect(e?.message).toContain("sqlguardllm serve");
    }
  });
});

describe("engine package manifests", () => {
  it("declare os and cpu and whitelist files", () => {
    for (const key of Object.keys(platforms)) {
      const m = platformManifest(key, "1.2.3", {});
      expect(m.os).toEqual([platforms[key]!.os]);
      expect(m.cpu).toEqual([platforms[key]!.cpu]);
      expect(m.files).toEqual(["engine", "LICENSE"]);
      expect(m.scripts).toBeUndefined();
    }
  });
  it("main optionalDependencies cover every platform at one version", () => {
    expect(mainOptionalDependencies("1.2.3")).toEqual(Object.fromEntries(Object.values(platforms).map((p) => [p.package, "1.2.3"])));
  });
  it("main package has no install scripts and a files whitelist", () => {
    const pkg = JSON.parse(readFileSync(join(__dirname, "../package.json"), "utf8"));
    for (const s of ["preinstall", "install", "postinstall"]) expect(pkg.scripts[s]).toBeUndefined();
    expect(pkg.files).toEqual(["dist", "README.md", "LICENSE"]);
  });
});

describe("resolveEngine", () => {
  const folder = fakePackage("good");
  const good = digestTree(join(folder, "engine"));
  const opts = (digest: string | undefined, locate = () => folder) => ({
    env: linux,
    locate,
    digests: { version: "0", platforms: digest ? { "linux-x64": digest } : {} },
  });

  it("digest matches the build script", () => expect(good).toBe(buildDigestTree(join(folder, "engine"))));
  it("returns the executable when the digest matches", () =>
    expect(resolveEngine(opts(good))).toBe(join(folder, "engine", "sqlguardllm-engine")));
  it("refuses a changed executable", () => {
    const tampered = fakePackage("tampered", "evil");
    const e = (() => { try { resolveEngine(opts(good, () => tampered)); } catch (x) { return x as SqlGuardError; } })();
    expect(e?.code).toBe("engine_digest_mismatch");
  });
  it("refuses a changed support file", () => {
    const tampered = fakePackage("tampered2");
    writeFileSync(join(tampered, "engine", "_internal", "lib.so"), "other");
    expect(() => resolveEngine(opts(good, () => tampered))).toThrow(/does not match/);
  });
  it("refuses an extra file", () => {
    const tampered = fakePackage("tampered3");
    writeFileSync(join(tampered, "engine", "extra"), "x");
    expect(() => resolveEngine(opts(good, () => tampered))).toThrow(/does not match/);
  });
  it("fails closed without a built-in digest", () => {
    const e = (() => { try { resolveEngine(opts(undefined)); } catch (x) { return x as SqlGuardError; } })();
    expect(e?.code).toBe("engine_not_found");
    expect(e?.message).toContain("remote mode");
  });
  it("the shipped development digests are empty, so nothing runs unverified", () => {
    expect(() => resolveEngine({ env: linux, locate: () => folder })).toThrow(SqlGuardError);
  });
  it("names remote mode when the platform package is missing", () => {
    const e = (() => { try { resolveEngine(opts(good, () => { throw new Error("nope"); })); } catch (x) { return x as SqlGuardError; } })();
    expect(e?.code).toBe("engine_not_found");
    expect(e?.message).toContain("remote mode");
  });
});
