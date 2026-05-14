import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

function renderProblemDetailPage(problemId = "1") {
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

describe("ProblemDetailPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockNavigate.mockReset();
  });

  it("renders problem details", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "What is 2+2?",
          tags: ["algebra", "basic"],
          graphDsl: "graph { a -- b }",
          correctAnswer: "42",
          isDeleted: false,
          createdAt: "2024-01-01",
          updatedAt: "2024-01-01",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 5,
          correctCount: 4,
          failedCount: 1,
          lastTestedAt: "2024-01-01T00:00:00Z",
        }),
      });

    renderProblemDetailPage();

    await waitFor(() => {
      expect(screen.getByText("Problem #1")).toBeInTheDocument();
    });

    expect(screen.getByText("math")).toBeInTheDocument();
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByText("algebra")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getAllByText("4").length).toBeGreaterThanOrEqual(1);
  });

  it("enters edit mode when clicking edit", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "Original text",
          tags: ["tag1"],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Original text",
          tags: ["tag1"],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "Edited text",
          tags: ["tag1", "tag2"],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "Edited text",
          tags: ["tag1", "tag2"],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
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
        expect.stringContaining("/problems/1"),
        expect.objectContaining({ method: "PUT" }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 10,
          correctCount: 8,
          failedCount: 2,
          lastTestedAt: "2024-01-15T10:30:00Z",
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

  it("displays image when imagePath exists", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          imagePath: "/path/to/image.png",
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
      });

    renderProblemDetailPage();

    await waitFor(() => {
      const img = screen.getByAltText("Problem");
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src", "/api/v1/problems/1/image");
    });
  });

  it("shows deleted indicator for soft-deleted problems", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          isDeleted: true,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Original text",
          tags: [],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
        json: async () => ({
          id: 1,
          type: "math",
          text: "Test",
          tags: [],
          isDeleted: false,
          createdAt: "",
          updatedAt: "",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          exposureCount: 0,
          correctCount: 0,
          failedCount: 0,
        }),
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
