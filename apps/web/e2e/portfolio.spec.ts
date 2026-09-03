import { expect, test } from "@playwright/test";
import { installMockBackend, installStoredSession } from "./support";

test.beforeEach(async ({ page }) => {
  await installStoredSession(page);
});

test("owner previews, cancels, and explicitly confirms a broker fill record", async ({ page }) => {
  const state = await installMockBackend(page);
  await page.goto("/portfolio");
  await expect(page.getByRole("heading", { name: /^portfolio$/i })).toBeVisible();
  await expect(page.getByRole("row", { name: /NVDA/i })).toBeVisible();

  await page.getByRole("button", { name: "Record Buy", exact: true }).click();
  await page.getByLabel("Ticker").fill("AMD");
  await page.getByLabel("Filled quantity").fill("1.00000000");
  await page.getByLabel("Fill price").fill("100.0000");
  await page.getByLabel("Fees").fill("0.50");
  await page.getByLabel("Broker cash total").fill("100.50");
  await page.getByLabel("Execution date").fill("2026-09-02");
  await page.getByLabel("Risk bucket").selectOption("growth");
  await page.getByRole("button", { name: /preview record buy/i }).click();

  const dialog = page.getByRole("dialog", { name: /review record buy/i });
  await expect(dialog).toContainText("Record only · no brokerage action");
  await expect(dialog).toContainText("Recordkeeping only; no brokerage order is placed.");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(state.calls.filter((call) => call.path.endsWith("/confirm"))).toHaveLength(0);

  await page.getByRole("button", { name: /preview record buy/i }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Confirm record" }).click();
  await expect(page.getByRole("dialog")).toContainText(/record applied/i);
  expect(state.calls.filter((call) => call.path.endsWith("/confirm"))).toHaveLength(1);
  expect(JSON.stringify(state.calls)).not.toContain("owner_id");
});
