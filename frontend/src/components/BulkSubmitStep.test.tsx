import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { BulkSubmitStep } from "./BulkSubmitStep";
import type { BulkBatch, BulkItem } from "@/types/bulkIngestion";

function makeItem(itemId: string, overrides: Partial<BulkItem> = {}): BulkItem {
  return {
    itemId,
    imageId: `img-${itemId}`,
    batchId: "batch-1",
    status: "ready",
    order: 0,
    draft: {
      text: "What is 2+2?",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "4",
      tags: [],
      subject: "math",
    },
    extraction: {},
    retryCount: 0,
    submit: {},
    origin: {},
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    ...overrides,
  };
}

function makeBatch(overrides: Partial<BulkBatch> = {}): BulkBatch {
  return {
    id: "batch-1",
    userId: "user-1",
    status: "active",
    images: [],
    items: [],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
    ...overrides,
  };
}

describe("BulkSubmitStep", () => {
  const handlers = {
    onSubmit: vi.fn(),
    onRetry: vi.fn(),
    onDelete: vi.fn(),
    onBackToReview: vi.fn(),
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    Object.values(handlers).forEach((fn) => fn.mockReset());
  });

  it("renders the submit queue", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-wizard-submit-step")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-submit-queue")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-submit-item-item-1")).toBeInTheDocument();
  });

  it("calls onBackToReview from the back button", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-submit-back-review"));

    expect(handlers.onBackToReview).toHaveBeenCalledTimes(1);
  });

  it("enables submit when all active items are ready with required fields", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", { order: 0 }),
            makeItem("item-2", { order: 1 }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).not.toBeDisabled();
  });

  it("disables submit when an item is missing text", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [makeItem("item-1", { draft: { correctAnswer: "4", problemType: "short-answer" } })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Question text is required");
  });

  it("disables submit when an item is missing problemType", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              draft: { text: "What is 2+2?", correctAnswer: "4" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Problem type is required");
  });

  it("disables submit when an item is missing correctAnswer", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              draft: { text: "What is 2+2?", problemType: "short-answer" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Correct answer is required");
  });

  it("disables submit when an item is not ready", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "extracting" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Item is not ready");
  });

  it("disables submit when an item failed extraction", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "failed" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Item is not ready");
  });

  it("disables submit when an item failed submission", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "submit-failed" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(
      screen.getByTestId("bulk-submit-item-reasons-item-1"),
    ).toHaveTextContent("Item is not ready");
  });

  it("ignores deleted items when computing the submit gate", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", { order: 0 }),
            makeItem("item-2", {
              order: 1,
              status: "deleted",
              draft: { correctAnswer: "", problemType: "", text: "" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-button")).not.toBeDisabled();
  });

  it("calls onSubmit when clicking the submit button", async () => {
    handlers.onSubmit.mockResolvedValue(undefined);

    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("bulk-submit-button"));
    });

    await waitFor(() => {
      expect(handlers.onSubmit).toHaveBeenCalledWith("batch-1");
    });
  });

  it("disables the submit button while submitting", async () => {
    let resolveSubmit: () => void = () => undefined;
    handlers.onSubmit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );

    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("bulk-submit-button"));
    });

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(screen.getByTestId("bulk-submit-button")).toHaveTextContent(
      "Submitting...",
    );

    await act(async () => {
      resolveSubmit();
    });
  });

  it("renders the result summary after partial submission", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submitted",
              submit: { submittedProblemId: "problem-1" },
            }),
            makeItem("item-2", {
              order: 1,
              status: "submit-failed",
              submit: { failureMessage: "Validation error" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-submit-summary")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-submit-summary-total")).toHaveTextContent("Total: 2");
    expect(screen.getByTestId("bulk-submit-summary-submitted")).toHaveTextContent(
      "Submitted: 1",
    );
    expect(screen.getByTestId("bulk-submit-summary-failed")).toHaveTextContent(
      "Failed: 1",
    );
    expect(
      screen.getByTestId("bulk-submit-item-failure-item-2"),
    ).toHaveTextContent("Validation error");
  });

  it("renders submitted problem ids", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submitted",
              submit: { submittedProblemId: "problem-1" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(
      screen.getByTestId("bulk-submit-item-problem-item-1"),
    ).toHaveTextContent("problem-1");
  });

  it("calls onRetry for submit-failed items", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submit-failed",
              submit: { failureMessage: "Validation error" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-submit-retry-item-1"));
    expect(handlers.onRetry).toHaveBeenCalledWith("item-1");
  });

  it("keeps submitted items visible when retrying a submit-failed sibling", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submitted",
              submit: { submittedProblemId: "problem-1" },
            }),
            makeItem("item-2", {
              order: 1,
              status: "submit-failed",
              submit: { failureMessage: "Validation error" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-submit-retry-item-2"));

    expect(handlers.onRetry).toHaveBeenCalledWith("item-2");
    expect(
      screen.getByTestId("bulk-submit-item-problem-item-1"),
    ).toHaveTextContent("problem-1");
    expect(
      screen.getByTestId("bulk-submit-item-status-item-1"),
    ).toHaveTextContent("Submitted");
  });

  it("calls onDelete for submit-failed items", () => {
    render(
      <BulkSubmitStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submit-failed",
              submit: { failureMessage: "Validation error" },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-submit-delete-item-1"));
    expect(handlers.onDelete).toHaveBeenCalledWith("item-1");
  });

  it("shows an error message when submission fails", async () => {
    handlers.onSubmit.mockRejectedValue(new Error("Network error"));

    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("bulk-submit-error")).toHaveTextContent(
        "Network error",
      );
    });
  });

  it("polls onRefresh while the batch is active", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onRefresh = vi.fn();

    render(
      <BulkSubmitStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        onRefresh={onRefresh}
        {...handlers}
      />,
    );

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledWith("batch-1");
    });

    vi.useRealTimers();
  });

  it("does not poll onRefresh when the batch is completed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onRefresh = vi.fn();

    render(
      <BulkSubmitStep
        batch={makeBatch({
          status: "completed",
          items: [
            makeItem("item-1", {
              order: 0,
              status: "submitted",
              submit: { submittedProblemId: "problem-1" },
            }),
          ],
        })}
        isLoading={false}
        onRefresh={onRefresh}
        {...handlers}
      />,
    );

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(onRefresh).not.toHaveBeenCalled();

    vi.useRealTimers();
  });

  it("shows disabled reasons when there are no items", () => {
    render(
      <BulkSubmitStep batch={makeBatch()} isLoading={false} {...handlers} />,
    );

    expect(screen.getByTestId("bulk-submit-button")).toBeDisabled();
    expect(screen.getByTestId("bulk-submit-disabled-reasons")).toHaveTextContent(
      "No items to submit",
    );
  });
});
