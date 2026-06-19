import { defineConfig } from "@playwright/test";

process.env.PLAYWRIGHT_API_ORIGIN ??= "http://127.0.0.1:18000";

export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/compose-smoke.spec.ts"],
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
  },
  webServer: [
    {
      command: "MATH_INGESTION_VLM_ENDPOINT=http://127.0.0.1:9 MATH_INGESTION_VLM_TIMEOUT_SECONDS=1 ENGLISH_INGESTION_VLM_ENDPOINT=http://127.0.0.1:9 ENGLISH_INGESTION_VLM_TIMEOUT_SECONDS=1 PROBLEM_SELECTION_MIN_AGE_DAYS=0 uv run --directory ../backend uvicorn app.main:app --host 127.0.0.1 --port 18000",
      url: "http://127.0.0.1:18000/api/health",
      name: "Backend",
      reuseExistingServer: false,
      timeout: 120 * 1000,
    },
    {
      command: "PLAYWRIGHT_API_ORIGIN=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      name: "Frontend",
      reuseExistingServer: false,
      timeout: 120 * 1000,
    },
  ],
});
