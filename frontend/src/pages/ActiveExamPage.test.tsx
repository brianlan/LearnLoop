import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ActiveExamPage } from "./ActiveExamPage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

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

function renderActiveExamPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ActiveExamPage />
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
    problemType: "fill-in-the-blank",
    correctAnswer: { display: "4", normalizedText: "4", normalizedSet: ["4"], format: "single" },
  },
  answer: { raw: "", savedAt: undefined },
  grading: { status: "ungraded", retryCount: 0 },
};

const baseExam = {
  id: "exam123",
  state: "in-progress",
  configSnapshot: {
    maxProblemCount: 5,
    selectionPolicy: { recencyWeight: 1.0, failureWeight: 1.0 },
    generatedAt: "2024-01-01T00:00:00Z",
  },
  items: [baseExamItem],
  summary: {
    totalProblems: 1,
    answeredProblems: 0,
    gradedProblems: 0,
    pendingProblems: 0,
    correctProblems: 0,
    failedProblems: 0,
    score: null,
  },
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
};

describe("ActiveExamPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockNavigate.mockReset();
  });

  it("shows loading state initially", () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));
    renderActiveExamPage();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("shows no active exam message for 404 response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ error: { message: "No active exam" } }),
    });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("No active exam found.")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Start New Exam" })).toBeInTheDocument();
  });

  it("shows generic API error when create exam fails with 500", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: async () => ({ error: { message: "No active exam" } }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: async () => ({ error: { message: "Something went wrong" } }),
      });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("No active exam found.")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    await waitFor(() => {
      expect(screen.getByText(/Something went wrong/)).toBeInTheDocument();
    });
  });

  it("renders active exam with problem text and navigation", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ exam: baseExam }),
    });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("Active Exam")).toBeInTheDocument();
    });

    expect(screen.getByText(/Question 1 of 1/)).toBeInTheDocument();
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit Exam" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
  });

  it("saves answer on blur after editing", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: baseExam }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          item: { ...baseExamItem, answer: { raw: "42", savedAt: "2024-01-01T01:00:00Z" } },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exam: {
            ...baseExam,
            items: [{ ...baseExamItem, answer: { raw: "42", savedAt: "2024-01-01T01:00:00Z" } }],
          },
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ exam: baseExam }),
      });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("Active Exam")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "42");
    await user.tab();

    await waitFor(() => {
      expect(mockFetch.mock.calls.some((call) =>
        String(call[0]).includes("/api/v1/exams/exam123/items/item1/answer")
      )).toBe(true);
    });

    const patchCall = mockFetch.mock.calls.find((call) =>
      String(call[0]).includes("/api/v1/exams/exam123/items/item1/answer")
    );
    expect(patchCall).toBeDefined();
    expect(patchCall![1].method).toBe("PATCH");
    expect(JSON.parse(patchCall![1].body)).toEqual({ answer: "42" });
  });

  it("saves answer when navigating to next question", async () => {
    const examWithTwoItems = {
      ...baseExam,
      items: [
        baseExamItem,
        {
          ...baseExamItem,
          itemId: "item2",
          order: 2,
          problem: {
            text: "What is 3+3?",
            problemType: "fill-in-the-blank",
          },
          answer: { raw: "", savedAt: undefined },
        },
      ],
      summary: {
        ...baseExam.summary,
        totalProblems: 2,
      },
    };

    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: examWithTwoItems }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          item: { ...baseExamItem, answer: { raw: "42", savedAt: "2024-01-01T01:00:00Z" } },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exam: {
            ...examWithTwoItems,
            items: [
              { ...baseExamItem, answer: { raw: "42", savedAt: "2024-01-01T01:00:00Z" } },
              examWithTwoItems.items[1],
            ],
          },
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ exam: examWithTwoItems }),
      });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("Active Exam")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "42");

    await user.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => {
      expect(mockFetch.mock.calls.some((call) =>
        String(call[0]).includes("/api/v1/exams/exam123/items/item1/answer")
      )).toBe(true);
    });

    const patchCall = mockFetch.mock.calls.find((call) =>
      String(call[0]).includes("/api/v1/exams/exam123/items/item1/answer")
    );
    expect(patchCall).toBeDefined();
    expect(patchCall![1].method).toBe("PATCH");
  });

  it("shows submit confirmation modal and navigates to exam detail on confirm", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: baseExam }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: { ...baseExam, state: "submitted" } }),
      });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("Active Exam")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Submit Exam" }));

    await waitFor(() => {
      expect(screen.getByText("Submit Exam?")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const submitCall = mockFetch.mock.calls[1];
    expect(submitCall[0]).toContain("/api/v1/exams/exam123/submit");
    expect(submitCall[1].method).toBe("POST");

    expect(mockNavigate).toHaveBeenCalledWith("/exams/exam123");
  });

  it("shows discard confirmation modal and navigates to exams list on confirm", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: baseExam }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ exam: { ...baseExam, state: "discarded" } }),
      });

    renderActiveExamPage();

    await waitFor(() => {
      expect(screen.getByText("Active Exam")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Discard" }));

    await waitFor(() => {
      expect(screen.getByText("Discard Exam?")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Discard Exam" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const discardCall = mockFetch.mock.calls[1];
    expect(discardCall[0]).toContain("/api/v1/exams/exam123/discard");
    expect(discardCall[1].method).toBe("POST");

    expect(mockNavigate).toHaveBeenCalledWith("/exams");
  });
});
