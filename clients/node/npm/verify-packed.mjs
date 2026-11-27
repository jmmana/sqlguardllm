#!/usr/bin/env node
// Install check for the packed platform packages: unpack each tarball the way `npm install` does and
// compare the digest of its engine folder with the one built into the main package.
//   node npm/verify-packed.mjs npm/packed
// This catches what a file list cannot: things npm drops on the way (symlinks, empty folders), which
// would make the client refuse the installed engine.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { digestTree } from "./build.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const dir = resolve(process.argv[2] ?? join(here, "packed"));
const digests = JSON.parse(readFileSync(join(here, "..", "src", "engine-digests.json"), "utf8"));
const platforms = JSON.parse(readFileSync(join(here, "platforms.json"), "utf8"));
let failed = 0;
for (const [key, p] of Object.entries(platforms)) {
  const tgz = readdirSync(dir).find((n) => n.startsWith(`${p.package}-`) && n.endsWith(".tgz"));
  if (!tgz) { console.error(`missing tarball for ${p.package}`); failed++; continue; }
  const tmp = mkdtempSync(join(tmpdir(), "sqlguard-verify-"));
  try {
    execFileSync("tar", ["-xzf", join(dir, tgz), "-C", tmp]);
    const actual = digestTree(join(tmp, "package", "engine"));
    if (actual !== digests.platforms[key]) {
      console.error(`${p.package}: installed engine digest ${actual} != built-in ${digests.platforms[key]}`);
      failed++;
    } else console.log(`${p.package}: installed engine matches the built-in digest`);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
process.exit(failed ? 1 : 0);
