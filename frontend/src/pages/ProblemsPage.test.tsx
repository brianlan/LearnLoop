import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function renderProblemsPage(initialEntries = ["/problems"]) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={initialEntries}>
        <ProblemsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function okJson(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => data,
  };
}

function problem(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "p1",
    problemType: "single-choice",
    text: "What is 2+2?",
    tags: ["algebra"],
    isDeleted: false,
    tracking: { exposureCount: 0, correctCount: 0, failedCount: 0 },
    createdAt: "",
    updatedAt: "",
    ...overrides,
  };
}

const folderTree = {
  allProblemsCount: 4,
  unfiledCount: 1,
  items: [
    {
      id: "chapter-1",
      name: "Chapter 1",
      parentId: null,
      problemCount: 3,
      createdAt: "",
      updatedAt: "",
      children: [
        {
          id: "section-1",
          name: "Section 1",
          parentId: "chapter-1",
          problemCount: 2,
          createdAt: "",
          updatedAt: "",
          children: [],
        },
      ],
    },
  ],
};

function installApiMock(options: {
  problems?: unknown[];
  total?: number;
  folderResponse?: unknown;
  tags?: string[];
  failMutations?: boolean;
} = {}) {
  const tags = options.tags ?? ["algebra", "geometry"];
  mockFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (method === "GET" && url.startsWith("/api/v1/problems?")) {
      const items = options.problems ?? [problem()];
      return okJson({
        items,
        total: options.total ?? items.length,
        page: 1,
        pageSize: 20,
      });
    }

    if (method === "GET" && url === "/api/v1/folders") {
      return okJson(options.folderResponse ?? folderTree);
    }

    if (method === "GET" && url === "/api/v1/tags") {
      return okJson({
        items: tags.map((name, index) => ({
          id: `tag-${index}`,
          name,
          createdAt: "",
          problemCount: 1,
        })),
      });
    }

    if (options.failMutations && method !== "GET") {
      return okJson({ error: { message: "Folder request failed" } }, 400);
    }

    if (method === "POST" && url === "/api/v1/folders") {
      return okJson({ folder: { id: "new-folder", name: "New Folder", parentId: null, createdAt: "", updatedAt: "" } }, 201);
    }

    if (method === "PATCH" && url.startsWith("/api/v1/folders/")) {
      return okJson({ folder: { id: "chapter-1", name: "Chapter 1", parentId: null, createdAt: "", updatedAt: "" } });
    }

    if (method === "DELETE" && url.startsWith("/api/v1/folders/")) {
      return okJson({ ok: true });
    }

    if (method === "PATCH" && url === "/api/v1/problems/bulk-folder") {
      return okJson({ ok: true });
    }

    throw new Error(`Unhandled request: ${method} ${url}`);
  });
}

function problemRequestUrls() {
  return mockFetch.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.startsWith("/api/v1/problems?"));
}

describe("ProblemsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockNavigate.mockReset();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("defaults to All Problems without a folderId query param", async () => {
    installApiMock();

    renderProblemsPage();

    await screen.findByText("What is 2+2?");

    expect(screen.getByRole("button", { name: "Show All Problems" })).toBeInTheDocument();
    expect(problemRequestUrls()[0]).not.toContain("folderId=");
  });

  it("renders All Problems, Unfiled, and nested folders with count badges", async () => {
    installApiMock();

    renderProblemsPage();

    await screen.findByText("Chapter 1");

    expect(screen.getByRole("button", { name: "Show All Problems" })).toBeInTheDocument();
    expect(screen.getByLabelText("All Problems count")).toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "Show Unfiled" })).toBeInTheDocument();
    expect(screen.getByLabelText("Unfiled count")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Select folder Chapter 1" })).toBeInTheDocument();
    expect(screen.getByLabelText("Chapter 1 count")).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: "Select folder Section 1" })).toBeInTheDocument();
    expect(screen.getByLabelText("Section 1 count")).toHaveTextContent("2");
  });

  it("filters by Unfiled and resets requests to page 1", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    await user.click(screen.getByRole("button", { name: "Show Unfiled" }));

    await waitFor(() => {
      const lastUrl = problemRequestUrls().at(-1) ?? "";
      expect(lastUrl).toContain("folderId=unfiled");
      expect(lastUrl).toContain("page=1");
    });
  });

  it("filters by real folder and composes with tag, type, and search params", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    await user.click(screen.getByRole("button", { name: "Select folder Section 1" }));
    await user.selectOptions(screen.getByLabelText("Filter by Tag:"), "algebra");
    await user.selectOptions(screen.getByLabelText("Filter by Type:"), "short-answer");
    await user.type(screen.getByLabelText("Search problems:"), "proof");

    await waitFor(() => {
      const lastUrl = problemRequestUrls().at(-1) ?? "";
      expect(lastUrl).toContain("folderId=section-1");
      expect(lastUrl).toContain("tag=algebra");
      expect(lastUrl).toContain("type=short-answer");
      expect(lastUrl).toContain("q=proof");
      expect(lastUrl).toContain("page=1");
    });
  });

  it("sends sort parameters with existing filters", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    expect(screen.getByRole("option", { name: "Selection score" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Add date" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Last test date" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Select folder Section 1" }));
    await user.selectOptions(screen.getByLabelText("Filter by Tag:"), "algebra");
    await user.selectOptions(screen.getByLabelText("Sort by:"), "selectionScore");
    await user.selectOptions(screen.getByLabelText("Order:"), "asc");

    await waitFor(() => {
      const lastUrl = problemRequestUrls().at(-1) ?? "";
      expect(lastUrl).toContain("folderId=section-1");
      expect(lastUrl).toContain("tag=algebra");
      expect(lastUrl).toContain("sortBy=selectionScore");
      expect(lastUrl).toContain("sortOrder=asc");
      expect(lastUrl).toContain("page=1");
    });
  });

  it("resets pagination and selection mode when sort changes", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })], total: 25 });

    renderProblemsPage();
    await screen.findByText("Second problem");
    await user.click(screen.getByRole("button", { name: "Next" }));

    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);
    await screen.findByRole("checkbox", { name: "Select problem p1" });

    await user.selectOptions(screen.getByLabelText("Sort by:"), "addDate");

    await waitFor(() => {
      const lastUrl = problemRequestUrls().at(-1) ?? "";
      expect(lastUrl).toContain("sortBy=addDate");
      expect(lastUrl).toContain("sortOrder=desc");
      expect(lastUrl).toContain("page=1");
    });
    expect(screen.queryByLabelText("Bulk actions")).not.toBeInTheDocument();
  });

  it("persists sidebar collapse state in session storage and shows the active label", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    await user.click(screen.getByRole("button", { name: "Select folder Chapter 1" }));
    await user.click(screen.getByRole("button", { name: "Hide" }));

    expect(window.sessionStorage.getItem("problems.folderSidebarCollapsed")).toBe("true");
    expect(screen.getByText("Folder: Chapter 1")).toBeInTheDocument();
  });

  it("expands ancestors of the selected folder on load", async () => {
    installApiMock();

    renderProblemsPage(["/problems?folderId=section-1"]);

    await screen.findByRole("button", { name: "Select folder Section 1" });
    expect(screen.getByRole("button", { name: "Collapse Chapter 1" })).toBeInTheDocument();
    await waitFor(() => {
      expect(problemRequestUrls()[0]).toContain("folderId=section-1");
    });
  });

  it("creates root and child folders through the folder API", async () => {
    const user = userEvent.setup();
    installApiMock();
    vi.spyOn(window, "prompt").mockReturnValue("New Folder");

    renderProblemsPage();
    await screen.findByText("Chapter 1");

    await user.click(screen.getByRole("button", { name: "Create folder" }));
    await user.click(screen.getByRole("button", { name: "Folder actions for Chapter 1" }));
    await user.click(screen.getByRole("menuitem", { name: "New child folder in Chapter 1" }));

    await waitFor(() => {
      const mutationCalls = mockFetch.mock.calls.filter((call) => call[1]?.method === "POST");
      expect(mutationCalls).toHaveLength(2);
      expect(JSON.parse(String(mutationCalls[0][1]?.body))).toEqual({ name: "New Folder", parentId: null });
      expect(JSON.parse(String(mutationCalls[1][1]?.body))).toEqual({ name: "New Folder", parentId: "chapter-1" });
    });
  });

  it("renames, moves, and deletes folders through the folder API", async () => {
    const user = userEvent.setup();
    installApiMock();
    vi.spyOn(window, "prompt").mockReturnValue("Renamed");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderProblemsPage();
    await screen.findByText("Chapter 1");

    await user.click(screen.getByRole("button", { name: "Folder actions for Chapter 1" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename Chapter 1" }));
    await user.click(screen.getByRole("button", { name: "Folder actions for Chapter 1" }));
    await user.click(screen.getByRole("menuitem", { name: "Move Chapter 1" }));
    await user.selectOptions(screen.getByLabelText("Move Chapter 1 to:"), "root");
    await user.click(screen.getByRole("button", { name: "Save move" }));
    await user.click(screen.getByRole("button", { name: "Folder actions for Chapter 1" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete Chapter 1" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/folders/chapter-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Renamed" }) }),
      );
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/folders/chapter-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ parentId: null }) }),
      );
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/folders/chapter-1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("bulk moves selected problems to a folder, clears selection, and refetches data", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })], total: 2 });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });
    await user.selectOptions(screen.getByLabelText("Move to:"), "section-1");
    await user.click(screen.getByRole("button", { name: "Move selected" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/problems/bulk-folder",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ problemIds: ["p1"], folderId: "section-1" }),
        }),
      );
    });
    expect(screen.queryByLabelText("Bulk actions")).not.toBeInTheDocument();
    expect(problemRequestUrls().length).toBeGreaterThan(1);
  });

  it("bulk move to Unfiled sends folderId null", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });
    await user.click(screen.getByRole("button", { name: "Move selected" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/problems/bulk-folder",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ problemIds: ["p1"], folderId: null }),
        }),
      );
    });
  });

  it("uses folder-aware empty state copy", async () => {
    installApiMock({ problems: [], total: 0 });

    renderProblemsPage(["/problems?folderId=unfiled"]);

    await screen.findAllByText("No problems found in Unfiled");
  });

  it("shows concise error feedback for folder and bulk move failures", async () => {
    const user = userEvent.setup();
    installApiMock({ failMutations: true });
    vi.spyOn(window, "prompt").mockReturnValue("New Folder");

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    await user.click(screen.getByRole("button", { name: "Create folder" }));

    await screen.findByText("Folder request failed");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });
    await user.click(screen.getByRole("button", { name: "Move selected" }));

    await screen.findByText("Folder request failed");
  });

  it("navigates to problem detail on card click outside selection mode", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "abc123", text: "Test problem" })] });

    renderProblemsPage();

    await user.click(await screen.findByText("Test problem"));

    expect(mockNavigate).toHaveBeenCalledWith("/problems/abc123");
  });

  // Selection mode tests
  it("hides per-problem checkboxes by default", async () => {
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    expect(screen.queryByRole("checkbox", { name: "Select problem p1" })).not.toBeInTheDocument();
  });

  it("long press enters selection mode and selects the pressed problem", async () => {
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })] });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Long press on first problem card
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    // Should now be in selection mode with checkboxes visible
    const checkbox = await screen.findByRole("checkbox", { name: "Select problem p1" });
    expect(checkbox).toBeInTheDocument();
    expect(checkbox).toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("in selection mode, card click toggles selection without navigating", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })] });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Enter selection mode via long press on first card
    const firstCard = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(firstCard);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(firstCard);

    await screen.findByRole("checkbox", { name: "Select problem p1" });

    // Click on second card should toggle selection, not navigate
    mockNavigate.mockClear();
    const secondCard = screen.getByText("Second problem").closest("div")!;
    await user.click(secondCard);

    expect(mockNavigate).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Select problem p2" })).toBeChecked();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("Select all selects only current-page problems", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })] });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });

    // Click Select all
    await user.click(screen.getByRole("button", { name: "Select all" }));

    expect(screen.getByRole("checkbox", { name: "Select problem p1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select problem p2" })).toBeChecked();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("Deselect all removes only current-page problems from selection", async () => {
    const user = userEvent.setup();
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })] });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Enter selection mode and select all
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });
    await user.click(screen.getByRole("button", { name: "Select all" }));

    // Now deselect all
    await user.click(screen.getByRole("button", { name: "Deselect all" }));

    expect(screen.getByRole("checkbox", { name: "Select problem p1" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select problem p2" })).not.toBeChecked();
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    // Bulk actions bar should still be visible (still in selection mode)
    expect(screen.getByLabelText("Bulk actions")).toBeInTheDocument();
  });

  it("Clear selection clears selected IDs but keeps selection mode active", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });

    // Click Clear selection
    await user.click(screen.getByRole("button", { name: "Clear selection" }));

    // Checkbox should still be visible (still in selection mode)
    expect(screen.getByRole("checkbox", { name: "Select problem p1" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select problem p1" })).not.toBeChecked();
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    expect(screen.getByLabelText("Bulk actions")).toBeInTheDocument();
  });

  it("Quit selection clears selected IDs and hides checkboxes", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });

    // Click Quit selection
    await user.click(screen.getByRole("button", { name: "Quit selection" }));

    // Checkbox should no longer be visible
    expect(screen.queryByRole("checkbox", { name: "Select problem p1" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bulk actions")).not.toBeInTheDocument();
  });

  it("filter changes exit selection mode and clear selection", async () => {
    const user = userEvent.setup();
    installApiMock();

    renderProblemsPage();
    await screen.findByText("What is 2+2?");

    // Enter selection mode via long press
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });

    // Change filter
    await user.selectOptions(screen.getByLabelText("Filter by Tag:"), "algebra");

    // Should exit selection mode
    await waitFor(() => {
      expect(screen.queryByRole("checkbox", { name: "Select problem p1" })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Bulk actions")).not.toBeInTheDocument();
    });
  });

  it("Move selected button is disabled when no problems are selected", async () => {
    installApiMock({ problems: [problem({ id: "p1" }), problem({ id: "p2", text: "Second problem" })] });

    renderProblemsPage();
    await screen.findByText("Second problem");

    // Enter selection mode via long press, then deselect all
    const card = screen.getByText("What is 2+2?").closest("div")!;
    fireEvent.pointerDown(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    fireEvent.pointerUp(card);

    await screen.findByRole("checkbox", { name: "Select problem p1" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select problem p1" })); // deselect

    // Move selected should be disabled
    expect(screen.getByRole("button", { name: "Move selected" })).toBeDisabled();
  });
});
