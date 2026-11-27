#!/usr/bin/env node
// Build the npm packages of a release from the engines unpacked by scripts/fetch-engine.py.
//
//   node npm/build.mjs --engines engines --out npm/dist
//
// Writes <out>/<platform>/ (package.json, LICENSE, engine/) for every platform in npm/platforms.json,
// <out>/optional-dependencies.json and src/engine-digests.json (the SHA-256 values built into the main
// package). Run it before `npm run build`; publish every platform folder first and the main package last,
// with optional-dependencies.json merged into its package.json as `optionalDependencies`. They are added
// at publish time so the development lockfile does not need packages that are not on the registry yet.
// Node.js standard library only.
import { createHash } from "node:crypto";
import { copyFileSync, cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, readlinkSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const platforms = JSON.parse(readFileSync(join(here, "platforms.json"), "utf8"));
const sha = (data) => createHash("sha256").update(data).digest("hex");

/** Must stay identical to `digestTree` in src/engine.ts. */
export function digestTree(dir) {
  const lines = [];
  const walk = (current, prefix) => {
    for (const name of readdirSync(current).sort()) {
      const path = join(current, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      const st = lstatSync(path);
      if (st.isDirectory()) walk(path, rel);
      else if (st.isSymbolicLink()) lines.push(`${sha(`link:${readlinkSync(path)}`)}  ${rel}\n`);
      else if (st.isFile()) lines.push(`${sha(readFileSync(path))}  ${rel}\n`);
      else throw new Error(`special file in the engine: ${rel}`);
    }
  };
  walk(dir, "");
  lines.sort((a, b) => (a.slice(66) < b.slice(66) ? -1 : a.slice(66) > b.slice(66) ? 1 : 0));
  return sha(lines.join(""));
}

/**
 * Copy a folder, turning every symlink into a real copy of what it points to. npm packs files, not
 * symlinks: one would vanish on install and the engine digest would no longer match. The macOS engine
 * (PyInstaller, Python.framework) has some. (fs.cpSync({dereference}) still kept them in Node 24.)
 */
export function copyResolved(from, to) {
  mkdirSync(to, { recursive: true });
  for (const name of readdirSync(from)) {
    const src = join(from, name);
    const dst = join(to, name);
    const st = statSync(src); // follows symlinks
    if (st.isDirectory()) copyResolved(src, dst);
    else if (st.isFile()) copyFileSync(src, dst);
    else throw new Error(`special file in the engine: ${src}`);
  }
}

export function platformManifest(key, version, repository) {
  const p = platforms[key];
  return {
    name: p.package,
    version,
    description: `The sqlguardllm engine for ${key}. Installed by the sqlguardllm package; do not depend on it directly.`,
    license: "Apache-2.0",
    repository,
    os: [p.os],
    cpu: [p.cpu],
    ...(p.libc ? { libc: [p.libc] } : {}),
    files: ["engine", "LICENSE"],
    publishConfig: { access: "public" },
  };
}

export function mainOptionalDependencies(version) {
  return Object.fromEntries(Object.values(platforms).map((p) => [p.package, version]));
}

function run(argv) {
  const opt = (name, fallback) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback);
  const engines = resolve(opt("--engines", "engines"));
  const out = resolve(opt("--out", join(here, "dist")));
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  const digests = { version: pkg.version, platforms: {} };
  rmSync(out, { recursive: true, force: true });
  for (const [key, p] of Object.entries(platforms)) {
    const unpacked = join(engines, key);
    const folder = existsSync(unpacked) ? readdirSync(unpacked).find((n) => lstatSync(join(unpacked, n)).isDirectory()) : undefined;
    if (!folder || !existsSync(join(unpacked, folder, p.executable))) {
      throw new Error(`no ${p.executable} under ${unpacked}; run scripts/fetch-engine.py first`);
    }
    const target = join(out, key);
    mkdirSync(target, { recursive: true });
    copyResolved(join(unpacked, folder), join(target, "engine"));
    cpSync(join(root, "LICENSE"), join(target, "LICENSE"));
    writeFileSync(join(target, "package.json"), JSON.stringify(platformManifest(key, pkg.version, pkg.repository), null, 2) + "\n");
    digests.platforms[key] = digestTree(join(target, "engine"));
    console.log(`${p.package}@${pkg.version}: ${digests.platforms[key]}`);
  }
  writeFileSync(join(root, "src", "engine-digests.json"), JSON.stringify(digests, null, 2) + "\n");
  writeFileSync(join(out, "optional-dependencies.json"), JSON.stringify(mainOptionalDependencies(pkg.version), null, 2) + "\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) run(process.argv.slice(2));
