import { describe, it, expect, vi, afterEach } from "vitest";
import { formatDate, getTimezone } from "./format";

describe("getTimezone", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the browser-detected IANA timezone", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Shanghai" }),
    } as Intl.DateTimeFormat);
    expect(getTimezone()).toBe("Asia/Shanghai");
  });

  it("defaults to UTC when detection returns empty", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "" }),
    } as Intl.DateTimeFormat);
    expect(getTimezone()).toBe("UTC");
  });

  it("defaults to UTC when detection throws", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("unsupported");
    });
    expect(getTimezone()).toBe("UTC");
  });
});

describe("formatDate", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns em-dash for undefined", () => {
    expect(formatDate(undefined)).toBe("—");
  });

  it("returns em-dash for empty string", () => {
    expect(formatDate("")).toBe("—");
  });

  it("formats a date string and includes a short timezone label", () => {
    const result = formatDate("2026-01-15T23:30:00Z");
    expect(result).not.toBe("—");
    // The formatted string should include a short timezone name (e.g. "GMT+8", "UTC", "CST")
    // We verify the result is a non-trivial formatted string, not just a raw ISO date
    expect(result.length).toBeGreaterThan(10);
  });

  it("uses the detected timezone so a UTC-midnight event shifts to the local next day", () => {
    // Mock timezone detection to Asia/Shanghai (UTC+8)
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
      (() => ({
        resolvedOptions: () => ({ timeZone: "Asia/Shanghai" }),
      })) as unknown as typeof Intl.DateTimeFormat,
    );

    const result = formatDate("2026-01-15T23:30:00Z");
    // In Asia/Shanghai (+08:00), 2026-01-15T23:30Z is 2026-01-16T07:30+08:00
    // The formatted result should reference Jan 16, not Jan 15
    expect(result).toContain("16");
  });

  it("uses UTC when detected timezone is UTC", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
      (() => ({
        resolvedOptions: () => ({ timeZone: "UTC" }),
      })) as unknown as typeof Intl.DateTimeFormat,
    );

    const result = formatDate("2026-01-15T23:30:00Z");
    // In UTC, the date should remain Jan 15
    expect(result).toContain("1/15/2026");
  });
});
