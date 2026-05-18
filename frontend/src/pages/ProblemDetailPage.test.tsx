import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ProblemDetailPage } from "./ProblemDetailPage";

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
    mockFetch.mockReset();
    mockNavigate.mockReset();
  });

  it("renders problem details", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, graphDsl: "graph { a -- b }", correctAnswer: { display: "42", normalizedText: "42", normalizedSet: ["42"], format: "single" } } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => baseTracking,
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Problem abc123")).toBeInTheDocument();
    });

    expect(screen.getByText("single-choice")).toBeInTheDocument();
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByText("algebra")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Reference: abc123")).toBeInTheDocument();
    expect(screen.getAllByText("4").length).toBeGreaterThanOrEqual(1);
  });

  it("enters edit mode when clicking edit", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Original text", tags: ["tag1"] } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Original text")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("saves edited problem", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Original text", tags: ["tag1"] } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Edited text", tags: ["tag1", "tag2"] } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Edited text", tags: ["tag1", "tag2"] } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

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
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/problems/abc123"),
        expect.objectContaining({ method: "PATCH" }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Edited text")).toBeInTheDocument();
    });
  });

  it("navigates back on delete", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("confirm", () => true);

    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      });

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
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          problemId: "abc123",
          tracking: {
            exposureCount: 10,
            correctCount: 8,
            failedCount: 2,
            lastTestedAt: "2024-01-15T10:30:00Z",
          },
        }),
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
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test", imageUrl: "/api/v1/problems/abc123/image" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

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
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test", imageUrl: "/api/v1/problems/abc123/image" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

    renderProblemDetailPage();

    await user.click(await screen.findByRole("button", { name: /Show Original Image/i }));

    const img = await screen.findByAltText("Problem");
    fireEvent.error(img);

    await waitFor(() => {
      expect(screen.queryByAltText("Problem")).not.toBeInTheDocument();
    });
  });

  it("shows deleted indicator for soft-deleted problems", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test", isDeleted: true } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Deleted")).toBeInTheDocument();
    });
  });

  it("navigates back when clicking back button", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Test")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Back to Problems/i }));

    expect(mockNavigate).toHaveBeenCalledWith("/problems");
  });

  it("displays error on update failure", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Original text" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ error: { message: "Update failed" } }),
      });

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

    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problem: { ...baseProblem, text: "Test" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ problemId: "abc123", tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 } }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ error: { message: "Delete failed" } }),
      });

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
