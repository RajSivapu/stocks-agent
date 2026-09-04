import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const appRoot = process.cwd();

describe("static dashboard security", () => {
  it("loads the theme bootstrap as a same-origin external script before the app", () => {
    const html = readFileSync(resolve(appRoot, "index.html"), "utf8");
    const theme = html.indexOf('<script src="/theme-bootstrap.js"></script>');
    const app = html.indexOf('<script type="module" src="/src/main.tsx"></script>');
    expect(theme).toBeGreaterThan(-1);
    expect(app).toBeGreaterThan(theme);
    expect(html).not.toMatch(/<script>(?!\s*<\/script>)/);
  });

  it("ships a CSP without inline or third-party script authority", () => {
    const headers = readFileSync(resolve(appRoot, "public/_headers"), "utf8");
    expect(headers).toContain("default-src 'none'");
    expect(headers).toContain("script-src 'self'");
    expect(headers).toContain("frame-ancestors 'none'");
    expect(headers).not.toContain("'unsafe-inline'");
    expect(headers).not.toContain("https://fonts.");
  });

  it("stores only the approved appearance values", () => {
    const script = readFileSync(resolve(appRoot, "public/theme-bootstrap.js"), "utf8");
    expect(script).toContain("personal-stock-agent-theme");
    expect(script).toContain('value === "light" || value === "dark"');
    expect(script).not.toMatch(/portfolio|holding|token|session/i);
  });
});
