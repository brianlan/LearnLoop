import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { CreateExamModal } from "./CreateExamModal";
import type {
  SelectionCandidate,
  SelectionCandidateSortBy,
} from "@/types/exam";

const mockFetch = vi.fn();
const mockChoiceFetch = vi.fn();
global.fetch = (input, init) =>
  ["/api/v1/folders", "/api/v1/tags"].includes(String(input))
    ? mockChoiceFetch(input, init)
    : mockFetch(input, init);

const foldersResponse = {
  allProblemsCount: 999,
  unfiledCount: 99,
  items: [
    { id: "parent", name: "Math", parentId: null, children: [
      { id: "child", name: "Basics", parentId: "parent", children: [] },
    ] },
    { id: "other", name: "Science", parentId: null, children: [
      { id: "other-child", name: "Basics", parentId: "other", children: [] },
    ] },
  ],
};
const tagsResponse = { items: [{ name: "Algebra", problemCount: 999 }, { name: "algebra" }, { name: "review" }] };

function choiceResponse(input: unknown) {
  return jsonResponse(String(input).endsWith("/folders") ? foldersResponse : tagsResponse);
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function candidate(id: string, overrides: Partial<SelectionCandidate> = {}): SelectionCandidate {
  return {
    id,
    text: `problem ${id}`,
    selectionScore: 1,
    createdAt: "2024-01-01T00:00:00Z",
    correctStreak: 0,
    successCount: 0,
    failedCount: 0,
    ...overrides,
  };
}

function jsonResponse(data: unknown) {
  return { ok: true, json: async () => data };
}

/**
 * Mirrors the backend candidate contract (#708): ordered `sort` criteria,
 * id-ascending pre-sort, criteria applied lowest-priority-first so the final
 * order is criteria-major with id-ascending ties, null streaks last in both
 * directions, chronological add-date, q filter, global sort then paging.
 */
function mockCandidatesEndpoint(all: SelectionCandidate[], pageSize = 10) {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = new URL(String(input), "http://localhost");
    if (!url.pathname.endsWith("/exams/selection-candidates")) {
      return jsonResponse({ ok: false, error: { message: `unexpected fetch: ${input}` } });
    }
    const params = url.searchParams;
    const sortParam = params.get("sort") ?? "";
    const criteria = sortParam
      ? sortParam.split(",").map((part) => {
          const [field, direction] = part.split(":");
          return {
            field: field as SelectionCandidateSortBy,
            direction: (direction === "asc" ? "asc" : "desc") as "asc" | "desc",
          };
        })
      : [{ field: "selectionScore" as SelectionCandidateSortBy, direction: "desc" as const }];
    const page = Number(params.get("page") ?? "1");
    const q = (params.get("q") ?? "").toLowerCase();
    const value = (row: SelectionCandidate, field: SelectionCandidateSortBy): number | null => {
      if (field === "addDate") return Date.parse(row.createdAt);
      if (field === "failureCount") return row.failedCount;
      if (field === "correctStreak") return row.correctStreak;
      return row.selectionScore;
    };
    let rows = [...all].sort((a, b) => (a.id < b.id ? -1 : 1));
    for (const { field, direction } of [...criteria].reverse()) {
      const known = rows.filter((row) => value(row, field) !== null);
      const unknown = rows.filter((row) => value(row, field) === null);
      known.sort((a, b) => {
        const av = value(a, field) as number;
        const bv = value(b, field) as number;
        if (av === bv) return 0;
        const compared = av < bv ? -1 : 1;
        return direction === "desc" ? -compared : compared;
      });
      rows = [...known, ...unknown];
    }
    if (q) rows = rows.filter((row) => row.text.toLowerCase().includes(q));
    const start = (page - 1) * pageSize;
    return jsonResponse({
      items: rows.slice(start, start + pageSize),
      page,
      pageSize,
      total: rows.length,
    });
  });
}

function renderModal(
  props: { staleProblemIds?: string[]; ineligibleMessage?: string | null } = {},
) {
  const onCreate = vi.fn();
  const queryClient = createQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <CreateExamModal isOpen isCreating={false} onClose={vi.fn()} onCreate={onCreate} {...props} />
    </QueryClientProvider>,
  );
  return { onCreate, queryClient };
}

async function openManualPicker(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: "Manual" }));
  await screen.findByRole("table");
}

describe("CreateExamModal", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockFetch.mockResolvedValue(jsonResponse({ items: [], total: 0 }));
    mockChoiceFetch.mockReset();
    mockChoiceFetch.mockImplementation(async (input: unknown) => choiceResponse(input));
  });

  it("switches between random and manual modes", async () => {
    const user = userEvent.setup();
    renderModal();

    expect(screen.getByLabelText("Problem count")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockChoiceFetch).toHaveBeenCalledWith("/api/v1/folders", { credentials: "include" });
    expect(mockChoiceFetch).toHaveBeenCalledWith("/api/v1/tags", { credentials: "include" });

    await user.click(screen.getByRole("radio", { name: "Manual" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter problems")).toBeInTheDocument();
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: "Random" }));

    expect(screen.getByLabelText("Problem count")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("selects folder subtrees and existing exact tags, without creating free-form tags or showing library counts", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderModal();
    await user.click(await screen.findByRole("checkbox", { name: "Math" }));
    await user.click(screen.getByRole("checkbox", { name: "Math / Basics" }));
    await user.click(screen.getByRole("checkbox", { name: "Unfiled" }));
    expect(screen.getByRole("checkbox", { name: "Science / Basics" })).not.toBeChecked();
    expect(screen.getByText(/Match any selected folder/)).toBeInTheDocument();
    expect(screen.getByText(/Match all selected tags exactly/)).toBeInTheDocument();
    expect(screen.getByText(/Up to 5 problems/)).toBeInTheDocument();
    expect(screen.queryByText(/999|99 available|eligible count/i)).not.toBeInTheDocument();

    const search = screen.getByRole("searchbox", { name: "Search existing tags" });
    await user.type(search, "ALG");
    expect(screen.queryByRole("checkbox", { name: "review" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Algebra" }));
    expect(screen.getByRole("checkbox", { name: "algebra" })).not.toBeChecked();
    await user.clear(search);
    await user.type(search, "rev");
    await user.click(screen.getByRole("checkbox", { name: "review" }));
    await user.clear(search);
    await user.type(search, "new tag{Enter}");
    expect(screen.getByText("No existing tags match.")).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
    expect(mockChoiceFetch.mock.calls.every(([, init]) => !init.method)).toBe(true);

    await user.clear(screen.getByLabelText("Problem count"));
    await user.type(screen.getByLabelText("Problem count"), "12");
    expect(screen.getByText(/Up to 12 problems/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Exam" }));
    expect(onCreate).toHaveBeenCalledWith({
      mode: "random", maxProblemCount: 12,
      folderIds: ["parent", "child", "unfiled"], tags: ["Algebra", "review"],
    });
  });

  it("removes individual constraints and clears both groups without changing the requested maximum", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderModal();
    await user.click(await screen.findByRole("checkbox", { name: "Math" }));
    await user.click(screen.getByRole("checkbox", { name: "Unfiled" }));
    await user.click(screen.getByRole("checkbox", { name: "Algebra" }));
    await user.click(screen.getByRole("checkbox", { name: "review" }));
    await user.clear(screen.getByLabelText("Problem count"));
    await user.type(screen.getByLabelText("Problem count"), "9");

    await user.click(screen.getByRole("button", { name: "Remove folder Math" }));
    await user.click(screen.getByRole("button", { name: "Remove tag Algebra" }));
    expect(screen.getByRole("checkbox", { name: "Math" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Algebra" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Unfiled" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "review" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByLabelText("Problem count")).toHaveValue(9);
    expect(screen.getByLabelText("Problem count slider")).toHaveValue("9");
    await user.click(screen.getByRole("button", { name: "Create Exam" }));
    expect(onCreate).toHaveBeenCalledWith({ mode: "random", maxProblemCount: 9 });
  });

  it("preserves Random filters across mode switches, isolates Manual requests, and resets on reopening", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    const queryClient = createQueryClient();
    const onCreate = vi.fn();
    const ui = (isOpen: boolean) => (
      <QueryClientProvider client={queryClient}>
        <CreateExamModal isOpen={isOpen} isCreating={false} onClose={vi.fn()} onCreate={onCreate} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(true));
    await user.click(await screen.findByRole("checkbox", { name: "Math" }));
    await user.click(screen.getByRole("checkbox", { name: "review" }));
    await user.clear(screen.getByLabelText("Problem count"));
    await user.type(screen.getByLabelText("Problem count"), "8");
    await openManualPicker(user);
    await user.click(await screen.findByRole("checkbox", { name: "Select problem id-1" }));
    expect(mockFetch.mock.calls.every(([url]) => !String(url).includes("folder") && !String(url).includes("tags="))).toBe(true);
    await user.click(screen.getByRole("radio", { name: "Random" }));
    expect(screen.getByRole("checkbox", { name: "Math" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "review" })).toBeChecked();
    expect(screen.getByLabelText("Problem count")).toHaveValue(8);
    await user.click(screen.getByRole("button", { name: "Create Exam" }));
    expect(onCreate).toHaveBeenLastCalledWith({ mode: "random", maxProblemCount: 8, folderIds: ["parent"], tags: ["review"] });
    await openManualPicker(user);
    expect(screen.getByRole("checkbox", { name: "Select problem id-1" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Create Exam (1 selected)" }));
    expect(onCreate).toHaveBeenLastCalledWith({ mode: "manual", problemIds: ["id-1"] });

    rerender(ui(false));
    rerender(ui(true));
    expect(screen.getByRole("radio", { name: "Random" })).toBeChecked();
    expect(screen.getByLabelText("Problem count")).toHaveValue(5);
    expect(screen.getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByRole("searchbox", { name: "Search existing tags" })).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Create Exam" }));
    expect(onCreate).toHaveBeenLastCalledWith({ mode: "random", maxProblemCount: 5 });
  });

  it("supports keyboard folder selection and searching then selecting existing tags", async () => {
    const user = userEvent.setup();
    const { onCreate } = renderModal();
    const folder = await screen.findByRole("checkbox", { name: "Math / Basics" });
    folder.focus();
    await user.keyboard(" ");
    expect(folder).toBeChecked();
    const search = screen.getByRole("searchbox", { name: "Search existing tags" });
    search.focus();
    await user.keyboard("rev");
    await user.tab();
    const tag = screen.getByRole("checkbox", { name: "review" });
    expect(tag).toHaveFocus();
    await user.keyboard(" ");
    expect(tag).toBeChecked();
    screen.getByRole("button", { name: "Create Exam" }).focus();
    await user.keyboard("{Enter}");
    expect(onCreate).toHaveBeenCalledWith({ mode: "random", maxProblemCount: 5, folderIds: ["child"], tags: ["review"] });
  });

  it("shows choice loading states without fetching Manual candidates", async () => {
    mockChoiceFetch.mockImplementation(() => new Promise(() => {}));
    renderModal();
    expect(screen.getByText("Loading folders...")).toHaveAttribute("role", "status");
    expect(screen.getByText("Loading tags...")).toHaveAttribute("role", "status");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("keeps selected constraints during choice-load errors and supports retry without broadening requests", async () => {
    const user = userEvent.setup();
    const { onCreate, queryClient } = renderModal();
    await user.click(await screen.findByRole("checkbox", { name: "Math" }));
    await user.click(screen.getByRole("checkbox", { name: "review" }));
    mockChoiceFetch.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["folders"] });
      await queryClient.invalidateQueries({ queryKey: ["tagSuggestions"] });
    });
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Remove folder Math" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove tag review" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Exam" }));
    expect(onCreate).toHaveBeenCalledWith({ mode: "random", maxProblemCount: 5, folderIds: ["parent"], tags: ["review"] });

    mockChoiceFetch.mockImplementation(async (input: unknown) => choiceResponse(input));
    await user.click(screen.getByRole("button", { name: "Retry folders" }));
    await user.click(screen.getByRole("button", { name: "Retry tags" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: "Math" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "review" })).toBeChecked();
  });

  it("builds the expected query from the filter input", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1", { text: "algebra one" })]);
    renderModal();
    await openManualPicker(user);

    await user.type(screen.getByLabelText("Filter problems"), "algebra");

    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(lastCall).toContain("q=algebra");
      // Default state sends the explicit default criterion (#708).
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:desc");
      expect(lastCall).toContain("page=1");
      expect(lastCall).toContain("pageSize=10");
    });
    expect(await screen.findByText("algebra one")).toBeInTheDocument();
  });

  it("shows the default indicator and cycles a header inactive -> desc -> asc -> inactive", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([
      candidate("id-1", { selectionScore: 3 }),
      candidate("id-2", { selectionScore: 1 }),
    ]);
    renderModal();
    await openManualPicker(user);

    const sortedHeader = (label: RegExp) =>
      screen.getByRole("button", { name: label }).closest("th") as HTMLElement;

    // Default state is visibly indicated, headers inactive; exactly ONE
    // sorted header (the default Score, #708 B4).
    expect(screen.getByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(sortedHeader(/Score, default order descending/)).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    const streakHeader = screen.getByRole("button", { name: /Correct streak, not sorted/ });
    expect(streakHeader).toHaveAttribute("aria-label", "Correct streak, not sorted. Activate to sort descending.");
    expect(sortedHeader(/Correct streak, not sorted/)).toHaveAttribute("aria-sort", "none");

    // Inactive -> descending (priority 1); the sorted header moves with it.
    await user.click(streakHeader);
    expect(await screen.findByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();
    expect(sortedHeader(/Correct streak, descending, priority 1/)).toHaveAttribute("aria-sort", "descending");
    expect(sortedHeader(/^Score,/)).toHaveAttribute("aria-sort", "none");
    expect(screen.queryByRole("status")).not.toBeInTheDocument(); // default indicator gone
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc");
    });

    // Descending -> ascending, priority kept.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending/ }));
    expect(await screen.findByRole("button", { name: /Correct streak, ascending, priority 1/ })).toBeInTheDocument();
    expect(sortedHeader(/Correct streak, ascending, priority 1/)).toHaveAttribute("aria-sort", "ascending");
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:asc");
    });

    // Ascending -> inactive: back to the default state and default header.
    await user.click(screen.getByRole("button", { name: /Correct streak, ascending/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(sortedHeader(/^Score,/)).toHaveAttribute("aria-sort", "descending");
    expect(sortedHeader(/Correct streak, not sorted/)).toHaveAttribute("aria-sort", "none");
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:desc");
    });
  });

  it("appends later criteria with lower priority, keeps priority on direction change, and renumbers after cancellation", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    await user.click(screen.getByRole("button", { name: /Added, not sorted/ }));

    // Joining order establishes priority: streak 1, added 2.
    expect(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Added, descending, priority 2/ })).toBeInTheDocument();
    // Exactly one sorted header: the primary criterion only (#708 B4).
    expect(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ }).closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("button", { name: /Added, descending, priority 2/ }).closest("th")).toHaveAttribute("aria-sort", "none");
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc,addDate:desc");
    });

    // Direction change keeps the criterion's priority.
    await user.click(screen.getByRole("button", { name: /Added, descending, priority 2/ }));
    expect(screen.getByRole("button", { name: /Added, ascending, priority 2/ })).toBeInTheDocument();

    // Cancelling the first criterion: desc -> asc -> inactive; the remaining
    // one renumbers to priority 1 and becomes the single sorted header.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ }));
    await user.click(screen.getByRole("button", { name: /Correct streak, ascending, priority 1/ }));
    expect(screen.getByRole("button", { name: /Added, ascending, priority 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Added, ascending, priority 1/ }).closest("th")).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("button", { name: /Correct streak, not sorted/ }).closest("th")).toHaveAttribute("aria-sort", "none");
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=addDate:asc");
    });
  });

  it("activates Score explicitly from the default state and resets to default", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    // First click on Score from no-custom-criteria: explicit Score descending.
    await user.click(screen.getByRole("button", { name: /Score, default order descending/ }));
    expect(screen.getByRole("button", { name: /Score, descending, priority 1/ })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Reset sorting restores the default indicator, request, and header.
    await user.click(screen.getByRole("button", { name: "Reset sorting" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(screen.getByRole("button", { name: /Score, default order descending/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Score, default order descending/ }).closest("th")).toHaveAttribute("aria-sort", "descending");
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:desc");
    });

    // Cancelling the last active criterion has the same effect as reset.
    await user.click(screen.getByRole("button", { name: /Failure, not sorted/ }));
    await user.click(screen.getByRole("button", { name: /Failure, descending, priority 1/ }));
    await user.click(screen.getByRole("button", { name: /Failure, ascending, priority 1/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
  });

  it("re-appends a cancelled criterion at the lowest priority", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    await user.click(screen.getByRole("button", { name: /Added, not sorted/ }));
    expect(screen.getByRole("button", { name: /Added, descending, priority 2/ })).toBeInTheDocument();

    // Cancel the streak criterion (desc -> asc -> inactive)... Added keeps
    // its direction and renumbers to priority 1.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ }));
    await user.click(screen.getByRole("button", { name: /Correct streak, ascending, priority 1/ }));
    expect(screen.getByRole("button", { name: /Added, descending, priority 1/ })).toBeInTheDocument();

    // ...and re-add it: it joins at the END (lowest priority), keeping Added first.
    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    expect(screen.getByRole("button", { name: /Added, descending, priority 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Correct streak, descending, priority 2/ })).toBeInTheDocument();
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=addDate:desc,correctStreak:desc");
    });
  });

  it("resets sorting from the keyboard", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    expect(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();

    const reset = screen.getByRole("button", { name: "Reset sorting" });
    reset.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(screen.getByRole("button", { name: /Correct streak, not sorted/ })).toBeInTheDocument();
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:desc");
    });
  });

  it("retains cross-page selections through Reset sorting and confirms in default order", async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 12 }, (_, index) =>
      candidate(`id-${String(index + 1).padStart(2, "0")}`, {
        selectionScore: index === 11 ? 9 : index === 1 ? 6 : 5,
      }),
    );
    mockCandidatesEndpoint(all);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    // Custom criterion first (Failure ties everywhere -> id-asc order).
    await user.click(screen.getByRole("button", { name: /Failure, not sorted/ }));
    await user.click(screen.getByRole("checkbox", { name: "Select problem id-02" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("checkbox", { name: "Select problem id-12" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    // Reset returns to page 1 under default Score descending and keeps both.
    await user.click(screen.getByRole("button", { name: "Reset sorting" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select problem id-12" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select problem id-02" })).toBeChecked();

    // Confirmation applies the DEFAULT criteria (Score descending: id-12=9
    // before id-02=6), not the cancelled Failure criterion.
    await user.click(screen.getByRole("button", { name: "Create Exam (2 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-12", "id-02"],
      });
    });
  });

  it("operates header sorting from the keyboard", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    const streakHeader = screen.getByRole("button", { name: /Correct streak, not sorted/ });
    streakHeader.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc");
    });
  });

  it("displays — for missing streak history and explains the limitation", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([
      candidate("id-missing", { correctStreak: null }),
      candidate("id-zero", { correctStreak: 0, failedCount: 3 }),
    ]);
    renderModal();
    await openManualPicker(user);

    const missingRow = screen.getByText("problem id-missing").closest("tr") as HTMLElement;
    expect(within(missingRow).getByText("—")).toBeInTheDocument();
    const zeroRow = screen.getByText("problem id-zero").closest("tr") as HTMLElement;
    expect(within(zeroRow).getByText("0")).toBeInTheDocument();
    expect(
      screen.getByText(/Correct streak counts consecutive correct results/),
    ).toBeInTheDocument();
  });

  it("keeps selections across pagination and emits ids in displayed order at confirm", async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 12 }, (_, index) =>
      candidate(`id-${String(index + 1).padStart(2, "0")}`, { selectionScore: index + 1 }),
    );
    mockCandidatesEndpoint(all);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    // Page 1 (desc): id-12 … id-03; page 2: id-02, id-01.
    await user.click(screen.getByRole("checkbox", { name: "Select problem id-12" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("checkbox", { name: "Select problem id-02" }));
    await user.click(screen.getByRole("button", { name: "Previous" }));

    expect(screen.getByRole("checkbox", { name: "Select problem id-12" })).toBeChecked();
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create Exam (2 selected)" }));

    await waitFor(() => {
      // Default sort: selectionScore desc → 12 before 2.
      expect(onCreate).toHaveBeenCalledWith({
        mode: "manual",
        problemIds: ["id-12", "id-02"],
      });
    });
  });

  it("emits problemIds re-sorted after a sort change", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([
      candidate("id-a", { selectionScore: 1 }),
      candidate("id-b", { selectionScore: 3 }),
      candidate("id-c", { selectionScore: 2 }),
    ]);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    for (const name of ["Select problem id-a", "Select problem id-b", "Select problem id-c"]) {
      await user.click(screen.getByRole("checkbox", { name }));
    }
    // Two clicks: explicit descending, then ascending (#708 cycle).
    await user.click(screen.getByRole("button", { name: /Score, default order descending/ }));
    await user.click(screen.getByRole("button", { name: /Score, descending, priority 1/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:asc");
    });
    await user.click(screen.getByRole("button", { name: "Create Exam (3 selected)" }));

    await waitFor(() => {
      // Ascending selectionScore: a (1), c (2), b (3).
      expect(onCreate).toHaveBeenCalledWith({
        mode: "manual",
        problemIds: ["id-a", "id-c", "id-b"],
      });
    });
  });

  it("keeps ascending id order among tied scores in both directions", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([
      candidate("id-001", { selectionScore: 5 }),
      candidate("id-002", { selectionScore: 5 }),
      candidate("id-003", { selectionScore: 5 }),
    ]);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    for (const name of ["Select problem id-001", "Select problem id-002", "Select problem id-003"]) {
      await user.click(screen.getByRole("checkbox", { name }));
    }

    // Descending with tied scores: ids stay ascending, not reversed.
    const descOrder = screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-label"));
    expect(descOrder).toEqual([
      "Select problem id-001",
      "Select problem id-002",
      "Select problem id-003",
    ]);
    await user.click(screen.getByRole("button", { name: "Create Exam (3 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-001", "id-002", "id-003"],
      });
    });

    // Ascending ties look the same and submit the same order (two clicks:
    // explicit descending, then ascending).
    await user.click(screen.getByRole("button", { name: /Score, default order descending/ }));
    await user.click(screen.getByRole("button", { name: /Score, descending, priority 1/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=selectionScore:asc");
    });
    const ascOrder = screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-label"));
    expect(ascOrder).toEqual(descOrder);
    await user.click(screen.getByRole("button", { name: "Create Exam (3 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-001", "id-002", "id-003"],
      });
    });
  });

  it("sorts add-date chronologically across whole and fractional second timestamps", async () => {
    const user = userEvent.setup();
    // The API emits both timestamp forms; ".001000Z" sorts BEFORE "Z"
    // lexicographically although it is later in time.
    mockCandidatesEndpoint([
      candidate("id-early", { createdAt: "2024-01-01T00:00:00Z" }),
      candidate("id-late", { createdAt: "2024-01-01T00:00:00.001000Z" }),
    ]);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    for (const name of ["Select problem id-early", "Select problem id-late"]) {
      await user.click(screen.getByRole("checkbox", { name }));
    }

    // Switch to the Added column (defaults to descending).
    await user.click(screen.getByRole("button", { name: /Added, not sorted/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=addDate:desc");
    });

    // Descending add-date: the later problem displays and submits first.
    const descOrder = screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-label"));
    expect(descOrder).toEqual(["Select problem id-late", "Select problem id-early"]);
    await user.click(screen.getByRole("button", { name: "Create Exam (2 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-late", "id-early"],
      });
    });

    // Ascending flips both display and submission.
    await user.click(screen.getByRole("button", { name: /Added, descending, priority 1/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=addDate:asc");
    });
    const ascOrder = screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-label"));
    expect(ascOrder).toEqual(["Select problem id-early", "Select problem id-late"]);
    await user.click(screen.getByRole("button", { name: "Create Exam (2 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-early", "id-late"],
      });
    });
  });

  it("applies multi-key criteria with null streaks to confirmation order", async () => {
    const user = userEvent.setup();
    // Streak ascending primary, Score descending secondary; the null-streak
    // row sorts last at the streak criterion regardless of direction, and
    // mixed ISO precision createdAt values compare chronologically.
    mockCandidatesEndpoint([
      candidate("id-null", { correctStreak: null, selectionScore: 9, createdAt: "2024-01-01T00:00:00Z" }),
      candidate("id-b", { correctStreak: 1, selectionScore: 1, createdAt: "2024-01-01T00:00:00.001000Z" }),
      candidate("id-a", { correctStreak: 1, selectionScore: 5, createdAt: "2024-01-01T00:00:00Z" }),
      candidate("id-c", { correctStreak: 2, selectionScore: 1, createdAt: "2024-01-01T00:00:00Z" }),
    ]);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    for (const name of ["Select problem id-null", "Select problem id-b", "Select problem id-a", "Select problem id-c"]) {
      await user.click(screen.getByRole("checkbox", { name }));
    }

    // Streak ascending, then Score descending.
    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    await user.click(screen.getByRole("button", { name: /^Score,/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc,selectionScore:desc");
    });

    const rows = screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-label"));
    expect(rows).toEqual([
      "Select problem id-c",
      "Select problem id-a",
      "Select problem id-b",
      "Select problem id-null",
    ]);

    // Flip the streak criterion to ascending (priority 1 kept): smaller
    // streaks first — a,b (streak 1, score desc) then c (streak 2) — null
    // still last. Confirmation must match the display.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ }));
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:asc,selectionScore:desc");
    });
    await user.click(screen.getByRole("button", { name: "Create Exam (4 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-a", "id-b", "id-c", "id-null"],
      });
    });
  });

  it("returns to page 1 on sorting changes while retaining cross-page selections", async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 12 }, (_, index) =>
      candidate(`id-${String(index + 1).padStart(2, "0")}`, { selectionScore: index + 1 }),
    );
    mockCandidatesEndpoint(all);
    const { onCreate } = renderModal();
    await openManualPicker(user);

    await user.click(screen.getByRole("checkbox", { name: "Select problem id-12" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("checkbox", { name: "Select problem id-02" }));

    // Sorting change resets to page 1 but keeps the selection: under the
    // Failure criterion every row ties, so page 1 now lists ids 01..10 and
    // the previously selected id-02 is still checked.
    await user.click(screen.getByRole("button", { name: /Failure, not sorted/ }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select problem id-02" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Create Exam (2 selected)" }));
    await waitFor(() => {
      // Failure ties everywhere -> id ascending.
      expect(onCreate).toHaveBeenLastCalledWith({
        mode: "manual",
        problemIds: ["id-02", "id-12"],
      });
    });
  });

  it("resets custom sorting when the modal reopens", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    const client = createQueryClient();
    const ui = (open: boolean) => (
      <QueryClientProvider client={client}>
        <CreateExamModal isOpen={open} isCreating={false} onClose={vi.fn()} onCreate={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(true));
    await openManualPicker(user);

    await user.click(screen.getByRole("button", { name: /Correct streak, not sorted/ }));
    expect(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();

    rerender(ui(false));
    rerender(ui(true));

    // Reopen lands in random mode as today; re-enter manual and confirm the
    // sorting state was reset with the rest.
    await user.click(screen.getByRole("radio", { name: "Manual" }));
    await screen.findByRole("table");

    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(screen.getByRole("button", { name: /Correct streak, not sorted/ })).toBeInTheDocument();
  });

  it("disables unchecked rows once 30 problems are selected", async () => {
    const user = userEvent.setup();
    const all = Array.from({ length: 40 }, (_, index) =>
      candidate(`id-${String(index + 1).padStart(2, "0")}`, { selectionScore: index + 1 }),
    );
    mockCandidatesEndpoint(all);
    renderModal();
    await openManualPicker(user);

    // Default desc order fills pages 1-3 entirely (30 rows) with ids 40..11.
    for (let page = 0; page < 3; page += 1) {
      if (page > 0) {
        await user.click(screen.getByRole("button", { name: "Next" }));
        await screen.findByText(`problem id-${40 - page * 10}`);
      }
      const checkboxes = screen.getAllByRole("checkbox");
      expect(checkboxes).toHaveLength(10);
      for (const checkbox of checkboxes) {
        await user.click(checkbox);
      }
    }
    expect(screen.getByText("30 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    const pageFour = await screen.findAllByRole("checkbox");
    for (const checkbox of pageFour) {
      expect(checkbox).toBeDisabled();
    }
  });

  it("marks stale rows, shows the ineligible message, and drops them from the selection", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([
      candidate("id-stale"),
      candidate("id-fresh"),
    ]);
    const onCreate = vi.fn();
    const client = createQueryClient();
    const ui = (stale: { staleProblemIds?: string[]; ineligibleMessage?: string | null }) => (
      <QueryClientProvider client={client}>
        <CreateExamModal isOpen isCreating={false} onClose={vi.fn()} onCreate={onCreate} {...stale} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui({}));
    await openManualPicker(user);

    await user.click(screen.getByRole("checkbox", { name: "Select problem id-stale" }));
    await user.click(screen.getByRole("checkbox", { name: "Select problem id-fresh" }));
    expect(screen.getByRole("button", { name: "Create Exam (2 selected)" })).toBeEnabled();

    // 422 INELIGIBLE_PROBLEMS arrives: the page names id-stale and rerenders.
    rerender(
      ui({
        staleProblemIds: ["id-stale"],
        ineligibleMessage: "Some selected problems are not exam-eligible",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Some selected problems are not exam-eligible",
    );
    const staleRow = screen.getByText("problem id-stale").closest("tr") as HTMLElement;
    expect(within(staleRow).getByText("no longer eligible")).toBeInTheDocument();
    const freshRow = screen.getByText("problem id-fresh").closest("tr") as HTMLElement;
    expect(within(freshRow).queryByText("no longer eligible")).not.toBeInTheDocument();

    // The rejected id is stripped so a retry cannot resubmit it invisibly.
    expect(screen.getByRole("button", { name: "Create Exam (1 selected)" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Create Exam (1 selected)" }));
    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith({
        mode: "manual",
        problemIds: ["id-fresh"],
      });
    });
  });

  it("disables Create until at least one problem is selected", async () => {
    const user = userEvent.setup();
    mockCandidatesEndpoint([candidate("id-1")]);
    renderModal();
    await openManualPicker(user);

    expect(screen.getByRole("button", { name: "Create Exam (0 selected)" })).toBeDisabled();

    await user.click(screen.getByRole("checkbox", { name: "Select problem id-1" }));

    expect(screen.getByRole("button", { name: "Create Exam (1 selected)" })).toBeEnabled();
  });
});
