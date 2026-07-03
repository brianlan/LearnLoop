import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { BulkReviewStep } from "./BulkReviewStep";
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
      tags: ["math"],
      subject: "math",
    },
    extraction: {},
    retryCount: 0,
    submit: {},
    origin: {},
    crop: {
      mediaUrl: `http://example.com/crop-${itemId}.png`,
    },
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
    images: [
      {
        imageId: "img-a",
        status: "committed",
        order: 0,
        sourceImage: {
          bucket: "b",
          objectKey: "k",
          mediaUrl: "http://example.com/source.png",
        },
        boxes: [],
        detection: {},
        createdAt: "2026-07-03T00:00:00Z",
        updatedAt: "2026-07-03T00:00:00Z",
      },
    ],
    items: [],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
    ...overrides,
  };
}

describe("BulkReviewStep", () => {
  const handlers = {
    onRefresh: vi.fn(),
    onUpdateDraft: vi.fn(),
    onRetry: vi.fn(),
    onDelete: vi.fn(),
    onUndoDelete: vi.fn(),
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.values(handlers).forEach((fn) => fn.mockReset());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the review queue and crop preview", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-queue")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-item-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-preview")).toHaveAttribute(
      "src",
      "http://example.com/crop-item-1.png",
    );
  });

  it("disables Previous at the first item and Next at the last item", () => {
    render(
      <BulkReviewStep
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

    expect(screen.getByTestId("bulk-review-prev")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-next")).not.toBeDisabled();

    fireEvent.click(screen.getByTestId("bulk-review-next"));

    expect(screen.getByTestId("bulk-review-prev")).not.toBeDisabled();
    expect(screen.getByTestId("bulk-review-next")).toBeDisabled();
  });

  it("debounces draft updates and sends the latest value", async () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "4" } });
    fireEvent.change(answerInput, { target: { value: "42" } });
    fireEvent.change(answerInput, { target: { value: "420" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });

    expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({ correctAnswer: "420" }),
    );
  });

  it("saves the latest draft value typed while a save is in flight", async () => {
    let resolveSave: (value: unknown) => void = () => undefined;
    handlers.onUpdateDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );

    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "first" } });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });

    fireEvent.change(answerInput, { target: { value: "second" } });

    await act(async () => {
      resolveSave(undefined);
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });

    expect(handlers.onUpdateDraft).toHaveBeenLastCalledWith(
      "item-1",
      expect.objectContaining({ correctAnswer: "second" }),
    );
  });

  it("disables fields and shows undo for deleted items", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "deleted" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-undo")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-text")).toBeDisabled();

    fireEvent.click(screen.getByTestId("bulk-review-undo"));
    expect(handlers.onUndoDelete).toHaveBeenCalledWith("item-1");
  });

  it("shows retry and failure reason for failed items", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem(
              "item-1",
              {
                status: "failed",
                extraction: { failureMessage: "VLM timeout" },
              },
            ),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-failure")).toHaveTextContent(
      "VLM timeout",
    );
    expect(screen.getByTestId("bulk-review-retry")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("bulk-review-retry"));
    expect(handlers.onRetry).toHaveBeenCalledWith("item-1");
  });

  it("polls the batch while extraction is active", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "extracting" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(handlers.onRefresh).not.toHaveBeenCalled();

    vi.advanceTimersByTime(2500);
    expect(handlers.onRefresh).toHaveBeenCalledTimes(1);
    expect(handlers.onRefresh).toHaveBeenCalledWith("batch-1");

    vi.advanceTimersByTime(2500);
    expect(handlers.onRefresh).toHaveBeenCalledTimes(2);
  });

  it("stops polling when all items are terminal", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { status: "ready" })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    vi.advanceTimersByTime(10000);
    expect(handlers.onRefresh).not.toHaveBeenCalled();
  });

  it("calls onDelete when clicking delete for a non-deleted item", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1")],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-review-delete"));
    expect(handlers.onDelete).toHaveBeenCalledWith("item-1");
  });
});
