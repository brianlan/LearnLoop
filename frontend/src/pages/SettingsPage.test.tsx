import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { SettingsPage } from "./SettingsPage";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn().mockResolvedValue({
      app: { env: "development", host: "0.0.0.0", port: 8000, log_level: "INFO" },
      database: { name: "learnloop" },
      storage: { endpoint: "http://localhost:9000", bucket: "media", region: "us-east-1", force_path_style: true },
      vlm: { endpoint: "https://example.com", model: "test", timeout_seconds: 120, preview_extracting_window_seconds: 150 },
      session: { cookie_name: "ll_session", secure: false, samesite: "lax" },
      practice: { cooldown_days: 7, last_wrong_weight: 1.0, failure_rate_weight: 1.0, recency_weight: 1.0 },
    }),
  },
}));

function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <SettingsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("SettingsPage", () => {
  it("renders settings heading", async () => {
    renderWithProviders();
    expect(await screen.findByText("Settings")).toBeInTheDocument();
  });

  it("renders read-only notice", async () => {
    renderWithProviders();
    expect(await screen.findByText(/read-only/i)).toBeInTheDocument();
  });

  it("renders app settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Application")).toBeInTheDocument();
    expect(await screen.findByText("Environment")).toBeInTheDocument();
    expect(await screen.findByText("development")).toBeInTheDocument();
  });

  it("renders database settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Database")).toBeInTheDocument();
    expect(await screen.findByText("learnloop")).toBeInTheDocument();
  });

  it("renders storage settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Storage (S3)")).toBeInTheDocument();
    expect(await screen.findByText("http://localhost:9000")).toBeInTheDocument();
  });

  it("renders VLM settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Vision Language Model")).toBeInTheDocument();
    expect(await screen.findByText("test")).toBeInTheDocument();
  });

  it("renders session settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Session")).toBeInTheDocument();
    expect(await screen.findByText("ll_session")).toBeInTheDocument();
  });

  it("renders practice settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Practice Mode")).toBeInTheDocument();
    expect(await screen.findByText("7")).toBeInTheDocument();
  });
});
