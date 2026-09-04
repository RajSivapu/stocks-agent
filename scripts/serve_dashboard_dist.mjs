#!/usr/bin/env node

import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, resolve, sep } from "node:path";

const root = resolve(process.argv[2] ?? "apps/web/dist");
const port = Number(process.argv[3] ?? "4175");
if (!existsSync(root) || !statSync(root).isDirectory() || !Number.isInteger(port)) {
  throw new Error("a built dashboard directory and integer port are required");
}

function globalHeaders() {
  const lines = readFileSync(resolve(root, "_headers"), "utf8").split(/\r?\n/);
  const headers = {};
  let global = false;
  for (const line of lines) {
    if (line === "/*") {
      global = true;
      continue;
    }
    if (global && line && !/^\s/.test(line)) break;
    if (!global || !line.trim()) continue;
    const separator = line.indexOf(":");
    if (separator < 1) throw new Error("invalid generated header line");
    headers[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  return headers;
}

const headers = globalHeaders();
const mime = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

const server = createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://127.0.0.1").pathname);
  const requested = resolve(root, pathname.replace(/^\/+/, ""));
  const safe = requested === root || requested.startsWith(`${root}${sep}`);
  let target = safe && existsSync(requested) && statSync(requested).isFile()
    ? requested
    : resolve(root, "index.html");
  if (!target.startsWith(`${root}${sep}`)) target = resolve(root, "index.html");
  response.writeHead(200, { ...headers, "Content-Type": mime[extname(target)] ?? "application/octet-stream" });
  response.end(readFileSync(target));
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`dashboard production fixture on http://127.0.0.1:${port}\n`);
});
