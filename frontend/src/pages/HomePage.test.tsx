import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

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
  days: { date: string; count: number }[];
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
    activity: {
      startDate: days[0].date,
      endDate: days[days.length - 1].date,
      days: overrides.days ?? days,
    },
  };
}

describe("HomePage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
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

  it("renders zero-problem state with 0% and note", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => summaryResponse({ totalProblems: 0, triedProblems: 0, percentage: 0, masteredProblems: 0, conquestPercentage: 0 }),
    });
    renderHomePage();
    await waitFor(() => {
      expect(screen.getByTestId("home-coverage-percentage").textContent).toBe("0%");
    });
    expect(screen.getByTestId("home-coverage-text").textContent).toContain("No problems yet");
    expect(screen.getByTestId("home-conquest-percentage").textContent).toBe("0%");
    expect(screen.getByTestId("home-conquest-text").textContent).toContain("No problems yet");
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
