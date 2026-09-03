import { expect, test } from "@playwright/test";
import { installMockBackend } from "./support";

test("OTP and current consent protect every private route", async ({ page }) => {
  await installMockBackend(page, {
    signedIn: false,
    consented: false,
    profileStatus: "invited",
  });
  await page.goto("/portfolio");
  await expect(page.getByRole("heading", { name: /sign in to your account/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^portfolio$/i })).toHaveCount(0);

  await page.getByLabel("Email address").fill("owner-a@example.com");
  await page.getByRole("button", { name: /send secure code/i }).click();
  await page.getByLabel("Six-digit code").fill("123456");
  await page.getByRole("button", { name: /verify and continue/i }).click();
  await expect(page.getByRole("heading", { name: /review before continuing/i })).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);

  await page.getByLabel(/I understand these disclosures/i).check();
  await page.getByRole("button", { name: /accept and continue/i }).click();
  await expect(page.getByRole("heading", { name: /^portfolio$/i })).toBeVisible();
  await expect(page.getByText(/only record activity you already completed at your broker/i)).toBeVisible();
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0);
});
