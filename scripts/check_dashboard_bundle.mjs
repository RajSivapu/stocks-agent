#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { basename, join, resolve } from "node:path";

const root = resolve(process.argv[2] ?? "apps/web/dist");
if (!existsSync(root) || !statSync(root).isDirectory()) {
  throw new Error(`dashboard build directory is missing: ${root}`);
}

function files(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

const paths = files(root);
if (paths.some((path) => path.endsWith(".map"))) throw new Error("production source maps are forbidden");
const textPaths = paths.filter((path) => /\.(?:html|js|css|json|txt)$/.test(path) || basename(path) === "_headers");
const combined = textPaths.map((path) => readFileSync(path, "utf8")).join("\n");
const forbidden = [
  "SUPABASE_SERVICE_ROLE_KEY",
  "TELEGRAM_BOT_TOKEN",
  "TELEGRAM_OWNER_CHAT_ID",
  "FINNHUB_API_KEY",
  "ALPHAVANTAGE_API_KEY",
  "MARKET_AGENT_SECRET",
  "DASHBOARD_DATABASE_URL",
  "FIXTURE_ONLY_TICKER",
  "__SUPABASE_HTTPS_ORIGIN__",
  "__SUPABASE_WSS_ORIGIN__",
  "__DASHBOARD_API_ORIGIN__",
  "'unsafe-inline'",
];
for (const pattern of forbidden) {
  if (combined.includes(pattern)) throw new Error(`forbidden production bundle pattern: ${pattern}`);
}
if (/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i.test(combined)) {
  throw new Error("UUID literal found in production assets");
}
if (/eyJ[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}/.test(combined)) {
  throw new Error("JWT-like value found in production assets");
}

const index = readFileSync(join(root, "index.html"), "utf8");
if (/<script(?![^>]*\bsrc=)[^>]*>\s*[^<\s]/i.test(index)) {
  throw new Error("inline executable script found in production HTML");
}
if (!index.includes('<script src="/theme-bootstrap.js"></script>')) {
  throw new Error("external theme bootstrap is missing");
}
const headers = readFileSync(join(root, "_headers"), "utf8");
for (const required of ["default-src 'none'", "script-src 'self'", "frame-ancestors 'none'", "cache-control: no-store"]) {
  if (!headers.toLowerCase().includes(required.toLowerCase())) throw new Error(`security header missing: ${required}`);
}

const moduleMatch = /<script type="module"[^>]*src="([^"]+)"/.exec(index);
if (!moduleMatch) throw new Error("initial application module is missing");
const modulePath = join(root, moduleMatch[1].replace(/^\//, ""));
const gzipBytes = gzipSync(readFileSync(modulePath)).byteLength;
if (gzipBytes > 250 * 1024) throw new Error(`initial JavaScript exceeds 250 KB gzip: ${gzipBytes}`);

const hashes = paths.map((path) => ({
  file: path.slice(root.length + 1),
  sha256: createHash("sha256").update(readFileSync(path)).digest("hex"),
}));
process.stdout.write(JSON.stringify({ status: "verified", file_count: paths.length, initial_js_gzip_bytes: gzipBytes, hashes }, null, 2) + "\n");
