import { expect, test } from "@playwright/test";

test("production shell applies its generated headers and CSP in Chromium", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("personal-stock-agent-theme", "dark"));
  const response = await page.goto("/");
  expect(response).not.toBeNull();
  const headers = response!.headers();
  expect(headers["content-security-policy"]).toContain("default-src 'none'");
  expect(headers["content-security-policy"]).toContain("script-src 'self'");
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["strict-transport-security"]).toContain("max-age=63072000");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["permissions-policy"]).toContain("payment=()");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  const inlineDirective = await page.evaluate(() => new Promise<string>((resolve) => {
    const timeout = window.setTimeout(() => resolve("not-blocked"), 1_000);
    window.addEventListener("securitypolicyviolation", (event) => {
      if (event.violatedDirective.startsWith("script-src")) {
        window.clearTimeout(timeout);
        resolve(event.violatedDirective);
      }
    }, { once: true });
    const script = document.createElement("script");
    script.textContent = "window.__unsafeInlineExecuted = true";
    document.head.append(script);
  }));
  expect(inlineDirective).toMatch(/^script-src/);
  expect(await page.evaluate(() => (window as Window & { __unsafeInlineExecuted?: boolean }).__unsafeInlineExecuted)).toBeUndefined();

  const connectDirective = await page.evaluate(() => new Promise<string>((resolve) => {
    const timeout = window.setTimeout(() => resolve("not-blocked"), 1_000);
    window.addEventListener("securitypolicyviolation", (event) => {
      if (event.violatedDirective === "connect-src") {
        window.clearTimeout(timeout);
        resolve(event.violatedDirective);
      }
    }, { once: true });
    void fetch("https://example.invalid/csp-probe").catch(() => undefined);
  }));
  expect(connectDirective).toBe("connect-src");
});
