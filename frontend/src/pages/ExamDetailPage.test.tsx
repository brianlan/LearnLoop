import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ExamDetailPage } from "./ExamDetailPage";

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    verifyTeacherPassword: vi.fn(),
    getSolutionStatus: vi.fn(),
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

function renderExamDetailPage(examId = "exam123") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/exams/${examId}`]}>
        <Routes>
          <Route path="/exams/:id" element={<ExamDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseExamItem = {
  itemId: "item1",
  order: 1,
  problemId: "prob1",
  problem: {
    text: "What is 2+2?",
    problemType: "single-choice",
    correctAnswer: { display: "4", normalizedText: "4", normalizedSet: ["4"], format: "single" },
  },
  answer: { raw: "4", savedAt: "2024-01-01T00:00:00Z" },
  grading: { status: "correct", retryCount: 0 },
};

const baseExam = {
  id: "exam123",
  state: "submitted",
  configSnapshot: {
    maxProblemCount: 5,
    selectionPolicy: { cooldownDays: 7, lastWrongWeight: 1.0, failureRateWeight: 1.0, recencyWeight: 1.0, minProblemAgeDays: 3 },
    generatedAt: "2024-01-01T00:00:00Z",
  },
  items: [
    { ...baseExamItem, itemId: "item1", order: 1, grading: { status: "correct", retryCount: 0 } },
    { ...baseExamItem, itemId: "item2", order: 2, grading: { status: "incorrect", retryCount: 0 } },
    { ...baseExamItem, itemId: "item3", order: 3, grading: { status: "pending-review", retryCount: 0 } },
  ],
  summary: {
    totalProblems: 3,
    answeredProblems: 3,
    gradedProblems: 2,
    pendingProblems: 1,
    correctProblems: 1,
    failedProblems: 1,
    score: 0.5,
  },
  createdAt: "2024-01-01T00:00:00Z",
  submittedAt: "2024-01-01T01:00:00Z",
  updatedAt: "2024-01-01T01:00:00Z",
};

describe("ExamDetailPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.verifyTeacherPassword).mockReset();
    vi.mocked(api.getSolutionStatus).mockReset();
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "none" });
    mockNavigate.mockReset();
  });

  it("renders exam details with summary", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Exam Results")).toBeInTheDocument();
    });

    expect(screen.getByText("50%")).toBeInTheDocument();
    // Use getAllByText since "Correct" appears multiple times (summary + grading badge)
    expect(screen.getAllByText("Correct").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Incorrect").length).toBeGreaterThan(0);
  });

  it("hides correct answers by default", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Question 1")).toBeInTheDocument();
    });

    // Correct answer should not be visible
    expect(screen.queryByText("Correct Answer:")).not.toBeInTheDocument();

    // Reveal Answer button should be visible for items with correct answers
    expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
  });

  it("shows Reveal Answer button for each item with correct answer", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
    });

    expect(screen.getByTestId("reveal-answer-item2")).toBeInTheDocument();
    expect(screen.getByTestId("reveal-answer-item3")).toBeInTheDocument();
  });

  it("opens modal when clicking Reveal Answer button", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("reveal-answer-item1"));

    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();
  });

  it("reveals only specific item's answer after verification", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
    });

    // Click reveal for item1
    await user.click(screen.getByTestId("reveal-answer-item1"));

    // Enter password and submit
    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    // Item1's answer is revealed
    await waitFor(() => {
      expect(screen.getByText("Correct Answer:")).toBeInTheDocument();
    });

    // Other items' answers remain hidden
    expect(screen.getByTestId("reveal-answer-item2")).toBeInTheDocument();
    expect(screen.getByTestId("reveal-answer-item3")).toBeInTheDocument();
  });

  it("keeps other items' answers hidden after revealing one", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
    });

    // Reveal item1's answer
    await user.click(screen.getByTestId("reveal-answer-item1"));
    await user.type(screen.getByTestId("teacher-password-input"), "teacher-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.queryByTestId("reveal-answer-item1")).not.toBeInTheDocument();
    });

    // Item2 and item3 still have Reveal Answer buttons
    expect(screen.getByTestId("reveal-answer-item2")).toBeInTheDocument();
    expect(screen.getByTestId("reveal-answer-item3")).toBeInTheDocument();
  });

  it("handles incorrect password for reveal", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.verifyTeacherPassword).mockRejectedValueOnce(
      new Error("Incorrect teacher password")
    );

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("reveal-answer-item1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("reveal-answer-item1"));
    await user.type(screen.getByTestId("teacher-password-input"), "wrong-password");
    await user.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toHaveTextContent(
        "Incorrect teacher password"
      );
    });

    // Answer remains hidden
    expect(screen.queryByText("Correct Answer:")).not.toBeInTheDocument();
  });

  it("shows pending review items with self-report buttons", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Question 3")).toBeInTheDocument();
    });

    expect(screen.getByText("I was correct")).toBeInTheDocument();
    expect(screen.getByText("I was incorrect")).toBeInTheDocument();
  });

  it("shows pending review notice when items need review", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Pending Review:")).toBeInTheDocument();
    });
  });

  it("navigates back when clicking Back to History", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Back to History" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Back to History" }));

    expect(mockNavigate).toHaveBeenCalledWith("/exams");
  });

  it("shows loading state", async () => {
    vi.mocked(api.get).mockImplementation(() => new Promise(() => {}));

    renderExamDetailPage();

    expect(screen.getByText("Loading exam details...")).toBeInTheDocument();
  });

  it("shows error state", async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new Error("Failed to load"));

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Error loading exam: Failed to load")).toBeInTheDocument();
    });
  });

  it("renders AI Explain button per item when status is ready and exam is submitted", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "ready" });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("explain-button-item1")).toBeInTheDocument();
    });

    expect(screen.getByTestId("explain-button-item1")).toHaveTextContent("AI Explain");
    expect(screen.getByTestId("explain-button-item1")).not.toBeDisabled();
    expect(screen.getByTestId("explain-button-item2")).toBeInTheDocument();
  });

  it("shows warning message when AI Explain is clicked while pending/generating in exam detail", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "pending" });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("explain-button-item1")).toBeInTheDocument();
    });

    expect(screen.getByTestId("explain-button-item1")).toHaveTextContent("AI Explain (Generating...)");

    await user.click(screen.getByTestId("explain-button-item1"));

    await waitFor(() => {
      expect(screen.getByTestId("explain-info-message-item1")).toBeInTheDocument();
    });
    expect(screen.getByTestId("explain-info-message-item1")).toHaveTextContent(
      "Solution is being generated, please try again shortly"
    );
  });

  it("disables AI Explain button when status is failed in exam detail", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ exam: baseExam });
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "failed" });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByTestId("explain-button-item1")).toBeInTheDocument();
    });

    expect(screen.getByTestId("explain-button-item1")).toHaveTextContent("AI Explain (Unavailable)");
    expect(screen.getByTestId("explain-button-item1")).toBeDisabled();
  });

  it("does NOT render AI Explain button when exam is in-progress", async () => {
    const inProgressExam = {
      ...baseExam,
      state: "in-progress",
    };
    vi.mocked(api.get).mockResolvedValueOnce({ exam: inProgressExam });
    vi.mocked(api.getSolutionStatus).mockResolvedValue({ status: "ready" });

    renderExamDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Exam Results")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("explain-button-item1")).not.toBeInTheDocument();
  });
});