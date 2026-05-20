import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ProblemsPage } from "./ProblemsPage";

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

function renderProblemsPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ProblemsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProblemsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockNavigate.mockReset();
  });

  it("renders problems list", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            { id: "1", problemType: "single-choice", text: "What is 2+2?", tags: ["algebra"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
            { id: "2", problemType: "multi-choice", text: "If A then B", tags: ["deduction"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
          ],
          total: 2,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [
          { id: "t1", name: "algebra", createdAt: "", problemCount: 1 },
          { id: "t2", name: "deduction", createdAt: "", problemCount: 1 },
          { id: "t3", name: "geometry", createdAt: "", problemCount: 0 },
        ] }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    });

    expect(screen.getByText("If A then B")).toBeInTheDocument();
    expect(screen.getAllByText("single-choice").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("multi-choice").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Showing 2 of 2 problems")).toBeInTheDocument();
  });

  it("navigates to problem detail on click", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ id: "abc123", problemType: "single-choice", text: "Test problem", tags: [], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" }],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("Test problem")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Test problem"));

    expect(mockNavigate).toHaveBeenCalledWith("/problems/abc123");
  });

  it("filters by tag", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            { id: "1", problemType: "single-choice", text: "Test", tags: ["algebra"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
          ],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [
          { id: "t1", name: "algebra", createdAt: "", problemCount: 1 },
          { id: "t2", name: "geometry", createdAt: "", problemCount: 0 },
        ] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            { id: "1", problemType: "single-choice", text: "Algebra problem", tags: ["algebra"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
          ],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByLabelText("Filter by Tag:")).toBeInTheDocument();
    });

    await waitFor(() => {
      const select = screen.getByLabelText("Filter by Tag:") as HTMLSelectElement;
      expect(select.options.length).toBeGreaterThan(1);
    });

    await user.selectOptions(screen.getByLabelText("Filter by Tag:"), "algebra");

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("tag=algebra"),
        expect.any(Object),
      );
    });
  });

  it("filters by problem type", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            { id: "1", problemType: "single-choice", text: "Test", tags: ["algebra"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
          ],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [
          { id: "t1", name: "algebra", createdAt: "", problemCount: 1 },
          { id: "t2", name: "geometry", createdAt: "", problemCount: 0 },
        ] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            { id: "2", problemType: "short-answer", text: "Explain", tags: ["geometry"], isDeleted: false, tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 }, createdAt: "", updatedAt: "" },
          ],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByLabelText("Filter by Type:")).toBeInTheDocument();
    });

    await user.selectOptions(screen.getByLabelText("Filter by Type:"), "short-answer");

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("type=short-answer"),
        expect.any(Object),
      );
    });
  });

  it("shows pagination controls when there are multiple pages", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: Array.from({ length: 20 }, (_, index) => ({
            id: `problem-${index + 1}`,
            problemType: "single-choice",
            text: `Problem ${index + 1}`,
            tags: [],
            isDeleted: false,
            tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 },
            createdAt: "",
            updatedAt: "",
          })),
          total: 50,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("shows no problems message when list is empty", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [],
          total: 0,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("No problems found")).toBeInTheDocument();
    });
  });
});
