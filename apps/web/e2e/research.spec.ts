import { expect, test } from "@playwright/test";
import { installMockBackend, installStoredSession } from "./support";

test.beforeEach(async ({ page }) => {
  await installStoredSession(page);
  await installMockBackend(page);
});

test("all seven owner screens expose evidence and receipt boundaries", async ({ page }) => {
  const screens = [
    ["/", "Today"], ["/portfolio", "Portfolio"], ["/activity", "Activity"],
    ["/research", "Research"], ["/runs", "Runs"], ["/connections", "Connections"],
    ["/settings", "Settings"],
  ] as const;
  for (const [path, heading] of screens) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
    const geometry = await page.evaluate(() => ({
      contentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    }));
    expect(geometry.contentWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  }

  await page.goto("/research");
  await expect(page.getByText(/independent checker/i)).toBeVisible();
  await expect(page.getByText(/deterministic policy result/i)).toBeVisible();
  await expect(page.getByRole("article", { name: /NVDA/i })).toContainText("Wait for a better entry.");

  await page.goto("/runs");
  await expect(page.getByText(/actual delivery receipts/i)).toBeVisible();
  await page.getByRole("button", { name: /run analysis now/i }).click();
  await expect(page.getByRole("status")).toContainText(/No Telegram notification will be sent/i);
});
