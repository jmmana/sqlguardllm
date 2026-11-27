#!/usr/bin/env node
// Publish (or dry-run) the npm packages built by npm/build.mjs, in the only safe order:
// the five platform packages first, the main package last.
//
//   node npm/publish.mjs --out npm/dist --dry-run [--pack-dir DIR]   # npm pack only, publishes nothing
//   node npm/publish.mjs --out npm/dist --tag latest                 # npm publish --provenance (CI only)
//
// A version that is already on the registry is skipped, so re-running a half-finished release is safe.
// The main package gets `optionalDependencies` (optional-dependencies.json) for the publish only:
// package.json is restored afterwards and the change is never committed. The main package is built
// (npm run build) after build.mjs wrote src/engine-digests.json, so the digests are in its dist.
// Node.js standard library only.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const platforms = JSON.parse(readFileSync(join(here, "platforms.json"), "utf8"));
const npm = process.platform === "win32" ? "npm.cmd" : "npm";

/** Merge optionalDependencies into a package.json object (pure, tested). */
export function withOptionalDependencies(pkg, optional) {
  return { ...pkg, optionalDependencies: { ...(pkg.optionalDependencies ?? {}), ...optional } };
}

function onRegistry(name, version) {
  try {
    execFileSync(npm, ["view", `${name}@${version}`, "version"], { stdio: "pipe" });
    return true;
  } catch {
    return false; // E404: not published yet
  }
}

function packOrPublish(cwd, name, version, mode, tag, packDir) {
  if (mode === "dry-run") {
    const out = execFileSync(npm, ["pack", "--json", "--pack-destination", packDir], { cwd, encoding: "utf8" });
    const [info] = JSON.parse(out);
    console.log(`${name}@${version}: ${info.files.length} files, ${info.size} bytes packed`);
    writeFileSync(join(packDir, `${info.filename}.files.txt`), info.files.map((f) => f.path).join("\n") + "\n");
    return;
  }
  if (onRegistry(name, version)) {
    console.log(`${name}@${version} is already on npm; skipping`);
    return;
  }
  execFileSync(npm, ["publish", "--provenance", "--access", "public", "--tag", tag], { cwd, stdio: "inherit" });
}

function run(argv) {
  const opt = (name, fallback) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback);
  const out = resolve(opt("--out", join(here, "dist")));
  const mode = argv.includes("--dry-run") ? "dry-run" : "publish";
  const tag = opt("--tag", "");
  if (mode === "publish" && !tag) throw new Error("--tag is required to publish");
  const packDir = resolve(opt("--pack-dir", join(out, "..", "packed")));
  const pkgPath = join(root, "package.json");
  const original = readFileSync(pkgPath, "utf8");
  const pkg = JSON.parse(original);
  if (mode === "dry-run") {
    rmSync(packDir, { recursive: true, force: true });
    mkdirSync(packDir, { recursive: true });
  }
  for (const key of Object.keys(platforms)) {
    const dir = join(out, key);
    const manifest = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
    if (manifest.version !== pkg.version) throw new Error(`${manifest.name} is ${manifest.version}, expected ${pkg.version}; rebuild`);
    packOrPublish(dir, manifest.name, manifest.version, mode, tag, packDir);
  }
  const optional = JSON.parse(readFileSync(join(out, "optional-dependencies.json"), "utf8"));
  try {
    writeFileSync(pkgPath, JSON.stringify(withOptionalDependencies(pkg, optional), null, 2) + "\n");
    execFileSync(npm, ["run", "build"], { cwd: root, stdio: "inherit" }); // engine-digests.json into dist
    packOrPublish(root, pkg.name, pkg.version, mode, tag, packDir);
  } finally {
    writeFileSync(pkgPath, original);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) run(process.argv.slice(2));
