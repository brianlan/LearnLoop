import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { PracticePage } from "./PracticePage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderPracticePage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <PracticePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticePage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("renders loading state initially", async () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));

    renderPracticePage();

    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  it("renders empty state when no history", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });
    expect(screen.getByText("No practice history yet")).toBeInTheDocument();
  });

  it("renders history with per-problem summary", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [
          {
            problemId: "problem-1",
            problemText: "What is 2+2?",
            problemType: "short-answer",
            summary: {
              totalAttempts: 3,
              correctCount: 2,
              wrongCount: 1,
              lastPracticedAt: "2024-01-01T12:00:00Z",
              lastResult: "correct",
            },
            attempts: [],
          },
        ],
      }),
    });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("history-row-problem-1")).toBeInTheDocument();
    });
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // totalAttempts
  });

  it("expands row to show attempt details on click", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [
          {
            problemId: "problem-1",
            problemText: "Test problem",
            problemType: "short-answer",
            summary: {
              totalAttempts: 1,
              correctCount: 1,
              wrongCount: 0,
              lastPracticedAt: "2024-01-01T12:00:00Z",
              lastResult: "correct",
            },
            attempts: [
              {
                submittedAnswer: "my answer",
                gradingStatus: "correct",
                gradingMethod: "normalized-match",
                createdAt: "2024-01-01T12:00:00Z",
              },
            ],
          },
        ],
      }),
    });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("history-row-problem-1")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("history-row-problem-1"));

    await waitFor(() => {
      expect(screen.getByTestId("attempts-problem-1")).toBeInTheDocument();
    });
    expect(screen.getByText("Answer: my answer")).toBeInTheDocument();
  });

  it("shows Start Practice button", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("start-practice-button")).toBeInTheDocument();
    });
    expect(screen.getByText("Start Practice")).toBeInTheDocument();
  });

  it("calls start practice API on button click", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "ok", problem: { id: "p1", text: "Test", type: "short-answer" } }),
      });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("start-practice-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("start-practice-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
      const secondCall = mockFetch.mock.calls[1];
      expect(secondCall[0]).toBe("/api/v1/practice/next");
      expect(secondCall[1].method).toBe("POST");
    });
  });

  it("shows no eligible message when status is no_eligible", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "no_eligible" }),
      });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("start-practice-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("start-practice-button"));

    await waitFor(() => {
      expect(screen.getByTestId("status-message")).toBeInTheDocument();
    });
    expect(screen.getByText(/No problems available for practice/)).toBeInTheDocument();
  });

  it("shows no problems message when status is no_problems", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "no_problems" }),
      });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("start-practice-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("start-practice-button"));

    await waitFor(() => {
      expect(screen.getByTestId("status-message")).toBeInTheDocument();
    });
    expect(screen.getByText("Add some problems first to start practicing.")).toBeInTheDocument();
  });

  it("shows error state when history fetch fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      statusText: "Internal Server Error",
      json: async () => ({ error: { message: "Server error" } }),
    });

    renderPracticePage();

    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeInTheDocument();
    });
    expect(screen.getByText(/Error loading history/)).toBeInTheDocument();
  });
});