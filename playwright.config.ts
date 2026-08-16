import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./web/tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  use: {
    channel: "msedge",
    headless: true,
    trace: "off",
    screenshot: "only-on-failure"
  },
  globalSetup: "./web/tests/e2e/global-setup.ts"
});
