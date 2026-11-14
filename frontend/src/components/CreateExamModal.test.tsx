import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { CreateExamModal } from "./CreateExamModal";
import type {
  SelectionCandidate,
  SelectionCandidateSortBy,
} from "@/types/exam";

const mockFetch = vi.fn();
global.fetch = mockFetch;

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
  render(
    <QueryClientProvider client={createQueryClient()}>
      <CreateExamModal isOpen isCreating={false} onClose={vi.fn()} onCreate={onCreate} {...props} />
    </QueryClientProvider>,
  );
  return { onCreate };
}

async function openManualPicker(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: "Manual" }));
  await screen.findByRole("table");
}

describe("CreateExamModal", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("switches between random and manual modes", async () => {
    const user = userEvent.setup();
    renderModal();

    expect(screen.getByLabelText("Problem count")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();

    await user.click(screen.getByRole("radio", { name: "Manual" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter problems")).toBeInTheDocument();
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: "Random" }));

    expect(screen.getByLabelText("Problem count")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
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

    // Default state is visibly indicated, headers inactive.
    expect(screen.getByRole("status")).toHaveTextContent("Default: Score ↓");
    const streakHeader = screen.getByRole("button", { name: /Correct streak, not sorted/ });
    expect(streakHeader).toHaveAttribute("aria-label", "Correct streak, not sorted. Activate to sort descending.");

    // Inactive -> descending (priority 1).
    await user.click(streakHeader);
    expect(await screen.findByRole("button", { name: /Correct streak, descending, priority 1/ })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument(); // default indicator gone
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc");
    });

    // Descending -> ascending, priority kept.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending/ }));
    expect(await screen.findByRole("button", { name: /Correct streak, ascending, priority 1/ })).toBeInTheDocument();
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:asc");
    });

    // Ascending -> inactive: back to the default state.
    await user.click(screen.getByRole("button", { name: /Correct streak, ascending/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
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
    await waitFor(() => {
      const lastCall = mockFetch.mock.calls.at(-1)?.[0] as string;
      expect(decodeURIComponent(lastCall)).toContain("sort=correctStreak:desc,addDate:desc");
    });

    // Direction change keeps the criterion's priority.
    await user.click(screen.getByRole("button", { name: /Added, descending, priority 2/ }));
    expect(screen.getByRole("button", { name: /Added, ascending, priority 2/ })).toBeInTheDocument();

    // Cancelling the first criterion: desc -> asc -> inactive, then the
    // remaining one renumbers to priority 1.
    await user.click(screen.getByRole("button", { name: /Correct streak, descending, priority 1/ }));
    await user.click(screen.getByRole("button", { name: /Correct streak, ascending, priority 1/ }));
    expect(screen.getByRole("button", { name: /Added, ascending, priority 1/ })).toBeInTheDocument();
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
    await user.click(screen.getByRole("button", { name: /Score, not sorted/ }));
    expect(screen.getByRole("button", { name: /Score, descending, priority 1/ })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Reset sorting restores the default indicator and request.
    await user.click(screen.getByRole("button", { name: "Reset sorting" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Default: Score ↓");
    expect(screen.getByRole("button", { name: /Score, not sorted/ })).toBeInTheDocument();
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
    await user.click(screen.getByRole("button", { name: /Score, not sorted/ }));
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
    await user.click(screen.getByRole("button", { name: /Score, not sorted/ }));
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
    await user.click(screen.getByRole("button", { name: /Score, not sorted/ }));
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
