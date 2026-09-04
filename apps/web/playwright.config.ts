import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "fixture-chromium",
      testMatch: /(?:dashboard|live-readonly)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4174" },
    },
    {
      name: "production-security-chromium",
      testMatch: /security-headers\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4175" },
    },
  ],
  webServer: [
    {
      command: "VITE_E2E_FIXTURES=1 npm run dev -- --host 127.0.0.1 --port 4174",
      url: "http://127.0.0.1:4174/?fixture=complete",
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
      timeout: 30_000,
    },
    {
      command: "node ../../scripts/serve_dashboard_dist.mjs dist 4175",
      url: "http://127.0.0.1:4175/",
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
      timeout: 30_000,
    },
  ],
});
