import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useGraphDslValidation } from "./useGraphDslValidation";

describe("useGraphDslValidation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not validate an empty DSL", () => {
    const validate = vi.spyOn(api, "validateGraphDsl");
    const { result } = renderHook(() => useGraphDslValidation(""));

    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(validate).not.toHaveBeenCalled();
    expect(result.current).toEqual({ issues: [], isValidating: false });
  });

  it("debounces validation and returns the backend issues", async () => {
    const validate = vi
      .spyOn(api, "validateGraphDsl")
      .mockResolvedValue({ valid: false, issues: ["disallowed element type"] });

    const { result } = renderHook(() =>
      useGraphDslValidation("board.create('rectangle', [0, 0, 1, 1]);"),
    );

    expect(validate).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(validate).toHaveBeenCalledWith(
      "board.create('rectangle', [0, 0, 1, 1]);",
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(result.current.issues).toEqual(["disallowed element type"]);
    expect(result.current.isValidating).toBe(false);
  });

  it("aborts a stale in-flight request when the DSL changes again", async () => {
    let firstSignal: AbortSignal | undefined;
    vi.spyOn(api, "validateGraphDsl").mockImplementation((dsl, options) => {
      if (dsl === "first") {
        firstSignal = options?.signal;
        return new Promise(() => {});
      }
      return Promise.resolve({ valid: false, issues: ["second issue"] });
    });

    const { result, rerender } = renderHook(
      ({ dsl }: { dsl: string }) => useGraphDslValidation(dsl),
      { initialProps: { dsl: "first" } },
    );

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(firstSignal?.aborted).toBe(false);

    rerender({ dsl: "second" });
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.issues).toEqual(["second issue"]);
  });

  it("clears issues when the DSL becomes empty", async () => {
    const validate = vi
      .spyOn(api, "validateGraphDsl")
      .mockResolvedValue({ valid: false, issues: ["bad"] });

    const { result, rerender } = renderHook(
      ({ dsl }: { dsl: string }) => useGraphDslValidation(dsl),
      { initialProps: { dsl: "bad" } },
    );

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.issues).toEqual(["bad"]);

    rerender({ dsl: "" });
    expect(result.current.issues).toEqual([]);
    expect(validate).toHaveBeenCalledTimes(1);
  });
});
