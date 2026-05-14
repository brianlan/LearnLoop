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
          problems: [
            { id: 1, type: "math", text: "What is 2+2?", tags: ["algebra"], isDeleted: false, createdAt: "", updatedAt: "" },
            { id: 2, type: "logic", text: "If A then B", tags: ["deduction"], isDeleted: false, createdAt: "", updatedAt: "" },
          ],
          total: 2,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ["algebra", "deduction", "geometry"],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ["math", "logic", "geometry"],
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    });

    expect(screen.getByText("If A then B")).toBeInTheDocument();
    expect(screen.getAllByText("math").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("logic").length).toBeGreaterThanOrEqual(1);
  });

  it("navigates to problem detail on click", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          problems: [{ id: 1, type: "math", text: "Test problem", tags: [], isDeleted: false, createdAt: "", updatedAt: "" }],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("Test problem")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Test problem"));

    expect(mockNavigate).toHaveBeenCalledWith("/problems/1");
  });

  it("filters by tag", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          problems: [
            { id: 1, type: "math", text: "Test", tags: ["algebra"], isDeleted: false, createdAt: "", updatedAt: "" },
          ],
          total: 1,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ["algebra", "geometry"],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ["math"],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          problems: [
            { id: 1, type: "math", text: "Algebra problem", tags: ["algebra"], isDeleted: false, createdAt: "", updatedAt: "" },
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

  it("shows pagination controls when there are multiple pages", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          problems: Array(20).fill({ id: 1, type: "math", text: "Problem", tags: [], isDeleted: false, createdAt: "", updatedAt: "" }),
          total: 50,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
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
          problems: [],
          total: 0,
          page: 1,
          pageSize: 20,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

    renderProblemsPage();

    await waitFor(() => {
      expect(screen.getByText("No problems found")).toBeInTheDocument();
    });
  });
});
