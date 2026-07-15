import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getPracticeHistory,
  getPracticeStats,
  startPractice,
  submitPracticeAttempt,
  nextPracticeProblem,
  PRACTICE_HISTORY_KEY,
  PRACTICE_STATS_KEY,
} from "./practice";
import type {
  PracticeAttemptResult,
  PracticeHistoryResponse,
  PracticeNextResponse,
  PracticeStatsResponse,
} from "@/types/practice";

describe("practice API module", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("exports the exact query keys", () => {
    expect(PRACTICE_HISTORY_KEY).toEqual(["practice-history"]);
    expect(PRACTICE_STATS_KEY).toEqual(["practice-stats"]);
  });

  it("getPracticeHistory GETs /practice/history", async () => {
    const response: PracticeHistoryResponse = { items: [] };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await getPracticeHistory();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/practice/history", {
      credentials: "include",
    });
    expect(result).toEqual(response);
  });

  it("getPracticeStats GETs /practice/stats", async () => {
    const response: PracticeStatsResponse = { practiceableCount: 5 };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await getPracticeStats();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/practice/stats", {
      credentials: "include",
    });
    expect(result).toEqual(response);
  });

  it("startPractice POSTs an empty object to /practice/next", async () => {
    const response: PracticeNextResponse = {
      status: "ok",
      problem: { id: "p1", text: "Q", type: "short-answer" },
    };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await startPractice();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/practice/next", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
    });
    expect(result).toEqual(response);
  });

  it("submitPracticeAttempt POSTs problemId and submittedAnswer to /practice/attempts", async () => {
    const response: PracticeAttemptResult = {
      gradingStatus: "correct",
      gradingMethod: "normalized-match",
    };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await submitPracticeAttempt("p1", "42");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/practice/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ problemId: "p1", submittedAnswer: "42" }),
    });
    expect(result).toEqual(response);
  });

  it("nextPracticeProblem POSTs an empty object to /practice/next", async () => {
    const response: PracticeNextResponse = { status: "no_eligible" };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await nextPracticeProblem();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/practice/next", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
    });
    expect(result).toEqual(response);
  });
});
