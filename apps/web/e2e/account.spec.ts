import { expect, test } from "@playwright/test";
import { installMockBackend, installStoredSession } from "./support";

test.beforeEach(async ({ page }) => {
  await installStoredSession(page);
});

test("exports, settings, and deletion require explicit owner actions", async ({ page }) => {
  const state = await installMockBackend(page);
  await page.goto("/settings");
  await page.getByLabel("Display name").fill("Owner A Updated");
  await page.getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /download ledger csv/i }).click();
  expect((await download).suggestedFilename()).toBe("stock-agent-ledger.csv");

  await page.getByRole("button", { name: /start account deletion/i }).click();
  await page.getByLabel("Fresh six-digit code").fill("123456");
  await page.getByRole("button", { name: /verify fresh code/i }).click();
  await page.getByLabel("Type DELETE MY ACCOUNT").fill("DELETE MY ACCOUNT");
  await page.getByRole("button", { name: /confirm account deletion/i }).click();
  await expect(page.getByRole("heading", { name: /sign in to your account/i })).toBeVisible();
  expect(state.profileStatus).toBe("deletion_pending");
});

test("a deletion-pending owner can cancel only with a fresh code", async ({ page }) => {
  const state = await installMockBackend(page, { profileStatus: "deletion_pending" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /account deletion is pending/i })).toBeVisible();
  await page.getByRole("button", { name: /cancel deletion with email code/i }).click();
  await page.getByLabel("Fresh six-digit code").fill("123456");
  await page.getByRole("button", { name: /verify fresh code/i }).click();
  await expect(page.getByRole("heading", { name: /^today$/i })).toBeVisible();
  expect(state.profileStatus).toBe("active");
});
