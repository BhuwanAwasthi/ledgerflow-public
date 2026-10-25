import { defineConfig, devices } from "@playwright/test";

/**
 * e2e runs against the production build served by `vite preview` (the same bundle Vercel ships, without the
 * dev login). `npm run e2e` builds first. No API is needed for the signed-out auth gate: every private route
 * must redirect before it asks the API for anything.
 */
const PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
