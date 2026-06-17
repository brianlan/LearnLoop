import { defineConfig } from "@playwright/test";

// Target the Compose-served API gateway on port 8000
process.env.PLAYWRIGHT_API_ORIGIN = "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./tests",
  testMatch: "compose-smoke.spec.ts",
  use: {
    baseURL: "http://127.0.0.1:8080",
    headless: true,
  },
});
