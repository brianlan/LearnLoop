import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";


export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": process.env.PLAYWRIGHT_API_ORIGIN ?? "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: false,
    exclude: ["tests/**", "node_modules/**"],
  },
});
