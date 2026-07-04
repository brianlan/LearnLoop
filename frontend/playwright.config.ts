import { defineConfig } from "@playwright/test";

process.env.PLAYWRIGHT_API_ORIGIN ??= "http://127.0.0.1:18000";

export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/compose-smoke.spec.ts"],
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
  },
  webServer: [
    {
      command: "(node tests/fake-vlm-server.cjs &) && for i in {1..30}; do if curl -s http://127.0.0.1:18001/health >/dev/null; then break; fi; sleep 0.5; done && HELPER_VLM_ENDPOINT=http://127.0.0.1:18001 HELPER_VLM_API_KEY=fake HELPER_VLM_TIMEOUT_SECONDS=30 MATH_INGESTION_VLM_ENDPOINT=http://127.0.0.1:18001 MATH_INGESTION_VLM_API_KEY=fake MATH_INGESTION_VLM_TIMEOUT_SECONDS=30 ENGLISH_INGESTION_VLM_ENDPOINT=http://127.0.0.1:18001 ENGLISH_INGESTION_VLM_API_KEY=fake ENGLISH_INGESTION_VLM_TIMEOUT_SECONDS=30 BULK_INGESTION_EXTRACTION_CONCURRENCY=1 PROBLEM_SELECTION_MIN_AGE_DAYS=0 uv run --directory ../backend uvicorn app.main:app --host 127.0.0.1 --port 18000",
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
