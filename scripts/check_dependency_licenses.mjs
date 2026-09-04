#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";

const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const allowed = new Set([
  "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BlueOak-1.0.0",
  "CC0-1.0", "ISC", "MIT", "MIT-0", "MPL-2.0",
]);
const failures = [];
const observed = new Set();
let checked = 0;

for (const [path, locked] of Object.entries(lock.packages ?? {})) {
  if (!path.startsWith("node_modules/") || locked.link === true) continue;
  const manifestPath = `${path}/package.json`;
  const manifest = existsSync(manifestPath)
    ? JSON.parse(readFileSync(manifestPath, "utf8"))
    : { name: path.slice("node_modules/".length), license: locked.license };
  const license = typeof manifest.license === "string" ? manifest.license :
    (typeof locked.license === "string" ? locked.license : "");
  checked += 1;
  observed.add(license || "missing");
  if (!allowed.has(license)) failures.push(`${manifest.name ?? path}: ${license || "missing license"}`);
}

const deno = JSON.parse(readFileSync("supabase/functions/deno.lock", "utf8"));
for (const required of ["npm:jose@6.2.2", "npm:postgres@3.4.9"]) {
  if (!Object.hasOwn(deno.specifiers ?? {}, required)) failures.push(`Deno specifier is not pinned: ${required}`);
}

if (failures.length > 0) throw new Error(`dependency license/pinning check failed:\n${failures.join("\n")}`);
process.stdout.write(JSON.stringify({ status: "verified", packages_checked: checked, licenses: [...observed].sort() }) + "\n");
