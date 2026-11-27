#!/usr/bin/env node
// Placeholder engines for the pull-request dry run (no real binaries, no Python):
//   node npm/fake-engines.mjs <dir>
// Writes <dir>/<platform>/sqlguardllm-engine-<platform>/ in the layout scripts/fetch-engine.py unpacks,
// so npm/build.mjs and npm/publish.mjs --dry-run can be exercised on every PR. They are never published.
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const platforms = JSON.parse(readFileSync(join(here, "platforms.json"), "utf8"));
const target = resolve(process.argv[2] ?? "engines");
for (const [key, p] of Object.entries(platforms)) {
  const folder = join(target, key, `sqlguardllm-engine-${key}`);
  mkdirSync(join(folder, "_internal"), { recursive: true });
  writeFileSync(join(folder, p.executable), `placeholder engine for ${key}\n`);
  chmodSync(join(folder, p.executable), 0o755);
  writeFileSync(join(folder, "_internal", "lib.bin"), "placeholder\n");
}
