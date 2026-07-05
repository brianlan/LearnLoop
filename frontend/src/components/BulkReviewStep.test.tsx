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
    onContinue: vi.fn(),
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

  it("updates the selected item's empty queued draft when extraction completes", async () => {
    const { rerender } = render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              status: "queued",
              order: 0,
              draft: {},
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-text")).toHaveValue("");

    rerender(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              status: "ready",
              order: 0,
              draft: {
                text: "Extracted selected problem text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: null,
                tags: [],
                subject: "math",
              },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-text")).toHaveValue(
        "Extracted selected problem text",
      );
    });
  });

  it("does not overwrite a dirty local draft when server draft changes", async () => {
    const { rerender } = render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              draft: {
                text: "Initial server text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-text"), {
      target: { value: "Unsaved local edit" },
    });

    rerender(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              draft: {
                text: "New server text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-text")).toHaveValue(
      "Unsaved local edit",
    );
  });

  it("uses a narrow item list column", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-layout")).toHaveStyle({
      gridTemplateColumns: "120px 1fr",
    });
  });

  it("renders text and graph previews and a resizable Graph DSL editor", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              draft: {
                text: "Compute $x^2$",
                problemType: "short-answer",
                graphDsl: "board.create('point', [0, 0]);",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-text-preview")).toHaveTextContent(
      "Compute",
    );
    expect(screen.getByTestId("graph-sandbox")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-graphdsl")).toHaveStyle({
      resize: "vertical",
      minHeight: "180px",
    });
  });

  it("shows tag autocomplete suggestions from existing tags", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        tagSuggestions={["algebra", "geometry"]}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), {
      target: { value: "a" },
    });

    expect(screen.getByTestId("bulk-review-tags-suggestion-algebra")).toBeInTheDocument();
  });

  it("requires complete draft fields before continuing to submit", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              draft: {
                text: "What is 2+2?",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "",
                tags: [],
                subject: "math",
              },
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-continue")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-continue-reasons")).toHaveTextContent(
      "Correct answer is required",
    );
  });

  it("continues to submit after all active items are ready and valid", () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-review-continue"));

    expect(handlers.onContinue).toHaveBeenCalledTimes(1);
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

  it("edits every draft field and routes the correct key to onUpdateDraft", async () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [makeItem("item-1", { order: 0 })],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-text"), {
      target: { value: "New question text" },
    });
    fireEvent.change(screen.getByTestId("bulk-review-type"), {
      target: { value: "single-choice" },
    });
    fireEvent.change(screen.getByTestId("bulk-review-subject"), {
      target: { value: "english" },
    });
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "B" },
    });
    fireEvent.change(screen.getByTestId("bulk-review-graphdsl"), {
      target: { value: "y = x" },
    });

    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "geometry" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });

    expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({
        text: "New question text",
        problemType: "single-choice",
        subject: "english",
        correctAnswer: "B",
        graphDsl: "y = x",
        tags: ["math", "geometry"],
      }),
    );
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

  it("retries a failed draft save after a bounded backoff delay", async () => {
    handlers.onUpdateDraft.mockRejectedValueOnce(new Error("network error"));

    render(
      <BulkReviewStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "A" },
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });
  });

  it("increases retry backoff up to a cap", async () => {
    handlers.onUpdateDraft.mockRejectedValue(new Error("persistent error"));

    render(
      <BulkReviewStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "A" },
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(3);

    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(4);

    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(5);
  });

  it("clears the failure state and stops retrying after a successful save", async () => {
    handlers.onUpdateDraft
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(undefined);

    render(
      <BulkReviewStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "A" },
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);

    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-save-status")).toBeInTheDocument();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("bulk-review-save-status"),
      ).not.toBeInTheDocument();
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
  });

  it("shows a save-failed indicator that disappears on success", async () => {
    handlers.onUpdateDraft
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(undefined);

    render(
      <BulkReviewStep
        batch={makeBatch({ items: [makeItem("item-1", { order: 0 })] })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "A" },
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);

    expect(screen.getByTestId("bulk-review-save-status")).toHaveTextContent(
      "Save failed, retrying...",
    );

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("bulk-review-save-status"),
      ).not.toBeInTheDocument();
    });
  });
});
