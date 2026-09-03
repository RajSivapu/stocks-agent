import { defineConfig, devices } from "@playwright/test";

const live = process.env.E2E_LIVE === "1";
const localUrl = "http://127.0.0.1:4173";

function liveBaseUrl(): string {
  const value = process.env.E2E_BASE_URL;
  if (!value) throw new Error("E2E_BASE_URL is required when E2E_LIVE=1");
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("E2E_BASE_URL must be a credential-free HTTPS origin or path");
  }
  return value;
}

const baseURL = live ? liveBaseUrl() : localUrl;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  ...(process.env.CI ? { workers: 2 } : {}),
  reporter: [["list"]],
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  ...(live ? {} : {
    webServer: {
      command: "VITE_SUPABASE_URL=https://test-project.supabase.co VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test_only_0000000000000000 npm run dev -- --host 127.0.0.1 --port 4173",
      url: localUrl,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  }),
  projects: live
    ? [{
      name: "live-staging",
      grep: /@live/,
      use: { ...devices["Desktop Chrome"], trace: "off", screenshot: "off" },
    }]
    : [
      { name: "desktop-chromium", grepInvert: /@live/, use: { ...devices["Desktop Chrome"] } },
      { name: "ios-layout", grepInvert: /@live/, use: { ...devices["iPhone 15"], browserName: "chromium" } },
      { name: "android-layout", grepInvert: /@live/, use: { ...devices["Pixel 7"], browserName: "chromium" } },
    ],
});
