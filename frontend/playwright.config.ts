import { defineConfig, devices } from "@playwright/test";

import { API_URL, WEB_PORT, WEB_URL } from "./e2e/support/stack";

// Uses an installed Chrome by default so no browser download is needed. Set
// PLAYWRIGHT_CHANNEL="" to use Playwright's bundled Chromium (`npx playwright install chromium`).
const channel = process.env.PLAYWRIGHT_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(channel ? { channel } : {}),
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) } },
  ],
  webServer: {
    // A production build pointed at the E2E API.
    command: `npm run build && npm run start -- --port ${WEB_PORT}`,
    url: WEB_URL,
    timeout: 300_000,
    reuseExistingServer: false,
    env: { NEXT_PUBLIC_API_BASE_URL: API_URL },
  },
});
