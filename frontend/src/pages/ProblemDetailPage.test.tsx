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
    setProblemDisabled: vi.fn(),
    getSolutionStatus: vi.fn(),
    regenerateSolution: vi.fn(),
  },
}));

vi.mock("@/components/GraphSandbox", () => ({
  GraphSandbox: ({ dsl, height }: { dsl: string; height?: number }) => (
    <div data-testid="graph-sandbox" data-dsl={dsl} data-height={height}>
      GraphSandbox
    </div>
  ),
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
  isDisabled: false,
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
  practiceWeight: {
    lastWrong: 1.0,
    failure: 1.2,
    recency: 2.5,
    total: 4.7,
  },
};

describe("ProblemDetailPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.mocked(api.verifyTeacherPassword).mockReset();
    vi.mocked(api.setProblemDisabled).mockReset();
    vi.mocked(api.getSolutionStatus).mockReset();
    vi.mocked(api.regenerateSolution).mockReset();
    mockNavigate.mockReset();

    // Default: no solution status
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "none" });
    vi.mocked(api.regenerateSolution).mockResolvedValue({ status: "pending" });
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
  });

  it("renders graph DSL with 300px height and no 400px max-width wrapper", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, graphDsl: "board.create('point', [0, 0]);" } })
      .mockResolvedValueOnce(baseTracking);

    renderProblemDetailPage();

    const sandbox = await screen.findByTestId("graph-sandbox");
    expect(sandbox).toHaveAttribute("data-dsl", "board.create('point', [0, 0]);");
    expect(sandbox).toHaveAttribute("data-height", "300");

    const wrapper = sandbox.parentElement;
    expect(wrapper).not.toHaveStyle({ maxWidth: "400px" });
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

    // In edit mode, answer input is hidden by default
    expect(screen.queryByTestId("edit-answer-input")).not.toBeInTheDocument();
    // "Edit Answer" button is visible
    expect(screen.getByTestId("edit-answer-button")).toBeInTheDocument();
  });

  it("reveals answer input after teacher password verification in edit mode", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Original text", tags: ["tag1"] } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Original text")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    // Click "Edit Answer" button to open modal
    await user.click(screen.getByTestId("edit-answer-button"));
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();

    // Enter password and submit
    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    // After verification, answer input is revealed with correct value
    await waitFor(() => {
      expect(screen.getByTestId("edit-answer-input")).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue("4")).toBeInTheDocument();
  });

  it("hides answer by default and toggles visibility in view mode", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);

    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    });

    // Answer hidden by default
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

  it("displays practice weight and hover breakdown", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce(baseTracking);

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("practice-weight")).toBeInTheDocument();
    });

    expect(screen.getByText("4.70")).toBeInTheDocument();

    // Breakdown hidden by default
    expect(screen.queryByTestId("practice-weight-breakdown")).not.toBeInTheDocument();

    // Hover to show breakdown
    await user.hover(screen.getByTestId("practice-weight"));
    await waitFor(() => {
      expect(screen.getByTestId("practice-weight-breakdown")).toBeInTheDocument();
    });

    expect(screen.getByText("last_wrong: 1.00")).toBeInTheDocument();
    expect(screen.getByText("failure: 1.20")).toBeInTheDocument();
    expect(screen.getByText("recency: 2.50")).toBeInTheDocument();
    expect(screen.getByText("total: 4.70")).toBeInTheDocument();

    // Move mouse away to hide
    await user.unhover(screen.getByTestId("practice-weight"));
    await waitFor(() => {
      expect(screen.queryByTestId("practice-weight-breakdown")).not.toBeInTheDocument();
    });
  });

  it("shows practice weight breakdown on focus", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce(baseTracking);

    renderProblemDetailPage();

    const weight = await screen.findByTestId("practice-weight");
    await user.click(weight);

    await waitFor(() => {
      expect(screen.getByTestId("practice-weight-breakdown")).toBeInTheDocument();
    });

    expect(screen.getByText("last_wrong: 1.00")).toBeInTheDocument();
    expect(screen.getByText("failure: 1.20")).toBeInTheDocument();
    expect(screen.getByText("recency: 2.50")).toBeInTheDocument();
    expect(screen.getByText("total: 4.70")).toBeInTheDocument();
  });

  it("derives displayed total from displayed components to avoid rounding mismatch", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, text: "Test" } })
      .mockResolvedValueOnce({
        problemId: "abc123",
        tracking: {
          exposureCount: 10,
          correctCount: 5,
          failedCount: 5,
          lastTestedAt: "2024-01-15T10:30:00Z",
          lastAttemptCorrect: true,
        },
        practiceWeight: {
          lastWrong: 1.0,
          failure: 1.1666666667,
          recency: 1.1666666667,
          total: 3.3333333334,
        },
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("practice-weight")).toBeInTheDocument();
    });

    // The exact backend total is 3.3333333334, but the displayed total should
    // equal the sum of displayed components: 1.00 + 1.17 + 1.17 = 3.34
    expect(screen.getByText("3.34")).toBeInTheDocument();

    await user.hover(screen.getByTestId("practice-weight"));
    await waitFor(() => {
      expect(screen.getByTestId("practice-weight-breakdown")).toBeInTheDocument();
    });

    expect(screen.getByText("last_wrong: 1.00")).toBeInTheDocument();
    expect(screen.getByText("failure: 1.17")).toBeInTheDocument();
    expect(screen.getByText("recency: 1.17")).toBeInTheDocument();
    expect(screen.getByText("total: 3.34")).toBeInTheDocument();
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

  it("edit form shows problem type select prefilled with current type", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "short-answer" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("short-answer")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    const select = screen.getByTestId("problem-type-input");
    expect(select).toBeInTheDocument();
    expect(select).toHaveValue("short-answer");
  });

  it("changing problem type sends it in PATCH", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "short-answer" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } })
      .mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "fill-in-the-blank" } })
      .mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "fill-in-the-blank" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    vi.mocked(api.patch).mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "fill-in-the-blank" } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("short-answer")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    const select = screen.getByTestId("problem-type-input");
    await user.selectOptions(select, "fill-in-the-blank");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        "/problems/abc123",
        expect.objectContaining({ problemType: "fill-in-the-blank" }),
      );
    });
  });

  it("changing problem type does not reveal answer input", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, problemType: "short-answer" } })
      .mockResolvedValueOnce({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("short-answer")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    // Answer input should not be visible
    expect(screen.queryByTestId("edit-answer-input")).not.toBeInTheDocument();

    // Change problem type
    const select = screen.getByTestId("problem-type-input");
    await user.selectOptions(select, "fill-in-the-blank");

    // Answer input should still not be visible
    expect(screen.queryByTestId("edit-answer-input")).not.toBeInTheDocument();

    // Edit Answer button should still be visible (teacher-password gated)
    expect(screen.getByTestId("edit-answer-button")).toBeInTheDocument();
  });

  it("renders Solution Generated and AI Explain button when status is ready", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "ready" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("solution-status")).toHaveTextContent("Solution Generated");
    });
    expect(screen.getByTestId("ai-explain-button")).toBeInTheDocument();
    expect(screen.getByTestId("ai-explain-button")).toHaveTextContent("AI Explain");
  });

  it("clicking AI Explain navigates to coaching page with from state", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "ready" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("ai-explain-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("ai-explain-button"));

    expect(mockNavigate).toHaveBeenCalledWith("/coaching/abc123", {
      state: { from: "/problems/abc123" },
    });
  });

  it("renders Solution Pending and no AI Explain button when status is pending", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "pending" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("solution-status")).toHaveTextContent("Solution Pending");
    });
    expect(screen.queryByTestId("ai-explain-button")).not.toBeInTheDocument();
  });

  it("renders Solution Generating and no AI Explain button when status is generating", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "generating" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("solution-status")).toHaveTextContent("Solution Generating");
    });
    expect(screen.queryByTestId("ai-explain-button")).not.toBeInTheDocument();
  });

  it("renders Solution Failed and no AI Explain button when status is failed", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("solution-status")).toHaveTextContent("Solution Failed");
    });
    expect(screen.queryByTestId("ai-explain-button")).not.toBeInTheDocument();
  });

  it("renders Solution Not Started when status is none", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "none" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("solution-status")).toHaveTextContent("Solution Not Started");
    });
    expect(screen.queryByTestId("ai-explain-button")).not.toBeInTheDocument();
  });

  it("renders Re-generate solution button when status is ready", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "ready" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("regenerate-solution-button")).toHaveTextContent("Re-generate solution");
  });

  it("renders Re-generate solution button when status is failed", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeInTheDocument();
    });
  });

  it("does not render Re-generate solution button for none, pending, or generating", async () => {
    for (const status of ["none", "pending", "generating"]) {
      vi.mocked(api.get).mockReset();
      vi.mocked(api.get)
        .mockResolvedValueOnce({ problem: baseProblem })
        .mockResolvedValueOnce(baseTracking);
      vi.mocked(api.getSolutionStatus).mockResolvedValue({ status });

      renderProblemDetailPage();

      await waitFor(() => {
        expect(screen.getByTestId("solution-status")).toBeInTheDocument();
      });
      expect(screen.queryByTestId("regenerate-solution-button")).not.toBeInTheDocument();
    }
  });

  it("calls regenerateSolution API and invalidates solution status on click", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("regenerate-solution-button"));

    await waitFor(() => {
      expect(vi.mocked(api.regenerateSolution)).toHaveBeenCalledWith("abc123");
    });
    // After success, solution status is refetched (invalidated)
    await waitFor(() => {
      expect(vi.mocked(api.getSolutionStatus).mock.calls.length).toBeGreaterThan(1);
    });
  });

  it("disables Re-generate button while mutation is pending", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });
    // Never resolves so mutation stays pending
    vi.mocked(api.regenerateSolution).mockReturnValue(new Promise(() => {}));

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("regenerate-solution-button"));

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeDisabled();
    });
    expect(screen.getByTestId("regenerate-solution-button")).toHaveTextContent("Regenerating...");
  });

  it("displays error when regeneration fails", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });
    vi.mocked(api.regenerateSolution).mockRejectedValue(new Error("Server error"));

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("regenerate-solution-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("regenerate-solution-button"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Server error");
    });
  });

  it("renders Disable button between Edit and Delete for enabled problems", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    });

    const editButton = screen.getByRole("button", { name: "Edit" });
    const disableButton = screen.getByTestId("toggle-disabled-button");
    const deleteButton = screen.getByRole("button", { name: "Delete" });

    expect(disableButton).toHaveTextContent("Disable");
    // Disable is positioned between Edit and Delete in the DOM
    expect(
      editButton.compareDocumentPosition(disableButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      disableButton.compareDocumentPosition(deleteButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByTestId("disabled-badge")).not.toBeInTheDocument();
  });

  it("renders Enable button and Disabled badge for disabled problems", async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: { ...baseProblem, isDisabled: true } })
      .mockResolvedValueOnce(baseTracking);

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("disabled-badge")).toBeInTheDocument();
    });
    expect(screen.getByTestId("toggle-disabled-button")).toHaveTextContent("Enable");
  });

  it("disables a problem via teacher password modal and refreshes state", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking)
      .mockResolvedValueOnce({ problem: { ...baseProblem, isDisabled: true } });

    vi.mocked(api.setProblemDisabled).mockResolvedValueOnce({
      problem: { ...baseProblem, isDisabled: true },
    });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("toggle-disabled-button")).toHaveTextContent("Disable");
    });

    await user.click(screen.getByTestId("toggle-disabled-button"));
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();

    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(api.setProblemDisabled).toHaveBeenCalledWith(
        "abc123",
        true,
        "teacher-password",
      );
    });

    // After success, problem is refreshed: Enable button + Disabled badge
    await waitFor(() => {
      expect(screen.getByTestId("toggle-disabled-button")).toHaveTextContent("Enable");
      expect(screen.getByTestId("disabled-badge")).toBeInTheDocument();
    });
  });

  it("surfaces error in modal on incorrect teacher password and leaves state unchanged", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get)
      .mockResolvedValueOnce({ problem: baseProblem })
      .mockResolvedValueOnce(baseTracking);

    vi.mocked(api.setProblemDisabled).mockRejectedValueOnce(
      new Error("Incorrect teacher password"),
    );

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("toggle-disabled-button")).toHaveTextContent("Disable");
    });

    await user.click(screen.getByTestId("toggle-disabled-button"));
    await user.type(screen.getByTestId("teacher-password-input"), "wrong-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toHaveTextContent(
        "Incorrect teacher password",
      );
    });

    // Modal stays open and problem state is unchanged
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-disabled-button")).toHaveTextContent("Disable");
    expect(screen.queryByTestId("disabled-badge")).not.toBeInTheDocument();
  });
});