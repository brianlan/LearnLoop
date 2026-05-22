import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ProblemDetailPage } from "./ProblemDetailPage";

vi.mock("@/hooks/useTagSuggestions", () => ({
  useTagSuggestions: () => [],
}));

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    verifyTeacherPassword: vi.fn(),
  },
}));

import { api } from "@/api/client";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderProblemDetailPage(problemId = "abc123") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/problems/${problemId}`]}>
        <Routes>
          <Route path="/problems/:id" element={<ProblemDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseProblem = {
  id: "abc123",
  problemType: "single-choice",
  text: "What is 2+2?",
  tags: ["algebra", "basic"],
  graphDsl: null,
  correctAnswer: { display: "4", normalizedText: "4", normalizedSet: ["4"], format: "single" },
  imageUrl: null,
  isDeleted: false,
  createdAt: "2024-01-01",
  updatedAt: "2024-01-01",
};

const baseTracking = {
  problemId: "abc123",
  tracking: {
    exposureCount: 5,
    correctCount: 4,
    failedCount: 1,
    lastTestedAt: "2024-01-01T00:00:00Z",
    lastAttemptCorrect: true,
  },
};

describe("ProblemDetailPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.mocked(api.verifyTeacherPassword).mockReset();
    mockNavigate.mockReset();
  });

  it("renders problem details", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, graphDsl: "graph { a -- b }", correctAnswer: { display: "42", normalizedText: "42", normalizedSet: ["42"], format: "single" } } })
      .mockResolvedValueOnce(baseTracking);

    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Problem abc123")).toBeInTheDocument();
    });

    expect(screen.getByText("single-choice")).toBeInTheDocument();
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByText("algebra")).toBeInTheDocument();
    expect(screen.getByText("Reference: abc123")).toBeInTheDocument();

    // Answer is hidden by default
    expect(screen.getByRole("button", { name: "Show Answer" })).toBeInTheDocument();
    expect(screen.queryByText("42")).not.toBeInTheDocument();

    // Click to open modal
    await user.click(screen.getByRole("button", { name: "Show Answer" }));

    // Modal appears
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();

    // Enter password and submit
    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    // After verification, answer is revealed
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Hide Answer" })).toBeInTheDocument();
    });
    expect(screen.getByText("42")).toBeInTheDocument();

    // Tracking statistics still show exposure count
    expect(screen.getAllByText("4").length).toBeGreaterThanOrEqual(1);
  });

  it("enters edit mode when clicking edit", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Original text", tags: ["tag1"] } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Original text")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    // Answer input is shown directly in edit mode (no toggle needed)
    expect(screen.getByDisplayValue("4")).toBeInTheDocument();
  });

  it("hides answer by default and toggles visibility", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);

    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    });

    // Answer hidden by default - check for the answer container not existing
    expect(screen.queryByRole("button", { name: "Hide Answer" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show Answer" })).toBeInTheDocument();

    // Show answer - opens modal first
    await user.click(screen.getByRole("button", { name: "Show Answer" }));
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();

    // Enter password and submit
    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    // After verification, answer is visible
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Hide Answer" })).toBeInTheDocument();
    });

    // Hide answer again (direct toggle, no modal)
    await user.click(screen.getByRole("button", { name: "Hide Answer" }));
    expect(screen.queryByRole("button", { name: "Hide Answer" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show Answer" })).toBeInTheDocument();
  });

  it("saves edited problem", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Original text", tags: ["tag1"] } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } })
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Edited text", tags: ["tag1", "tag2"] } })
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Edited text", tags: ["tag1", "tag2"] } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.patch).mockResolvedValueOnce({ problem: { ...baseProblem, text: "Edited text", tags: ["tag1", "tag2"] } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Original text")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    const textInput = screen.getByDisplayValue("Original text");
    await user.clear(textInput);
    await user.type(textInput, "Edited text");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        "/problems/abc123",
        expect.objectContaining({ text: "Edited text" }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Edited text")).toBeInTheDocument();
    });
  });

  it("navigates back on delete", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("confirm", () => true);

    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.delete).mockResolvedValueOnce({ ok: true });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Test")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/problems");
    });

    vi.unstubAllGlobals();
  });

  it("displays tracking statistics", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce({
        problemId: "abc123",
        tracking: {
          exposureCount: 10,
          correctCount: 8,
          failedCount: 2,
          lastTestedAt: "2024-01-15T10:30:00Z",
        },
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Tracking Statistics")).toBeInTheDocument();
    });

    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("displays image when imageUrl exists", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test", imageUrl: "/api/v1/problems/abc123/image" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await screen.findByRole("button", { name: /Show Original Image/i });
    expect(screen.queryByAltText("Problem")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Show Original Image/i }));

    const img = await screen.findByAltText("Problem");
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "/api/v1/problems/abc123/image");
  });

  it("hides broken image after load error", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test", imageUrl: "/api/v1/problems/abc123/image" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await user.click(await screen.findByRole("button", { name: /Show Original Image/i }));

    const img = await screen.findByAltText("Problem");
    fireEvent.error(img);

    await waitFor(() => {
      expect(screen.queryByAltText("Problem")).not.toBeInTheDocument();
    });
  });

  it("shows deleted indicator for soft-deleted problems", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test", isDeleted: true } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Deleted")).toBeInTheDocument();
    });
  });

  it("navigates back when clicking back button", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Test")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Back to Problems/i }));

    expect(mockNavigate).toHaveBeenCalledWith("/problems");
  });

  it("displays error on update failure", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Original text" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.patch).mockRejectedValueOnce(new Error("Update failed"));

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Original text")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    const textInput = screen.getByDisplayValue("Original text");
    await user.clear(textInput);
    await user.type(textInput, "New text");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Update failed/);
    });
  });

  it("displays error on delete failure", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("confirm", () => true);

    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.delete).mockRejectedValueOnce(new Error("Delete failed"));

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Test")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Delete failed/);
    });

    vi.unstubAllGlobals();
  });
});