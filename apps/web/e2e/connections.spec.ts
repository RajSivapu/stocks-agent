import { expect, test } from "@playwright/test";
import { installMockBackend, installStoredSession } from "./support";

test.beforeEach(async ({ page }) => {
  await installStoredSession(page);
});

test("Claude setup requires one-time credential, phone step-up, activation, and revocation", async ({ page }) => {
  const state = await installMockBackend(page);
  await page.goto("/connections");
  await page.getByRole("button", { name: /start claude setup/i }).click();
  await expect(page.getByText(/gateway credential — shown once/i)).toBeVisible();
  await page.getByRole("button", { name: /I saved the credential/i }).click();
  await page.getByLabel("Routine fire URL").fill(
    "https://api.anthropic.com/v1/claude_code/routines/trig_ABCDEF/fire",
  );
  await page.getByLabel("One-time Routine token").fill("routine-token-value-with-32-bytes");
  await page.getByRole("button", { name: /test connection/i }).click();
  await page.getByLabel("Fresh six-digit code").fill("123456");
  await page.getByRole("button", { name: /verify fresh code/i }).click();
  await expect(page.getByText(/application-fired handshake queued/i)).toBeVisible();
  await page.getByRole("button", { name: /activate as primary/i }).click();
  await expect(page.getByText(/activated as the primary provider/i)).toBeVisible();
  await page.getByRole("button", { name: /revoke connection/i }).click();
  await expect(page.getByText(/revoked in both directions/i)).toBeVisible();
  expect(state.connectionStatus).toBe("revoked");
});

test("Telegram pairing and unlink use fresh authentication and fixed recordkeeping copy", async ({ page }) => {
  const state = await installMockBackend(page, { telegramActive: true, connectionStatus: "active" });
  await page.goto("/connections");
  await page.getByRole("button", { name: /new pairing code/i }).click();
  await page.getByLabel("Fresh six-digit code").fill("123456");
  await page.getByRole("button", { name: /verify fresh code/i }).click();
  await expect(page.getByText(/\/pair ABCD234567/i)).toBeVisible();
  await page.getByRole("button", { name: /unlink telegram/i }).click();
  await expect(page.getByText(/pending Telegram confirmations cancelled/i)).toBeVisible();
  expect(state.telegramActive).toBe(false);
});
