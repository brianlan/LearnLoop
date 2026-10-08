import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { SettingsPage } from "./SettingsPage";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    changeTeacherPassword: vi.fn(),
  },
}));

import { api } from "../api/client";

// Mutable per-test health snapshot served at /settings/vlm-health (#654).
let healthResponse: Record<string, unknown> = {
  running: false,
  started_at: "2026-10-01T09:00:00Z",
  finished_at: "2026-10-01T09:00:05Z",
  profiles: {
    helper_vlm: { status: "ok", checked_at: "2026-10-01T09:00:01Z" },
    grading_vlm: {
      status: "unavailable",
      reason: "connection refused",
      code: "vlm-network-error",
      attempts: 3,
      checked_at: "2026-10-01T09:00:03Z",
    },
    variant_validator_vlm: { status: "misconfigured", checked_at: "2026-10-01T09:00:04Z" },
    variant_validator2_vlm: { status: "unconfigured", checked_at: "2026-10-01T09:00:05Z" },
  },
};

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

const mockSettings = {
  app: { env: "development", host: "0.0.0.0", port: 8000, log_level: "INFO" },
  database: { name: "learnloop" },
  storage: { endpoint: "http://localhost:9000", bucket: "media", region: "us-east-1", force_path_style: true },
  preview_extracting_window_seconds: 150,
  helper_vlm: {
    endpoint: "https://helper.example.com",
    model: "helper-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 30,
    status: "configured",
  },
  math_ingestion_vlm: {
    endpoint: "https://math-ingestion.example.com",
    model: "math-ingestion-model",
    provider: "ollama",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 120,
    status: "configured",
  },
  english_ingestion_vlm: {
    endpoint: "https://english-ingestion.example.com",
    model: "english-ingestion-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 120,
    status: "configured",
  },
  grading_vlm: {
    endpoint: "https://grading.example.com",
    model: "grading-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 60,
    status: "configured",
  },
  math_solution_vlm: {
    endpoint: "https://math-solution.example.com",
    model: "math-solution-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 90,
    status: "configured",
  },
  english_solution_vlm: {
    endpoint: "https://english-solution.example.com",
    model: "english-solution-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 90,
    status: "configured",
  },
  math_coaching_vlm: {
    endpoint: "https://math-coaching.example.com",
    model: "math-coaching-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 45,
    status: "configured",
  },
  english_coaching_vlm: {
    endpoint: "https://english-coaching.example.com",
    model: "english-coaching-model",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 45,
    status: "configured",
  },
  variant_generator_vlm: {
    endpoint: "https://variant-generator.example.com",
    model: "variant-generator-model",
    provider: "openai",
    api_mode: "responses",
    reasoning_effort: "high",
    timeout_seconds: 120,
    status: "configured",
  },
  variant_validator_vlm: {
    endpoint: "https://variant-validator.example.com",
    model: "validator-1",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "none",
    timeout_seconds: 120,
    status: "misconfigured",
  },
  variant_validator2_vlm: {
    endpoint: "https://example-variant-validator2-vlm-provider.invalid/api",
    model: "replace-me",
    provider: "openai",
    api_mode: "chat",
    reasoning_effort: "high",
    timeout_seconds: 120,
    status: "unconfigured",
  },
  session: { cookie_name: "ll_session", secure: false, samesite: "lax" },
  problem_selection: { cooldown_days: 7, last_wrong_weight: 1.0, failure_rate_weight: 1.0, recency_weight: 1.0, min_problem_age_days: 3 },
};

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.changeTeacherPassword).mockReset();
    healthResponse = {
      running: false,
      started_at: "2026-10-01T09:00:00Z",
      finished_at: "2026-10-01T09:00:05Z",
      profiles: {
        helper_vlm: { status: "ok", checked_at: "2026-10-01T09:00:01Z" },
        grading_vlm: {
          status: "unavailable",
          reason: "connection refused",
          code: "vlm-network-error",
          attempts: 3,
          checked_at: "2026-10-01T09:00:03Z",
        },
        variant_validator_vlm: { status: "misconfigured", checked_at: "2026-10-01T09:00:04Z" },
        variant_validator2_vlm: { status: "unconfigured", checked_at: "2026-10-01T09:00:05Z" },
      },
    };
    vi.mocked(api.get).mockImplementation(((url: string) =>
      Promise.resolve(
        url === "/settings/vlm-health" ? healthResponse : mockSettings,
      )) as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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

  it("renders all VLM settings sections", async () => {
    renderWithProviders();
    expect(await screen.findByText("Helper VLM")).toBeInTheDocument();
    expect(await screen.findByText("Math Ingestion VLM")).toBeInTheDocument();
    expect(await screen.findByText("English Ingestion VLM")).toBeInTheDocument();
    expect(await screen.findByText("Grading VLM")).toBeInTheDocument();
    expect(await screen.findByText("Math Solution VLM")).toBeInTheDocument();
    expect(await screen.findByText("English Solution VLM")).toBeInTheDocument();
    expect(await screen.findByText("Math Coaching VLM")).toBeInTheDocument();
    expect(await screen.findByText("English Coaching VLM")).toBeInTheDocument();
    // Variant profiles (#652); validator2 is optional and labeled as such.
    expect(await screen.findByText("Variant Generator VLM")).toBeInTheDocument();
    expect(await screen.findByText("Variant Validator VLM")).toBeInTheDocument();
    expect(
      await screen.findByText("Variant Validator2 VLM (optional)"),
    ).toBeInTheDocument();
    expect(await screen.findByText("helper-model")).toBeInTheDocument();
    expect(await screen.findByText("math-ingestion-model")).toBeInTheDocument();
    expect(await screen.findByText("english-ingestion-model")).toBeInTheDocument();
    expect(await screen.findByText("grading-model")).toBeInTheDocument();
    expect(await screen.findByText("math-solution-model")).toBeInTheDocument();
    expect(await screen.findByText("english-solution-model")).toBeInTheDocument();
    expect(await screen.findByText("math-coaching-model")).toBeInTheDocument();
    expect(await screen.findByText("english-coaching-model")).toBeInTheDocument();
    expect(await screen.findByText("variant-generator-model")).toBeInTheDocument();
    expect(await screen.findByText("validator-1")).toBeInTheDocument();
    expect(await screen.findByText("replace-me")).toBeInTheDocument();
  });

  it("renders VLM provider values", async () => {
    renderWithProviders();
    expect(await screen.findAllByText("Provider")).toHaveLength(11);
    expect(await screen.findByText("ollama")).toBeInTheDocument();
  });

  it("renders VLM api mode values", async () => {
    renderWithProviders();
    expect(await screen.findAllByText("API mode")).toHaveLength(11);
    expect(await screen.findByText("responses")).toBeInTheDocument();
  });

  it("renders VLM reasoning effort values (#677)", async () => {
    renderWithProviders();
    expect(await screen.findAllByText("Reasoning effort")).toHaveLength(11);
    expect(await screen.findByText("none")).toBeInTheDocument();
  });

  it("renders a status badge per VLM profile with hints for problem states", async () => {
    renderWithProviders();
    // 8 existing + variant generator are configured; validator is
    // misconfigured; optional validator2 is unconfigured.
    expect(await screen.findAllByText("configured")).toHaveLength(9);
    expect(screen.getByText("misconfigured")).toBeInTheDocument();
    expect(
      screen.getByText(/misconfigured: check env var names/i),
    ).toBeInTheDocument();
    expect(screen.getByText("unconfigured")).toBeInTheDocument();
    expect(
      screen.getByText(/unconfigured.*optional/i),
    ).toBeInTheDocument();
  });

  it("renders a health badge per probed profile plus the vision-only note (#654)", async () => {
    renderWithProviders();
    expect(await screen.findByText("OK")).toBeInTheDocument();
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText(/connection refused/)).toBeInTheDocument();
    expect(screen.getByText("MISCONFIGURED")).toBeInTheDocument();
    expect(screen.getByText("UNCONFIGURED")).toBeInTheDocument();
    expect(
      screen.getByText(/vision-only/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/last checked/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-run" })).toBeEnabled();
  });

  it("triggers a health re-run on click (#654)", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Re-run" }));

    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/settings/vlm-health/run", {});
  });

  it("disables re-run and shows checking rows while a run is in flight (#654)", async () => {
    healthResponse = { running: true, started_at: "t0", finished_at: null, profiles: {} };
    renderWithProviders();

    expect(await screen.findByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(await screen.findAllByText("checking…")).toHaveLength(11);
  });

  it("renders a placeholder instead of Invalid Date during the initial in-flight run (#654)", async () => {
    healthResponse = { running: true, started_at: null, finished_at: null, profiles: {} };
    renderWithProviders();

    expect((await screen.findAllByText("Checking…")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/last checked/i)).not.toBeInTheDocument();
  });

  it("polls health every 5s while a run is in flight (#654)", async () => {
    vi.useFakeTimers();
    healthResponse = { running: true, started_at: "t0", finished_at: null, profiles: {} };
    renderWithProviders();
    await vi.advanceTimersByTimeAsync(0);
    const healthCalls = () =>
      vi.mocked(api.get).mock.calls.filter((call) => call[0] === "/settings/vlm-health").length;
    expect(healthCalls()).toBeGreaterThanOrEqual(1);

    await vi.advanceTimersByTimeAsync(5000);

    expect(healthCalls()).toBeGreaterThanOrEqual(2);
  });

  it("does not reference stale VLM keys", async () => {
    renderWithProviders();
    await screen.findByText("Settings");
    expect(screen.queryByText("Ingestion VLM")).not.toBeInTheDocument();
    expect(screen.queryByText("Solution VLM")).not.toBeInTheDocument();
    expect(screen.queryByText("Coaching VLM")).not.toBeInTheDocument();
  });

  it("renders session settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Session")).toBeInTheDocument();
    expect(await screen.findByText("ll_session")).toBeInTheDocument();
  });

  it("renders problem selection settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Problem Selection")).toBeInTheDocument();
    expect(await screen.findByText("7")).toBeInTheDocument();
  });

  it("renders Teacher Password section with change button", async () => {
    renderWithProviders();
    expect(await screen.findByText("Teacher Password")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Change Teacher Password" })).toBeInTheDocument();
  });

  it("opens modal when clicking Change Teacher Password button", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    expect(screen.getByTestId("change-password-modal")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Change Teacher Password" })).toBeInTheDocument();
  });

  it("shows error when passwords do not match", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    await user.type(screen.getByTestId("current-password-input"), "current-password");
    await user.type(screen.getByTestId("new-password-input"), "new-password");
    await user.type(screen.getByTestId("confirm-password-input"), "different-password");

    await user.click(screen.getByTestId("change-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("change-password-error")).toHaveTextContent("New passwords do not match");
    });
  });

  it("shows error when fields are empty", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    await user.click(screen.getByTestId("change-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("change-password-error")).toHaveTextContent("All fields are required");
    });
  });

  it("shows error when wrong current password is provided", async () => {
    const user = userEvent.setup();
    vi.mocked(api.changeTeacherPassword).mockRejectedValueOnce(
      new Error("Incorrect teacher password")
    );

    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    await user.type(screen.getByTestId("current-password-input"), "wrong-password");
    await user.type(screen.getByTestId("new-password-input"), "new-password");
    await user.type(screen.getByTestId("confirm-password-input"), "new-password");

    await user.click(screen.getByTestId("change-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("change-password-error")).toHaveTextContent("Incorrect current password");
    });
  });

  it("shows success message on successful change", async () => {
    const user = userEvent.setup();
    vi.mocked(api.changeTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    await user.type(screen.getByTestId("current-password-input"), "current-password");
    await user.type(screen.getByTestId("new-password-input"), "new-password");
    await user.type(screen.getByTestId("confirm-password-input"), "new-password");

    await user.click(screen.getByTestId("change-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("success-message")).toHaveTextContent("Teacher password changed successfully");
    });
  });

  it("closes modal on cancel", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(await screen.findByRole("button", { name: "Change Teacher Password" }));

    expect(screen.getByTestId("change-password-modal")).toBeInTheDocument();

    await user.click(screen.getByTestId("change-password-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("change-password-modal")).not.toBeInTheDocument();
    });
  });
});
