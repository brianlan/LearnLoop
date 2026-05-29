import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ExamsPage } from "./ExamsPage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderExamsPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ExamsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("shows the discarded toggle even when submitted exam history is empty", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [], page: 1, pageSize: 10, total: 0 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            {
              id: "exam-discarded",
              state: "discarded",
              createdAt: "2024-01-01T00:00:00Z",
              discardedAt: "2024-01-01T01:00:00Z",
              summary: {
                totalProblems: 1,
                answeredProblems: 0,
                gradedProblems: 0,
                pendingProblems: 0,
                correctProblems: 0,
                failedProblems: 0,
                score: null,
              },
            },
          ],
          page: 1,
          pageSize: 10,
          total: 1,
        }),
      });

    renderExamsPage();

    const toggle = await screen.findByLabelText("Show discarded");
    expect(screen.getByText("No submitted exams yet")).toBeInTheDocument();

    await user.click(toggle);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenLastCalledWith(
        expect.stringContaining("includeDiscarded=true"),
        expect.any(Object),
      );
    });
    expect(await screen.findByText("Exam exam-discarded")).toBeInTheDocument();
  });
});
