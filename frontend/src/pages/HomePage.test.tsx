import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { HomePage } from "./HomePage";
import { ThemeProvider } from "@/contexts/ThemeContext";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderHomePage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ThemeProvider>
          <HomePage />
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function summaryResponse(overrides: Partial<{
  totalProblems: number;
  triedProblems: number;
  percentage: number;
  masteredProblems: number;
  conquestPercentage: number;
  firstPassAttempted: number;
  firstPassCorrect: number;
  firstPassPercentage: number;
  days: { date: string; count: number }[];
  scoreDistributionBuckets: { start: number; neverTested: number; minAged: number; tested: number; cooldown: number }[];
}> = {}) {
  const today = new Date();
  const days: { date: string; count: number }[] = [];
  for (let i = 364; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    days.push({ date: d.toISOString().slice(0, 10), count: 0 });
  }
  const totalProblems = overrides.totalProblems ?? 2;
  return {
    coverage: {
      totalProblems,
      triedProblems: overrides.triedProblems ?? 1,
      percentage: overrides.percentage ?? 50,
    },
    conquest: {
      totalProblems,
      masteredProblems: overrides.masteredProblems ?? 0,
      percentage: overrides.conquestPercentage ?? 0,
    },
    firstPass: {
      attemptedProblems: overrides.firstPassAttempted ?? 1,
      firstPassCorrectProblems: overrides.firstPassCorrect ?? 0,
      percentage: overrides.firstPassPercentage ?? 0,
    },
    activity: {
      startDate: days[0].date,
      endDate: days[days.length - 1].date,
      days: overrides.days ?? days,
    },
    scoreDistribution: {
      buckets: overrides.scoreDistributionBuckets ?? [],
    },
  };
}

describe("HomePage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders loading state initially", () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));
    renderHomePage();
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  it("renders error state when request fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: { message: "boom" } }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("error").textContent).toContain("boom");
  });

  it("sends the detected timezone as a query parameter", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse(),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/home/summary");
    expect(calledUrl).toContain("timezone=");
  });

  it("renders zero-problem state with 0% and note", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ totalProblems: 0, triedProblems: 0, percentage: 0, masteredProblems: 0, conquestPercentage: 0, firstPassAttempted: 0, firstPassCorrect: 0, firstPassPercentage: 0 }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-coverage-percentage").textContent).toBe("0%");
    });
    expect(screen.getByTestId("home-coverage-text").textContent).toContain("No problems yet");
    expect(screen.getByTestId("home-conquest-percentage").textContent).toBe("0%");
    expect(screen.getByTestId("home-conquest-text").textContent).toContain("No problems yet");
    expect(screen.getByTestId("home-firstpass-percentage").textContent).toBe("0%");
    expect(screen.getByTestId("home-firstpass-text").textContent).toContain("No attempts yet");
  });

  it("renders coverage percentage and supporting text in normal state", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ totalProblems: 4, triedProblems: 2, percentage: 50 }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-coverage-percentage").textContent).toBe("50%");
    });
    expect(screen.getByTestId("home-coverage-text").textContent).toContain("2 of 4 problems tried");
  });

  it("renders conquest percentage and supporting text in normal state", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({
        totalProblems: 4,
        triedProblems: 3,
        percentage: 75,
        masteredProblems: 2,
        conquestPercentage: 50,
      }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-conquest-percentage").textContent).toBe("50%");
    });
    expect(screen.getByTestId("home-conquest-text").textContent).toContain("2 of 4 problems mastered");
  });

  it("renders first-pass rate as the third stat card with percentage and supporting text", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({
        totalProblems: 4,
        triedProblems: 3,
        percentage: 75,
        masteredProblems: 2,
        conquestPercentage: 50,
        firstPassAttempted: 4,
        firstPassCorrect: 2,
        firstPassPercentage: 50,
      }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-firstpass-percentage").textContent).toBe("50%");
    });
    expect(screen.getByTestId("home-firstpass-text").textContent).toContain("2 of 4 attempts correct on first try");
  });

  it("renders no-attempt message when firstPass has zero attempts", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({
        totalProblems: 2,
        triedProblems: 0,
        percentage: 0,
        firstPassAttempted: 0,
        firstPassCorrect: 0,
        firstPassPercentage: 0,
      }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-firstpass-percentage").textContent).toBe("0%");
    });
    expect(screen.getByTestId("home-firstpass-text").textContent).toContain("No attempts yet");
  });

  it("renders activity grid cells with returned counts", async () => {
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days: { date: string; count: number }[] = [];
    for (let i = 364; i >= 0; i--) {
      const d = new Date(today);
      d.setUTCDate(d.getUTCDate() - i);
      const dateStr = d.toISOString().slice(0, 10);
      days.push({ date: dateStr, count: dateStr === todayStr ? 5 : 0 });
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const cells = screen.getAllByTestId("home-activity-cell");
    const activeCell = cells.find((c) => c.getAttribute("data-count") === "5");
    expect(activeCell).toBeDefined();
    expect(activeCell?.getAttribute("data-date")).toBe(todayStr);
  });

  it("uses higher intensity for higher activity counts (theme-aware)", async () => {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    const todayStr = today.toISOString().slice(0, 10);
    const yesterdayStr = yesterday.toISOString().slice(0, 10);
    const days: { date: string; count: number }[] = [];
    for (let i = 364; i >= 0; i--) {
      const d = new Date(today);
      d.setUTCDate(d.getUTCDate() - i);
      const dateStr = d.toISOString().slice(0, 10);
      let count = 0;
      if (dateStr === todayStr) count = 5;
      if (dateStr === yesterdayStr) count = 1;
      days.push({ date: dateStr, count });
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const cells = screen.getAllByTestId("home-activity-cell");
    const highCell = cells.find((c) => c.getAttribute("data-date") === todayStr) as HTMLElement;
    const lowCell = cells.find((c) => c.getAttribute("data-date") === yesterdayStr) as HTMLElement;
    const zeroCell = cells.find((c) => c.getAttribute("data-count") === "0") as HTMLElement;

    const highBg = highCell.style.backgroundColor;
    const lowBg = lowCell.style.backgroundColor;
    const zeroBg = zeroCell.style.backgroundColor;

    const highPct = parseInt(highBg.match(/(\d+)%/)?.[1] ?? "0", 10);
    const lowPct = parseInt(lowBg.match(/(\d+)%/)?.[1] ?? "0", 10);
    expect(highPct).toBeGreaterThan(lowPct);
    expect(zeroBg).toBe("var(--color-surface-muted)");
  });

  it("renders a bounded one-year week-column grid for a 365-day response", async () => {
    const days = buildDayRange("2025-06-22", 365);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const columns = screen.getAllByTestId("home-activity-week-column");
    expect(columns.length).toBeGreaterThanOrEqual(52);
    expect(columns.length).toBeLessThanOrEqual(54);

    // 365 cells should be present (no extra empty columns inflating the chart).
    const cells = screen.getAllByTestId("home-activity-cell");
    const realCells = cells.filter((c) => (c.getAttribute("data-date") ?? "") !== "");
    expect(realCells.length).toBe(365);
  });

  it("renders uniform fixed-size slots for real and padding cells", async () => {
    const days = buildDayRange("2025-06-22", 365);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    const columns = screen.getAllByTestId("home-activity-week-column");
    const slots = columns.flatMap((column) => Array.from(column.children));
    expect(slots.length).toBe(columns.length * 7);

    const firstSlot = slots[0] as HTMLElement;
    for (const slot of slots) {
      const el = slot as HTMLElement;
      expect(el.style.position).toBe("relative");
      expect(el.style.width).toBe(firstSlot.style.width);
      expect(el.style.height).toBe(firstSlot.style.height);
    }

    const cells = screen.getAllByTestId("home-activity-cell");
    const realCell = cells.find((c) => c.getAttribute("data-date") !== "");
    const paddingCell = cells.find((c) => c.getAttribute("data-date") === "");
    expect(realCell?.tagName).toBe("BUTTON");
    expect(paddingCell?.tagName).toBe("DIV");
    expect(paddingCell).toHaveAttribute("aria-hidden", "true");
  });

  it("renders month labels for months in the visible range", async () => {
    const days = buildDayRange("2025-06-22", 365);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const labels = screen.getAllByTestId("home-activity-month-label");
    const texts = labels.map((l) => l.textContent?.trim()).filter(Boolean);
    expect(texts).toEqual(expect.arrayContaining(["Jul", "Oct", "Jan"]));
    // Every label corresponds to a real month abbreviation.
    const allMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    for (const t of texts) {
      expect(allMonths).toContain(t as string);
    }
    // A 365-day range crosses ~12-13 month transitions.
    expect(texts.length).toBeGreaterThanOrEqual(12);
    expect(texts.length).toBeLessThanOrEqual(13);
  });

  it("renders weekday labels Monday through Sunday", async () => {
    const days = buildDayRange("2025-06-22", 365);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });
    const weekdayLabels = screen.getAllByTestId("home-activity-weekday-label");
    expect(weekdayLabels.map((l) => l.textContent)).toEqual([
      "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    ]);
  });

  it("shows a custom tooltip when clicking a real activity cell", async () => {
    const user = userEvent.setup();
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days: { date: string; count: number }[] = [];
    for (let i = 364; i >= 0; i--) {
      const d = new Date(today);
      d.setUTCDate(d.getUTCDate() - i);
      const dateStr = d.toISOString().slice(0, 10);
      days.push({ date: dateStr, count: dateStr === todayStr ? 5 : 0 });
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    const activeCell = screen.getAllByTestId("home-activity-cell").find((c) => c.getAttribute("data-date") === todayStr);
    expect(activeCell).toBeDefined();
    await user.click(activeCell as HTMLElement);

    const tooltip = screen.getByTestId("home-activity-tooltip");
    expect(tooltip.textContent).toBe(`${todayStr}: 5 events`);
  });

  it("uses singular event text for count of 1", async () => {
    const user = userEvent.setup();
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days = [{ date: todayStr, count: 1 }];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    const activeCell = screen.getByLabelText(`${todayStr}: 1 event`);
    await user.click(activeCell);

    expect(screen.getByTestId("home-activity-tooltip").textContent).toBe(`${todayStr}: 1 event`);
  });

  it("hides the tooltip after 3 seconds", async () => {
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days = [{ date: todayStr, count: 2 }];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    const activeCell = screen.getByLabelText(`${todayStr}: 2 events`);
    await user.click(activeCell);
    expect(screen.getByTestId("home-activity-tooltip")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    expect(screen.queryByTestId("home-activity-tooltip")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("replaces tooltip and restarts timer when clicking a different cell", async () => {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    const todayStr = today.toISOString().slice(0, 10);
    const yesterdayStr = yesterday.toISOString().slice(0, 10);
    const days = [
      { date: yesterdayStr, count: 1 },
      { date: todayStr, count: 2 },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    const yesterdayCell = screen.getByLabelText(`${yesterdayStr}: 1 event`);
    const todayCell = screen.getByLabelText(`${todayStr}: 2 events`);

    await user.click(yesterdayCell);
    expect(screen.getByTestId("home-activity-tooltip").textContent).toBe(`${yesterdayStr}: 1 event`);

    await vi.advanceTimersByTimeAsync(2000);
    await user.click(todayCell);
    expect(screen.getByTestId("home-activity-tooltip").textContent).toBe(`${todayStr}: 2 events`);

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByTestId("home-activity-tooltip")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(1000);
    expect(screen.queryByTestId("home-activity-tooltip")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("no longer renders native title attribute on real cells", async () => {
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days = [{ date: todayStr, count: 2 }];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    const activeCell = screen.getByLabelText(`${todayStr}: 2 events`);
    expect(activeCell).not.toHaveAttribute("title");
  });

  it("keeps padding cells inert and without tooltip", async () => {
    const user = userEvent.setup();
    const days = buildDayRange("2025-06-22", 365);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    const paddingCell = screen.getAllByTestId("home-activity-cell").find((c) => c.getAttribute("data-date") === "");
    expect(paddingCell).toBeDefined();
    expect(paddingCell).toHaveAttribute("aria-hidden", "true");
    expect(paddingCell).not.toHaveAttribute("tabindex");

    await user.click(paddingCell as HTMLElement);
    expect(screen.queryByTestId("home-activity-tooltip")).not.toBeInTheDocument();
  });

  it("activates tooltip with Enter and Space on real cells", async () => {
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const days = [{ date: todayStr, count: 3 }];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ days }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-activity-grid")).toBeInTheDocument();
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    const activeCell = screen.getByLabelText(`${todayStr}: 3 events`);
    activeCell.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("home-activity-tooltip").textContent).toBe(`${todayStr}: 3 events`);

    await vi.advanceTimersByTimeAsync(3000);
    expect(screen.queryByTestId("home-activity-tooltip")).not.toBeInTheDocument();

    await user.keyboard(" ");
    expect(screen.getByTestId("home-activity-tooltip").textContent).toBe(`${todayStr}: 3 events`);

    vi.useRealTimers();
  });

  it("renders the score distribution card after the activity card", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ scoreDistributionBuckets: [{ start: 0, neverTested: 0, minAged: 0, tested: 1, cooldown: 0 }] }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution")).toBeInTheDocument();
    });
    const activity = screen.getByTestId("home-activity-grid");
    const distribution = screen.getByTestId("home-score-distribution");
    expect(activity.compareDocumentPosition(distribution))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("renders score distribution bucket labels and stacked counts in ascending order", async () => {
    const buckets = [
      { start: -1, neverTested: 1, minAged: 0, tested: 0, cooldown: 0 },
      { start: 0, neverTested: 0, minAged: 0, tested: 2, cooldown: 0 },
      { start: 2, neverTested: 3, minAged: 1, tested: 1, cooldown: 0 },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ scoreDistributionBuckets: buckets }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution-plot")).toBeInTheDocument();
    });

    const labels = screen.getAllByTestId("home-score-distribution-bucket-label").map((el) => el.textContent?.trim());
    expect(labels).toEqual(["-1–0", "0–+1", "+2–+3"]);

    const columns = screen.getAllByTestId("home-score-distribution-column");
    expect(columns.map((c) => c.getAttribute("data-start"))).toEqual(["-1", "0", "2"]);

    const legend = screen.getAllByTestId("home-score-distribution-legend").map((el) => el.textContent?.trim());
    expect(legend).toEqual(["Never tested", "Min aged", "Tested", "Cooldown"]);

    const neverTestedSwatch = screen.getByTestId("home-score-distribution-legend-never-tested");
    const minAgedSwatch = screen.getByTestId("home-score-distribution-legend-min-aged");
    const testedSwatch = screen.getByTestId("home-score-distribution-legend-tested");
    const cooldownSwatch = screen.getByTestId("home-score-distribution-legend-cooldown");
    expect(neverTestedSwatch.style.backgroundColor).toBe("var(--color-border)");
    expect(minAgedSwatch.style.backgroundColor).toBe("var(--color-text-muted)");
    expect(testedSwatch.style.backgroundColor).toBe("var(--color-primary)");
    expect(cooldownSwatch.style.backgroundColor).toBe("var(--color-warning)");
  });

  it("renders score distribution count values for tested and never-tested counts", async () => {
    const buckets = [
      { start: 0, neverTested: 2, minAged: 0, tested: 3, cooldown: 0 },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ scoreDistributionBuckets: buckets }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution-plot")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution-tested-count-value").textContent).toBe("3");
    });
    expect(screen.getByTestId("home-score-distribution-never-tested-count-value").textContent).toBe("2");
  });

  it("renders all four category segments with count values and accessible labels", async () => {
    const buckets = [
      { start: 0, neverTested: 1, minAged: 1, tested: 1, cooldown: 1 },
    ];
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ scoreDistributionBuckets: buckets }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution-plot")).toBeInTheDocument();
    });

    expect(screen.getByTestId("home-score-distribution-cooldown-count-value").textContent).toBe("1");
    expect(screen.getByTestId("home-score-distribution-tested-count-value").textContent).toBe("1");
    expect(screen.getByTestId("home-score-distribution-min-aged-count-value").textContent).toBe("1");
    expect(screen.getByTestId("home-score-distribution-never-tested-count-value").textContent).toBe("1");

    const column = screen.getByTestId("home-score-distribution-column");
    expect(column.getAttribute("aria-label")).toBe("0\u2013+1: 1 cooldown, 1 tested, 1 min aged, 1 never tested");
  });

  it("renders an empty state when there are no score distribution buckets", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ scoreDistributionBuckets: [] }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-score-distribution-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("home-score-distribution-plot")).not.toBeInTheDocument();
  });
});

function buildDayRange(startDate: string, count: number): { date: string; count: number }[] {
  const out: { date: string; count: number }[] = [];
  const start = new Date(`${startDate}T00:00:00Z`);
  for (let i = 0; i < count; i++) {
    const d = new Date(start);
    d.setUTCDate(start.getUTCDate() + i);
    out.push({ date: d.toISOString().slice(0, 10), count: 0 });
  }
  return out;
}
