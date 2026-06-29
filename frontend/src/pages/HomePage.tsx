import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "@/contexts/ThemeContext";
import { api, ApiError } from "@/api/client";

interface HomeCoverage {
  totalProblems: number;
  triedProblems: number;
  percentage: number;
}

interface HomeConquest {
  totalProblems: number;
  masteredProblems: number;
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
  conquest: HomeConquest;
  activity: HomeActivity;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTH_ABBREVIATIONS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
const CELL_SIZE_PX = 11;
const CELL_GAP_PX = 3;
const COLUMN_WIDTH_PX = CELL_SIZE_PX + CELL_GAP_PX;

function parseUtcDate(value: string): Date {
  return new Date(`${value}T00:00:00Z`);
}

function mondayIndex(date: Date): number {
  return (date.getUTCDay() + 6) % 7;
}

function buildWeekColumns(days: HomeActivityDay[]): (HomeActivityDay | null)[][] {
  if (days.length === 0) return [];
  const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));
  const firstDate = parseUtcDate(sorted[0].date);
  const startRow = mondayIndex(firstDate);

  const columns: (HomeActivityDay | null)[][] = [];
  for (let i = 0; i < sorted.length; i++) {
    const offset = startRow + i;
    const colIndex = Math.floor(offset / 7);
    const row = offset % 7;
    if (!columns[colIndex]) {
      columns[colIndex] = new Array(7).fill(null);
    }
    columns[colIndex][row] = sorted[i];
  }
  return columns;
}

interface MonthLabel {
  columnIndex: number;
  text: string;
}

function buildMonthLabels(columns: (HomeActivityDay | null)[][]): MonthLabel[] {
  const labels: MonthLabel[] = [];
  let previousMonth = -1;
  columns.forEach((column, columnIndex) => {
    const firstDay = column.find((cell): cell is HomeActivityDay => cell !== null);
    if (!firstDay) return;
    const month = parseUtcDate(firstDay.date).getUTCMonth();
    if (month !== previousMonth) {
      labels.push({ columnIndex, text: MONTH_ABBREVIATIONS[month] });
      previousMonth = month;
    }
  });
  return labels;
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

  const [activeTooltip, setActiveTooltip] = useState<{ date: string; text: string } | null>(null);
  const tooltipTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showTooltip = (date: string, text: string) => {
    if (tooltipTimerRef.current) {
      clearTimeout(tooltipTimerRef.current);
    }
    setActiveTooltip({ date, text });
    tooltipTimerRef.current = setTimeout(() => {
      setActiveTooltip(null);
    }, 3000);
  };

  useEffect(() => {
    return () => {
      if (tooltipTimerRef.current) {
        clearTimeout(tooltipTimerRef.current);
      }
    };
  }, []);

  const pageCanvasStyle = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    padding: "1.5rem",
  } as const;
  const contentStyle = { maxWidth: "1100px", margin: "0 auto" } as const;

  if (isLoading) {
    return (
      <main style={pageCanvasStyle}>
        <div data-testid="loading" style={{ ...contentStyle, padding: "0.5rem 0", color: "var(--color-text-muted)" }}>
          Loading dashboard...
        </div>
      </main>
    );
  }

  if (error) {
    const message = error instanceof ApiError ? error.message : "Failed to load dashboard";
    return (
      <main style={pageCanvasStyle}>
        <div data-testid="error" style={{ ...contentStyle, padding: "0.5rem 0", color: "var(--color-text-danger)" }}>
          {message}
        </div>
      </main>
    );
  }

  if (!data) {
    return <main style={pageCanvasStyle} />;
  }

  const { coverage, conquest, activity } = data;
  const maxCount = activity.days.reduce((max, day) => Math.max(max, day.count), 0);
  const weekColumns = buildWeekColumns(activity.days);
  const monthLabels = buildMonthLabels(weekColumns);
  const zeroProblems = coverage.totalProblems === 0;

  const statCardStyle = {
    flex: "1 1 0",
    minWidth: "240px",
    padding: "1.5rem",
    backgroundColor: "var(--color-surface)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-lg)",
    boxShadow: "var(--shadow-sm)",
  } as const;

  return (
    <main style={pageCanvasStyle}>
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes home-tooltip-fade-in {
          from { opacity: 0; transform: translateX(-50%) translateY(4px); }
          to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
      ` }} />
      <div style={contentStyle}>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 800, marginBottom: "1.5rem", color: "var(--color-text)" }}>
          Home
        </h1>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", marginBottom: "1.5rem" }}>
        <section style={statCardStyle}>
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

        <section style={statCardStyle}>
          <div style={{ fontSize: "0.8125rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginBottom: "0.5rem" }}>
            Conquest Rate
          </div>
          <div data-testid="home-conquest-percentage" style={{ fontSize: "3.5rem", fontWeight: 800, lineHeight: 1, color: "var(--color-primary)" }}>
            {conquest.percentage}%
          </div>
          <div data-testid="home-conquest-text" style={{ marginTop: "0.5rem", color: "var(--color-text-muted)", fontSize: "0.95rem" }}>
            {zeroProblems ? (
              "No problems yet. Add problems to start tracking conquest."
            ) : (
              <>
                {conquest.masteredProblems} of {conquest.totalProblems} problems mastered
              </>
            )}
          </div>
        </section>
      </div>

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
          <div style={{ display: "inline-flex", flexDirection: "column", gap: "0.25rem" }}>
            <div style={{ display: "flex" }}>
              <div style={{ width: `${COLUMN_WIDTH_PX + 4}px` }} aria-hidden="true" />
              <div
                style={{
                  position: "relative",
                  height: "0.85rem",
                  width: `${weekColumns.length * COLUMN_WIDTH_PX}px`,
                }}
              >
                {monthLabels.map((label) => (
                  <span
                    key={`${label.text}-${label.columnIndex}`}
                    data-testid="home-activity-month-label"
                    style={{
                      position: "absolute",
                      left: `${label.columnIndex * COLUMN_WIDTH_PX}px`,
                      top: 0,
                      fontSize: "0.625rem",
                      color: "var(--color-text-muted)",
                      lineHeight: "0.85rem",
                    }}
                  >
                    {label.text}
                  </span>
                ))}
              </div>
            </div>
            <div style={{ display: "flex", gap: `${CELL_GAP_PX}px` }}>
              <div style={{ display: "flex", flexDirection: "column", gap: `${CELL_GAP_PX}px`, marginRight: "0.25rem", justifyContent: "space-around" }}>
                {WEEKDAY_LABELS.map((label) => (
                  <div
                    key={label}
                    data-testid="home-activity-weekday-label"
                    style={{ fontSize: "0.625rem", color: "var(--color-text-muted)", height: `${CELL_SIZE_PX}px`, lineHeight: `${CELL_SIZE_PX}px` }}
                  >
                    {label}
                  </div>
                ))}
              </div>
              {weekColumns.map((column, colIndex) => (
                <div
                  key={colIndex}
                  data-testid="home-activity-week-column"
                  style={{ display: "flex", flexDirection: "column", gap: `${CELL_GAP_PX}px` }}
                >
                  {column.map((day, rowIndex) => {
                    const intensity = day ? cellIntensity(day.count, maxCount) : 0;
                    const background =
                      day && day.count > 0
                        ? `color-mix(in srgb, var(--color-primary) ${Math.round(intensity * 100)}%, transparent)`
                        : "var(--color-surface-muted)";
                    if (!day) {
                      return (
                        <div
                          key={rowIndex}
                          data-testid="home-activity-cell"
                          data-date=""
                          data-count={0}
                          aria-hidden="true"
                          style={{
                            width: `${CELL_SIZE_PX}px`,
                            height: `${CELL_SIZE_PX}px`,
                            borderRadius: "2px",
                            backgroundColor: background,
                          }}
                        />
                      );
                    }

                    const tooltipText = `${day.date}: ${day.count} event${day.count === 1 ? "" : "s"}`;
                    const isActive = activeTooltip?.date === day.date;
                    return (
                      <div key={rowIndex} style={{ position: "relative" }}>
                        <button
                          data-testid="home-activity-cell"
                          data-date={day.date}
                          data-count={day.count}
                          type="button"
                          aria-label={tooltipText}
                          onClick={() => showTooltip(day.date, tooltipText)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              showTooltip(day.date, tooltipText);
                            }
                          }}
                          style={{
                            width: `${CELL_SIZE_PX}px`,
                            height: `${CELL_SIZE_PX}px`,
                            borderRadius: "2px",
                            backgroundColor: background,
                            border: "none",
                            padding: 0,
                            cursor: "default",
                          }}
                        />
                        {isActive && (
                          <span
                            data-testid="home-activity-tooltip"
                            style={{
                              position: "absolute",
                              bottom: "calc(100% + 4px)",
                              left: "50%",
                              transform: "translateX(-50%)",
                              whiteSpace: "nowrap",
                              padding: "0.25rem 0.5rem",
                              backgroundColor: "var(--color-surface)",
                              color: "var(--color-text)",
                              border: "1px solid var(--color-border)",
                              borderRadius: "var(--radius-md)",
                              fontSize: "0.75rem",
                              boxShadow: "var(--shadow-md)",
                              zIndex: 10,
                              animation: "home-tooltip-fade-in 150ms ease-out",
                            }}
                          >
                            {tooltipText}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
    </main>
  );
}
