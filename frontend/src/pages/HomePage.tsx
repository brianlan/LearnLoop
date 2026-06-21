import { useQuery } from "@tanstack/react-query";
import { useTheme } from "@/contexts/ThemeContext";
import { api, ApiError } from "@/api/client";

interface HomeCoverage {
  totalProblems: number;
  triedProblems: number;
  percentage: number;
}

interface HomeActivityDay {
  date: string;
  count: number;
}

interface HomeActivity {
  startDate: string;
  endDate: string;
  days: HomeActivityDay[];
}

interface HomeSummaryResponse {
  coverage: HomeCoverage;
  activity: HomeActivity;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

function buildWeekColumns(days: HomeActivityDay[]): (HomeActivityDay | null)[][] {
  const columns: (HomeActivityDay | null)[][] = [];
  days.forEach((day) => {
    const date = new Date(`${day.date}T00:00:00Z`);
    const row = mondayIndex(date);
    const lastColumn = columns[columns.length - 1];
    if (!lastColumn || lastColumn.every((cell) => cell === null)) {
      const column: (HomeActivityDay | null)[] = new Array(7).fill(null);
      column[row] = day;
      columns.push(column);
    } else {
      const firstDayInColumn = lastColumn.find((cell) => cell !== null);
      if (firstDayInColumn) {
        const firstDate = new Date(`${firstDayInColumn.date}T00:00:00Z`);
        const dayDiff = Math.round(
          (date.getTime() - firstDate.getTime()) / (1000 * 60 * 60 * 24),
        );
        const expectedColumnIndex = Math.floor((dayDiff + mondayIndex(firstDate)) / 7);
        if (expectedColumnIndex === columns.length - 1) {
          lastColumn[row] = day;
        } else {
          const column: (HomeActivityDay | null)[] = new Array(7).fill(null);
          column[row] = day;
          columns.push(column);
        }
      }
    }
  });
  return columns;
}

function cellIntensity(count: number, maxCount: number): number {
  if (count <= 0 || maxCount <= 0) return 0;
  return Math.min(0.25 + (count / maxCount) * 0.75, 1);
}

export function HomePage() {
  const { theme } = useTheme();
  const { data, isLoading, error } = useQuery<HomeSummaryResponse>({
    queryKey: ["home-summary"],
    queryFn: () => api.get<HomeSummaryResponse>("/home/summary"),
  });

  if (isLoading) {
    return (
      <div data-testid="loading" style={{ padding: "2rem", color: "var(--color-text-muted)" }}>
        Loading dashboard...
      </div>
    );
  }

  if (error) {
    const message = error instanceof ApiError ? error.message : "Failed to load dashboard";
    return (
      <div data-testid="error" style={{ padding: "2rem", color: "var(--color-text-danger)" }}>
        {message}
      </div>
    );
  }

  if (!data) {
    return null;
  }

  const { coverage, activity } = data;
  const maxCount = activity.days.reduce((max, day) => Math.max(max, day.count), 0);
  const weekColumns = buildWeekColumns(activity.days);
  const zeroProblems = coverage.totalProblems === 0;

  return (
    <div style={{ padding: "1.5rem", maxWidth: "1100px", margin: "0 auto" }}>
      <h1 style={{ fontSize: "1.75rem", fontWeight: 800, marginBottom: "1.5rem", color: "var(--color-text)" }}>
        Home
      </h1>

      <section
        style={{
          padding: "1.5rem",
          marginBottom: "1.5rem",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <div style={{ fontSize: "0.8125rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginBottom: "0.5rem" }}>
          Problem Coverage
        </div>
        <div data-testid="home-coverage-percentage" style={{ fontSize: "3.5rem", fontWeight: 800, lineHeight: 1, color: "var(--color-primary)" }}>
          {coverage.percentage}%
        </div>
        <div data-testid="home-coverage-text" style={{ marginTop: "0.5rem", color: "var(--color-text-muted)", fontSize: "0.95rem" }}>
          {zeroProblems ? (
            "No problems yet. Add problems to start tracking coverage."
          ) : (
            <>
              {coverage.triedProblems} of {coverage.totalProblems} problems tried
            </>
          )}
        </div>
      </section>

      <section
        style={{
          padding: "1.5rem",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <div style={{ fontSize: "0.8125rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginBottom: "0.75rem" }}>
          Activity (last year)
        </div>
        <div data-testid="home-activity-grid" style={{ overflowX: "auto" }}>
          <div style={{ display: "flex", gap: "0.25rem" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.125rem", marginRight: "0.25rem", justifyContent: "space-around" }}>
              {WEEKDAY_LABELS.map((label) => (
                <div key={label} style={{ fontSize: "0.625rem", color: "var(--color-text-muted)", height: "12px", lineHeight: "12px" }}>
                  {label}
                </div>
              ))}
            </div>
            {weekColumns.map((column, colIndex) => (
              <div key={colIndex} style={{ display: "flex", flexDirection: "column", gap: "0.125rem" }}>
                {column.map((day, rowIndex) => {
                  const intensity = day ? cellIntensity(day.count, maxCount) : 0;
                  const background =
                    day && day.count > 0
                      ? `color-mix(in srgb, var(--color-primary) ${Math.round(intensity * 100)}%, transparent)`
                      : "var(--color-surface-muted)";
                  return (
                    <div
                      key={rowIndex}
                      data-testid="home-activity-cell"
                      data-date={day?.date ?? ""}
                      data-count={day?.count ?? 0}
                      title={day ? `${day.date}: ${day.count} event${day.count === 1 ? "" : "s"}` : ""}
                      style={{
                        width: "12px",
                        height: "12px",
                        borderRadius: "2px",
                        backgroundColor: background,
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
