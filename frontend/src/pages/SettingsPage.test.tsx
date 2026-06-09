import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { SettingsPage } from "./SettingsPage";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(),
    changeTeacherPassword: vi.fn(),
  },
}));

import { api } from "../api/client";

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
    timeout_seconds: 30,
  },
  math_ingestion_vlm: {
    endpoint: "https://math-ingestion.example.com",
    model: "math-ingestion-model",
    timeout_seconds: 120,
  },
  english_ingestion_vlm: {
    endpoint: "https://english-ingestion.example.com",
    model: "english-ingestion-model",
    timeout_seconds: 120,
  },
  grading_vlm: {
    endpoint: "https://grading.example.com",
    model: "grading-model",
    timeout_seconds: 60,
  },
  math_solution_vlm: {
    endpoint: "https://math-solution.example.com",
    model: "math-solution-model",
    timeout_seconds: 90,
  },
  english_solution_vlm: {
    endpoint: "https://english-solution.example.com",
    model: "english-solution-model",
    timeout_seconds: 90,
  },
  math_coaching_vlm: {
    endpoint: "https://math-coaching.example.com",
    model: "math-coaching-model",
    timeout_seconds: 45,
  },
  english_coaching_vlm: {
    endpoint: "https://english-coaching.example.com",
    model: "english-coaching-model",
    timeout_seconds: 45,
  },
  session: { cookie_name: "ll_session", secure: false, samesite: "lax" },
  practice: { cooldown_days: 7, last_wrong_weight: 1.0, failure_rate_weight: 1.0, recency_weight: 1.0 },
};

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.changeTeacherPassword).mockReset();
    vi.mocked(api.get).mockResolvedValue(mockSettings);
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
    expect(await screen.findByText("helper-model")).toBeInTheDocument();
    expect(await screen.findByText("math-ingestion-model")).toBeInTheDocument();
    expect(await screen.findByText("english-ingestion-model")).toBeInTheDocument();
    expect(await screen.findByText("grading-model")).toBeInTheDocument();
    expect(await screen.findByText("math-solution-model")).toBeInTheDocument();
    expect(await screen.findByText("english-solution-model")).toBeInTheDocument();
    expect(await screen.findByText("math-coaching-model")).toBeInTheDocument();
    expect(await screen.findByText("english-coaching-model")).toBeInTheDocument();
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

  it("renders practice settings section", async () => {
    renderWithProviders();
    expect(await screen.findByText("Practice Mode")).toBeInTheDocument();
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
