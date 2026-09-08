import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const routes = ["/portfolio", "/ideas", "/intelligence", "/reports", "/reports/7d834dbd-75bb-4313-931f-09732f003932", "/system", "/runs/7d834dbd-75bb-4313-931f-09732f003932"];

test("password sign-in and email recovery are accessible", async ({ page }) => {
  await page.goto("/?fixture=auth");
  await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();
  await expect(page.getByLabel(/^password$/i)).toHaveAttribute("autocomplete", "current-password");
  let results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.getByRole("button", { name: /set up or reset password/i }).click();
  await expect(page.getByRole("heading", { name: /set up or reset your password/i })).toBeVisible();
  results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.goto("/?fixture=auth-recovery");
  await expect(page.getByRole("heading", { name: /choose a new password/i })).toBeVisible();
  await expect(page.getByLabel(/^new password$/i)).toHaveAttribute("autocomplete", "new-password");
  results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

for (const width of [300, 390, 1024]) {
  test(`authentication fits ${width}px without page-level horizontal clipping`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/?fixture=auth");
    await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  });
}

test("owner dashboard is keyboard usable in light and dark modes", async ({ page }) => {
  await page.goto("/portfolio?fixture=complete");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeVisible();
  const focusedResults = await new AxeBuilder({ page }).analyze();
  expect(focusedResults.violations).toEqual([]);
  await page.keyboard.press("Enter");
  await expect(page.locator("main#main-content")).toBeFocused();
  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("radio", { name: "Light" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

for (const width of [300, 320, 390, 768, 1024, 1440]) {
  test(`dashboard fits ${width}px without page-level horizontal clipping`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/portfolio?fixture=complete");
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  });
}

for (const width of [300, 390]) {
  test(`intelligence wraps at ${width}px without horizontal clipping`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/intelligence?fixture=complete");
    await expect(page.getByRole("heading", { name: "What changed" })).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  });
}

test("intelligence disclosures follow visual keyboard order and respect reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/intelligence?fixture=complete");
  const history = page.getByText("History, questions, and invalidation");
  const diagnostics = page.locator("summary").filter({ hasText: "Evidence and diagnostics" });
  await history.focus();
  await expect(history).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Missing inputs" }).first()).toBeVisible();
  await diagnostics.focus();
  await expect(diagnostics).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Evidence passages" })).toBeVisible();
  const transitionSeconds = await diagnostics.evaluate((node) =>
    Number.parseFloat(getComputedStyle(node).transitionDuration)
  );
  expect(transitionSeconds).toBeLessThanOrEqual(0.00001);
});

for (const route of routes) {
  test(`receipt fixture is accessible at ${route}`, async ({ page }) => {
    await page.goto(`${route}?fixture=complete`);
    await expect(page.locator("main#main-content")).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

test("hostile stored report content remains inert and unsafe links remain text", async ({ page }) => {
  await page.goto("/reports/7d834dbd-75bb-4313-931f-09732f003932?fixture=hostile");
  await expect(page.getByText(/<img src=x onerror/)).toBeVisible();
  await expect(page.locator("main img")).toHaveCount(0);
  await expect(page.getByText("unsafe.example")).toBeVisible();
  await expect(page.getByRole("link", { name: "unsafe.example" })).toHaveCount(0);
  await expect(page.getByText(/accepted by telegram/i)).toBeVisible();
  await expect(page.getByText(/^delivered$/i)).toHaveCount(0);
});

test("stale, owner-denied, and expired-session states disclose no private fixture data", async ({ page }) => {
  await page.goto("/portfolio?fixture=stale");
  await expect(page.getByText(/stale evidence/i)).toBeVisible();
  await page.goto("/portfolio?fixture=owner-denied");
  await expect(page.getByRole("heading", { name: /owner only/i })).toBeVisible();
  await expect(page.getByText("FIXTURE_ONLY_TICKER")).toHaveCount(0);
  await page.goto("/portfolio?fixture=expired");
  await expect(page.getByRole("heading", { name: /session expired/i })).toBeVisible();
  await expect(page.getByText("FIXTURE_ONLY_TICKER")).toHaveCount(0);
});
