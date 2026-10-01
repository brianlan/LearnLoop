import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { BulkReviewStep } from "./BulkReviewStep";
import { variantPassGateReason } from "./BulkReviewStep.helpers";
import type {
  BulkBatch,
  BulkItem,
  BulkItemVariation,
} from "@/types/bulkIngestion";

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
    contentRevision: 0,
    variation: null,
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
    ingestionMode: "original",
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
    onGenerate: vi.fn(),
    onRevalidate: vi.fn(),
    onAttest: vi.fn(),
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

  it("uses tags added to one bulk item as suggestions for another item", () => {
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

    const firstTagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(firstTagInput, { target: { value: "calculus-new" } });
    fireEvent.keyDown(firstTagInput, { key: "Enter", code: "Enter" });

    fireEvent.click(screen.getByTestId("bulk-review-next"));

    const secondTagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(secondTagInput, { target: { value: "cal" } });

    expect(
      screen.getByTestId("bulk-review-tags-suggestion-calculus-new"),
    ).toBeInTheDocument();
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
    expect(screen.queryByTestId("bulk-review-continue-reasons")).not.toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-item-item-1")).toHaveAttribute(
      "data-action-required",
      "true",
    );
    expect(screen.getByTestId("bulk-review-answer").style.border).toBe(
      "2px solid var(--color-error, #dc2626)",
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

  it("navigates to the next item on Alt+PageDown", () => {
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

    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("1 / 2");

    fireEvent(
      window,
      new KeyboardEvent("keydown", { key: "PageDown", altKey: true }),
    );

    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("2 / 2");
  });

  it("navigates to the previous item on Alt+PageUp", () => {
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

    fireEvent.click(screen.getByTestId("bulk-review-next"));
    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("2 / 2");

    fireEvent(
      window,
      new KeyboardEvent("keydown", { key: "PageUp", altKey: true }),
    );

    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("1 / 2");
  });

  it("navigates via Alt+PageDown while a Review text field has focus", () => {
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

    const textField = screen.getByTestId("bulk-review-text");
    textField.focus();
    expect(textField).toHaveFocus();

    fireEvent(
      window,
      new KeyboardEvent("keydown", { key: "PageDown", altKey: true }),
    );

    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("2 / 2");
  });

  it("does not wrap item selection at the first or last item", () => {
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

    // At first item: Alt+PageUp keeps the first item selected.
    fireEvent(
      window,
      new KeyboardEvent("keydown", { key: "PageUp", altKey: true }),
    );
    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("1 / 2");

    // Move to the last item; Alt+PageDown keeps the last item selected.
    fireEvent.click(screen.getByTestId("bulk-review-next"));
    fireEvent(
      window,
      new KeyboardEvent("keydown", { key: "PageDown", altKey: true }),
    );
    expect(screen.getByTestId("bulk-review-position")).toHaveTextContent("2 / 2");
  });

  it("prevents the default browser behavior for Alt+PageDown and Alt+PageUp", () => {
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

    const downEvent = new KeyboardEvent("keydown", {
      key: "PageDown",
      altKey: true,
      cancelable: true,
    });
    fireEvent(window, downEvent);
    expect(downEvent.defaultPrevented).toBe(true);

    const upEvent = new KeyboardEvent("keydown", {
      key: "PageUp",
      altKey: true,
      cancelable: true,
    });
    fireEvent(window, upEvent);
    expect(upEvent.defaultPrevented).toBe(true);
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
      expect.objectContaining({ target: "source", expectedRevision: 0 }),
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
      expect.objectContaining({ target: "source", expectedRevision: 0 }),
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
      expect.objectContaining({ target: "source", expectedRevision: 0 }),
    );
  });

  it("keeps focused fields enabled while autosaving", async () => {
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
    answerInput.focus();
    fireEvent.change(answerInput, { target: { value: "focused answer" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });
    expect(answerInput).not.toBeDisabled();
    expect(answerInput).toHaveFocus();

    await act(async () => {
      resolveSave(undefined);
    });
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

  it("adds a tag to one draft and reuses it on another draft via the recent chip", async () => {
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

    // Add "calculus" to item-1.
    const firstTagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(firstTagInput, { target: { value: "calculus" } });
    fireEvent.keyDown(firstTagInput, { key: "Enter", code: "Enter" });

    // Switch to item-2; the recent chip should appear and add the tag on click.
    fireEvent.click(screen.getByTestId("bulk-review-next"));
    const chip = screen.getByTestId("bulk-review-recent-tag-calculus");
    fireEvent.click(chip);

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
        "item-2",
        expect.objectContaining({ tags: ["math", "calculus"] }),
        expect.objectContaining({ target: "source" }),
      );
    });
  });

  it("hides recent tags already selected on the current draft", () => {
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

    // Add "calculus" to item-1; it is now selected, so the chip is hidden.
    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "calculus" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    expect(
      screen.queryByTestId("bulk-review-recent-tag-calculus"),
    ).not.toBeInTheDocument();

    // Switch to item-2 (does not have "calculus"); the chip now appears.
    fireEvent.click(screen.getByTestId("bulk-review-next"));
    expect(
      screen.getByTestId("bulk-review-recent-tag-calculus"),
    ).toBeInTheDocument();
  });

  it("renders at most 5 recent tag chips", () => {
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

    // Add 7 distinct tags to item-1 via comma-separated input.
    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "t1,t2,t3,t4,t5,t6,t7" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    // Switch to item-2 (only has "math"); recent chips should be capped at 5.
    fireEvent.click(screen.getByTestId("bulk-review-next"));
    const chips = within(
      screen.getByTestId("bulk-review-recent-tags"),
    ).getAllByRole("button");
    expect(chips).toHaveLength(5);
  });

  it("does not add tags from recent chips when review fields are disabled", async () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", { order: 0 }),
            makeItem("item-2", { order: 1, status: "deleted" }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    // Add "physics" to item-1.
    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "physics" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    // Switch to the deleted item-2 (fields disabled); chip is disabled.
    fireEvent.click(screen.getByTestId("bulk-review-next"));
    const chip = screen.getByTestId("bulk-review-recent-tag-physics");
    expect(chip).toBeDisabled();

    fireEvent.click(chip);

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    // item-2's tags must remain unchanged.
    expect(handlers.onUpdateDraft).not.toHaveBeenCalledWith(
      "item-2",
      expect.objectContaining({ tags: expect.arrayContaining(["physics"]) }),
      expect.objectContaining({ target: "source" }),
    );
  });

  it("adds each tag from comma-separated multi-tag input to recent tags", () => {
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

    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "alpha,beta" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    fireEvent.click(screen.getByTestId("bulk-review-next"));
    expect(screen.getByTestId("bulk-review-recent-tag-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-recent-tag-beta")).toBeInTheDocument();
  });

  it("adds each tag from semicolon-separated input to recent tags", () => {
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

    const tagInput = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "gamma" } });
    fireEvent.keyDown(tagInput, { key: ";" });
    fireEvent.change(tagInput, { target: { value: "delta" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    fireEvent.click(screen.getByTestId("bulk-review-next"));
    expect(screen.getByTestId("bulk-review-recent-tag-gamma")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-recent-tag-delta")).toBeInTheDocument();
  });
});

function makeVariation(overrides: Partial<BulkItemVariation> = {}): BulkItemVariation {
  return {
    status: "ready",
    generationCount: 1,
    original: {
      text: "What is 2+2?",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "4",
      subject: "math",
    },
    candidate: {
      text: "What is 3+3?",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "6",
    },
    validation: { verdict: "pass" },
    validatedRevision: 1,
    attestation: null,
    queuedAt: null,
    ...overrides,
  };
}

describe("BulkReviewStep source/candidate autosave identity", () => {
  const handlers = {
    onRefresh: vi.fn(),
    onUpdateDraft: vi.fn(),
    onGenerate: vi.fn(),
    onRevalidate: vi.fn(),
    onAttest: vi.fn(),
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

  it("keeps source and candidate edits in separate buffers and save targets", async () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              contentRevision: 1,
              variation: makeVariation(),
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    // The candidate is the editing target by default; source values untouched.
    expect(screen.getByTestId("bulk-review-text")).toHaveValue("What is 3+3?");
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("6");
    expect(screen.getByTestId("bulk-review-subject")).toBeDisabled();

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
        "item-1",
        expect.objectContaining({ correctAnswer: "66" }),
        expect.objectContaining({
          target: "candidate",
          expectedRevision: 1,
        }),
      );
    });

    // Switching to the source shows the source draft, not the candidate buffer.
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    expect(screen.getByTestId("bulk-review-text")).toHaveValue("What is 2+2?");
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("4");
    expect(screen.getByTestId("bulk-review-subject")).not.toBeDisabled();

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "44" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
        "item-1",
        expect.objectContaining({ correctAnswer: "44" }),
        expect.objectContaining({ target: "source", expectedRevision: 1 }),
      );
    });

    // The candidate buffer kept its own edits.
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    expect(screen.getByTestId("bulk-review-text")).toHaveValue("What is 3+3?");
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("66");
  });

  it("preserves shared draft tags in the candidate buffer and candidate saves", async () => {
    render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              contentRevision: 1,
              draft: {
                text: "What is 2+2?",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: ["math", "algebra"],
                subject: "math",
              },
              variation: makeVariation(),
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    // The candidate editor starts from the shared draft tags (the candidate
    // payload carries none).
    expect(screen.getByTestId("bulk-review-tags-tag-math")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-tags-tag-algebra")).toBeInTheDocument();

    // A semantic candidate edit keeps the shared tags untouched.
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
        "item-1",
        expect.objectContaining({
          correctAnswer: "66",
          tags: ["math", "algebra"],
        }),
        expect.objectContaining({ target: "candidate", expectedRevision: 1 }),
      );
    });
  });

  it("replaces the candidate buffer on regeneration and ignores the old write", async () => {
    let resolveSave: (value: unknown) => void = () => undefined;
    handlers.onUpdateDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );

    const { rerender } = render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              contentRevision: 1,
              variation: makeVariation(),
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-text"), {
      target: { value: "Edited candidate" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });

    // A new generation replaces the buffer, dropping the dead candidate's edits.
    rerender(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              contentRevision: 2,
              variation: makeVariation({
                generationCount: 2,
                candidate: {
                  text: "What is 5+5?",
                  problemType: "short-answer",
                  graphDsl: "",
                  correctAnswer: "10",
                },
              }),
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-text")).toHaveValue("What is 5+5?");

    // The old in-flight write cannot clobber the regenerated candidate.
    await act(async () => {
      resolveSave(undefined);
      vi.advanceTimersByTime(10000);
    });

    expect(screen.getByTestId("bulk-review-text")).toHaveValue("What is 5+5?");
    expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
  });

  it("ignores a stale snapshot older than the buffer and applies a newer one", async () => {
    handlers.onUpdateDraft.mockResolvedValue(undefined);

    const { rerender } = render(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              draft: {
                text: "Saved text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
              contentRevision: 1,
              updatedAt: "2026-07-03T00:00:02Z",
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    fireEvent.change(screen.getByTestId("bulk-review-text"), {
      target: { value: "Local newer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(1);
    });

    // A stale save/poll response must not roll the buffer back.
    rerender(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              draft: {
                text: "Old server text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
              contentRevision: 0,
              updatedAt: "2026-07-03T00:00:01Z",
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );
    expect(screen.getByTestId("bulk-review-text")).toHaveValue("Local newer");

    // A genuinely newer snapshot still updates the clean buffer.
    rerender(
      <BulkReviewStep
        batch={makeBatch({
          items: [
            makeItem("item-1", {
              order: 0,
              draft: {
                text: "Newer server text",
                problemType: "short-answer",
                graphDsl: "",
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
              contentRevision: 2,
              updatedAt: "2026-07-03T00:00:03Z",
            }),
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-text")).toHaveValue(
        "Newer server text",
      );
    });
  });
});

describe("BulkReviewStep variant generate", () => {
  const handlers = {
    onRefresh: vi.fn(),
    onUpdateDraft: vi.fn(),
    onGenerate: vi.fn(),
    onRevalidate: vi.fn(),
    onAttest: vi.fn(),
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

  function renderReview(items: BulkItem[], overrides: Partial<BulkBatch> = {}) {
    return render(
      <BulkReviewStep
        batch={makeBatch({
          ingestionMode: "data-and-wording",
          items,
          ...overrides,
        })}
        isLoading={false}
        {...handlers}
      />,
    );
  }

  it("confirms the reviewed source with Generate exactly once, waiting for the autosave", async () => {
    handlers.onUpdateDraft.mockResolvedValue({ contentRevision: 3 });
    renderReview([makeItem("item-1", { contentRevision: 2 })]);

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    // Unsaved state is explained before the click.
    expect(screen.getByTestId("bulk-review-generate-hint")).toHaveTextContent(
      "once its save settles",
    );
    fireEvent.click(screen.getByTestId("bulk-review-generate"));

    // Generate waits for the reviewed save: no stale generate, no debounce race.
    expect(handlers.onGenerate).not.toHaveBeenCalled();
    expect(screen.getByTestId("bulk-review-generate-hint")).toHaveTextContent(
      "Generating...",
    );

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(handlers.onGenerate).toHaveBeenCalledTimes(1);
    });
    expect(handlers.onGenerate).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({
        text: "What is 2+2?",
        problemType: "short-answer",
        correctAnswer: "66",
      }),
      3,
    );
  });

  it("sends Generate with the current revision when the source is already saved", () => {
    renderReview([makeItem("item-1", { contentRevision: 2 })]);

    fireEvent.click(screen.getByTestId("bulk-review-generate"));

    expect(handlers.onUpdateDraft).not.toHaveBeenCalled();
    expect(handlers.onGenerate).toHaveBeenCalledTimes(1);
    expect(handlers.onGenerate).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({ correctAnswer: "4" }),
      2,
    );
  });

  it("prevents a stale Generate when the source save fails and explains the disabled state", async () => {
    handlers.onUpdateDraft.mockRejectedValue(new Error("save exploded"));
    renderReview([makeItem("item-1")]);

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    fireEvent.click(screen.getByTestId("bulk-review-generate"));
    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    expect(handlers.onGenerate).not.toHaveBeenCalled();
    expect(screen.getByTestId("bulk-review-generate")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-generate-hint")).toHaveTextContent(
      "Draft save failed",
    );
  });

  it("explains why Generate is disabled when the source is unprepared", () => {
    renderReview([makeItem("item-1")]);

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "" },
    });

    expect(screen.getByTestId("bulk-review-generate")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-generate-hint")).toHaveTextContent(
      "confirmed answer",
    );
  });

  it("keeps other items editable while a variant generates and restores server state without restarting", async () => {
    const itemA = makeItem("item-a", {
      order: 0,
      variation: makeVariation({ status: "generating" }),
    });
    const itemB = makeItem("item-b", { order: 1 });
    const { rerender } = renderReview([itemA, itemB]);

    expect(screen.getByTestId("bulk-review-variation-status")).toHaveTextContent(
      "generating",
    );
    // Variant work keeps the batch being polled.
    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(handlers.onRefresh).toHaveBeenCalledWith("batch-1");

    // Item B stays fully editable while A processes in the background.
    fireEvent.click(screen.getByTestId("bulk-review-item-item-b"));
    expect(screen.getByTestId("bulk-review-answer")).toBeEnabled();

    // Reloading server-backed state never restarts generation.
    rerender(
      <BulkReviewStep
        batch={makeBatch({
          ingestionMode: "data-and-wording",
          items: [
            makeItem("item-a", {
              order: 0,
              variation: makeVariation({ status: "validating" }),
            }),
            itemB,
          ],
        })}
        isLoading={false}
        {...handlers}
      />,
    );
    expect(
      screen.getByTestId("bulk-review-item-variation-item-a"),
    ).toHaveTextContent("validating");
    expect(handlers.onGenerate).not.toHaveBeenCalled();
  });

  it("renders structured content-failure evidence with no accept or override control", () => {
    renderReview([
      makeItem("item-1", {
        variation: makeVariation({
          status: "failed",
          candidate: {
            text: "What is 3+3?",
            problemType: "single-choice",
            graphDsl: "",
            correctAnswer: "6",
          },
          validation: {
            verdict: "fail",
            failures: [
              { kind: "content", evidence: "category-conflict: type changed" },
            ],
            reports: [
              {
                validatorModel: { provider: "fake-vlm", model: "helper-1" },
                originalSolvedAnswer: "4",
                variantSolvedAnswer: "7",
                checks: {
                  graphConsistency: {
                    category: "graphConsistency",
                    evidence: "inconsistent graph",
                  },
                },
                answerComparisonOriginal: {
                  result: "agree",
                  evidence: "4 == 4",
                },
                answerComparisonVariant: {
                  result: "mismatch",
                  evidence: "7 != 6",
                },
              },
            ],
          },
        }),
      }),
    ]);

    expect(screen.getByTestId("bulk-review-evidence")).toBeInTheDocument();
    // Type mismatch between source and candidate is concrete.
    expect(screen.getByTestId("bulk-review-evidence-types")).toHaveTextContent(
      "short-answer",
    );
    expect(screen.getByTestId("bulk-review-evidence-types")).toHaveTextContent(
      "single-choice",
    );
    // Expected (source + candidate) and solved answers.
    expect(screen.getByTestId("bulk-review-evidence-answers")).toHaveTextContent(
      "Expected answer (source): 4",
    );
    expect(screen.getByTestId("bulk-review-evidence-answers")).toHaveTextContent(
      "Expected answer (candidate): 6",
    );
    expect(screen.getByTestId("bulk-review-evidence-solved")).toHaveTextContent(
      "Solved (variant): 7",
    );
    // Helper judgements (both comparison paths) and check categories/reasons.
    expect(
      screen.getByTestId("bulk-review-evidence-helper-original"),
    ).toHaveTextContent("agree");
    expect(
      screen.getByTestId("bulk-review-evidence-helper-variant"),
    ).toHaveTextContent("mismatch");
    expect(screen.getByTestId("bulk-review-evidence-checks")).toHaveTextContent(
      "graphConsistency",
    );
    expect(screen.getByTestId("bulk-review-evidence-checks")).toHaveTextContent(
      "inconsistent graph",
    );
    expect(
      screen.getByTestId("bulk-review-evidence-failure-kind"),
    ).toHaveTextContent("Content failure");
    // No accept/override/fallback path exists.
    expect(
      screen.queryByRole("button", { name: /accept|override|fallback/i }),
    ).toBeNull();
  });

  it("distinguishes model execution failures from content failures", () => {
    renderReview([
      makeItem("item-1", {
        variation: makeVariation({
          status: "failed",
          candidate: null,
          validation: {
            verdict: "fail",
            failures: [{ kind: "provider", evidence: "helper failed: timeout" }],
            reports: [],
          },
        }),
      }),
    ]);

    expect(
      screen.getByTestId("bulk-review-evidence-failure-kind"),
    ).toHaveTextContent("Model execution failure");
    expect(screen.getByTestId("bulk-review-evidence")).toHaveTextContent(
      "helper failed: timeout",
    );
  });

  it("re-runs the flow only via manual Generate Again", () => {
    renderReview([
      makeItem("item-1", {
        contentRevision: 4,
        variation: makeVariation({ status: "failed" }),
      }),
    ]);

    const button = screen.getByTestId("bulk-review-generate");
    expect(button).toHaveTextContent("Generate Again");
    fireEvent.click(button);

    expect(handlers.onGenerate).toHaveBeenCalledTimes(1);
    expect(handlers.onGenerate).toHaveBeenCalledWith(
      "item-1",
      expect.anything(),
      4,
    );
  });

  it("keeps polling for background extraction (original-mode compatibility)", async () => {
    renderReview([makeItem("item-1", { status: "queued" })], {
      ingestionMode: "original",
    });

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(handlers.onRefresh).toHaveBeenCalledWith("batch-1");
  });

  it("releases the source Generate only from the source save, not a candidate save", async () => {
    const resolvers: Record<string, (value: unknown) => void> = {};
    handlers.onUpdateDraft.mockImplementation(
      (_itemId, _changes, options: { target: string }) =>
        new Promise((resolve) => {
          resolvers[options.target] = resolve;
        }),
    );
    renderReview([
      makeItem("item-1", { contentRevision: 1, variation: makeVariation() }),
    ]);

    // The candidate buffer is the default target; dirty it first.
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "44" },
    });
    // Generate queues behind the pending SOURCE save.
    fireEvent.click(screen.getByTestId("bulk-review-generate"));
    expect(handlers.onGenerate).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });

    // The candidate save settles first: it must not release the Generate.
    await act(async () => {
      resolvers.candidate({ contentRevision: 99 });
    });
    expect(handlers.onGenerate).not.toHaveBeenCalled();

    // The source save settles with the reviewed source: Generate fires once.
    await act(async () => {
      resolvers.source({ contentRevision: 5 });
    });
    await waitFor(() => {
      expect(handlers.onGenerate).toHaveBeenCalledTimes(1);
    });
    expect(handlers.onGenerate).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({ correctAnswer: "44" }),
      5,
    );
  });

  it("does not cancel the source Generate when a candidate save fails", async () => {
    const resolvers: Record<string, (value: unknown) => void> = {};
    const rejecters: Record<string, (error: unknown) => void> = {};
    handlers.onUpdateDraft.mockImplementation(
      (_itemId, _changes, options: { target: string }) =>
        new Promise((resolve, reject) => {
          resolvers[options.target] = resolve;
          rejecters[options.target] = reject;
        }),
    );
    renderReview([
      makeItem("item-1", { contentRevision: 1, variation: makeVariation() }),
    ]);

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "44" },
    });
    fireEvent.click(screen.getByTestId("bulk-review-generate"));

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledTimes(2);
    });

    // A failed candidate save must not drop the queued source Generate.
    await act(async () => {
      rejecters.candidate(new Error("candidate save exploded"));
    });
    expect(handlers.onGenerate).not.toHaveBeenCalled();

    await act(async () => {
      resolvers.source({ contentRevision: 5 });
    });
    await waitFor(() => {
      expect(handlers.onGenerate).toHaveBeenCalledTimes(1);
    });
    expect(handlers.onGenerate).toHaveBeenCalledWith(
      "item-1",
      expect.objectContaining({ correctAnswer: "44" }),
      5,
    );
  });

  it("classifies persisted failure kinds as content or model-execution failures", () => {
    renderReview([
      makeItem("item-1", {
        variation: makeVariation({
          status: "failed",
          candidate: null,
          validation: {
            verdict: "fail",
            failures: [
              { kind: "content", evidence: "category-conflict: type changed" },
              {
                kind: "invalid-candidate",
                evidence: "candidate failed schema validation",
              },
              { kind: "provider", evidence: "helper failed: timeout" },
              { kind: "invalid-response", evidence: "unparseable completion" },
              {
                kind: "vlm-invalid-response",
                evidence: "generator openai/x failed: Variant VLM response failed schema validation",
              },
              { kind: "vlm-timeout", evidence: "generator timed out" },
            ],
            reports: [],
          },
        }),
      }),
    ]);

    const kinds = screen
      .getAllByTestId("bulk-review-evidence-failure-kind")
      .map((element) => element.textContent);
    expect(kinds).toEqual([
      "Content failure",
      "Content failure",
      "Model execution failure",
      "Model execution failure",
      "Model execution failure",
      "Model execution failure",
    ]);
    // The persisted invalid-candidate evidence stays inspectable.
    expect(screen.getByTestId("bulk-review-evidence")).toHaveTextContent(
      "candidate failed schema validation",
    );
  });

  it("renders both answer-comparison judgements and expected-vs-solved pairs for every report", () => {
    renderReview([
      makeItem("item-1", {
        variation: makeVariation({
          status: "failed",
          validation: {
            verdict: "fail",
            failures: [{ kind: "content", evidence: "answer mismatch" }],
            reports: [
              {
                validatorModel: { provider: "fake-vlm", model: "helper-1" },
                originalSolvedAnswer: "4",
                variantSolvedAnswer: "7",
                answerComparisonOriginal: {
                  result: "agree",
                  evidence: "4 == 4",
                },
                answerComparisonVariant: {
                  result: "mismatch",
                  evidence: "7 != 6",
                },
                checks: {},
              },
              {
                validatorModel: { provider: "fake-vlm", model: "helper-2" },
                originalSolvedAnswer: "2+2",
                variantSolvedAnswer: "6.5",
                answerComparisonOriginal: { result: "agree", evidence: "same" },
                answerComparisonVariant: {
                  result: "mismatch",
                  evidence: "6.5 != 6",
                },
                checks: {},
              },
            ],
          },
        }),
      }),
    ]);

    // Both expected answers are inspectable.
    expect(screen.getByTestId("bulk-review-evidence-answers")).toHaveTextContent(
      "Expected answer (source): 4",
    );
    expect(screen.getByTestId("bulk-review-evidence-answers")).toHaveTextContent(
      "Expected answer (candidate): 6",
    );
    // Every report shows its solved pair and both comparison judgements.
    const solved = screen.getAllByTestId("bulk-review-evidence-solved");
    expect(solved[0]).toHaveTextContent("Solved (original): 4");
    expect(solved[0]).toHaveTextContent("Solved (variant): 7");
    expect(solved[1]).toHaveTextContent("Solved (original): 2+2");
    expect(solved[1]).toHaveTextContent("Solved (variant): 6.5");
    const originalComparisons = screen.getAllByTestId(
      "bulk-review-evidence-helper-original",
    );
    expect(originalComparisons[0]).toHaveTextContent("agree");
    expect(originalComparisons[0]).toHaveTextContent("4 == 4");
    expect(originalComparisons[1]).toHaveTextContent("same");
    const variantComparisons = screen.getAllByTestId(
      "bulk-review-evidence-helper-variant",
    );
    expect(variantComparisons[0]).toHaveTextContent("7 != 6");
    expect(variantComparisons[1]).toHaveTextContent("6.5 != 6");
  });
});

describe("BulkReviewStep variant pass gating and revalidation", () => {
  const handlers = {
    onRefresh: vi.fn(),
    onUpdateDraft: vi.fn(),
    onGenerate: vi.fn(),
    onRevalidate: vi.fn(),
    onAttest: vi.fn(),
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

  // Current PASS baseline: contentRevision and validatedRevision must match.
  function passedItem(
    overrides: Partial<BulkItem> = {},
    variationOverrides: Partial<BulkItemVariation> = {},
  ): BulkItem {
    return makeItem("item-1", {
      contentRevision: 2,
      variation: makeVariation({ validatedRevision: 2, ...variationOverrides }),
      ...overrides,
    });
  }

  function variantReviewUi(item: BulkItem) {
    return (
      <BulkReviewStep
        batch={makeBatch({ ingestionMode: "data-and-wording", items: [item] })}
        isLoading={false}
        {...handlers}
      />
    );
  }

  it("continues when every item holds a current PASS candidate", () => {
    render(variantReviewUi(passedItem()));

    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    fireEvent.click(screen.getByTestId("bulk-review-continue"));
    expect(handlers.onContinue).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      "not generated",
      { status: "not-requested", original: null, candidate: null, generationCount: 0, validation: null, validatedRevision: null } as Partial<BulkItemVariation>,
      "Variant not generated",
    ],
    [
      "queued",
      { status: "queued" } as Partial<BulkItemVariation>,
      "Variant generation is queued",
    ],
    [
      "generating",
      { status: "generating" } as Partial<BulkItemVariation>,
      "Variant generation is running",
    ],
    [
      "validating",
      { status: "validating" } as Partial<BulkItemVariation>,
      "Variant validation is running",
    ],
    [
      "needs-validation",
      { status: "needs-validation", validation: null, validatedRevision: null } as Partial<BulkItemVariation>,
      "Variant needs validation",
    ],
    [
      "failed",
      { status: "failed", candidate: null, validation: null, validatedRevision: null } as Partial<BulkItemVariation>,
      "Variant failed — generate again",
    ],
    [
      "ready with a fail verdict",
      { validation: { verdict: "fail" }, validatedRevision: null } as Partial<BulkItemVariation>,
      "Variant validation failed",
    ],
    [
      "ready with a stale validatedRevision",
      { validatedRevision: 1 } as Partial<BulkItemVariation>,
      "Variant needs revalidation",
    ],
  ])(
    "blocks Continue while the variant is %s",
    (_label, variationOverrides, reason) => {
      const item = passedItem({}, variationOverrides);
      // The exact gate reason comes from the shared helper.
      expect(variantPassGateReason(item)).toBe(reason);

      render(variantReviewUi(item));
      expect(screen.getByTestId("bulk-review-continue")).toBeDisabled();
      expect(screen.getByTestId("bulk-review-item-item-1")).toHaveAttribute(
        "data-action-required",
        "true",
      );
    },
  );

  it("lets a submit-failed item with a current PASS candidate continue", () => {
    render(variantReviewUi(passedItem({ status: "submit-failed" })));

    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
  });

  it("treats submitted items as done for Continue gating", () => {
    const submitted = makeItem("item-done", {
      status: "submitted",
      contentRevision: 2,
      variation: makeVariation({ validatedRevision: 2 }),
    });
    const remaining = makeItem("item-2", {
      order: 1,
      contentRevision: 2,
      variation: makeVariation({ validatedRevision: 2 }),
    });
    render(
      <BulkReviewStep
        batch={makeBatch({
          ingestionMode: "data-and-wording",
          items: [submitted, remaining],
        })}
        isLoading={false}
        {...handlers}
      />,
    );

    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    expect(screen.getByTestId("bulk-review-item-item-done")).toHaveAttribute(
      "data-action-required",
      "false",
    );
  });

  it("blocks Continue after a semantic candidate edit and re-approves via validator-only revalidation", async () => {
    const { rerender } = render(variantReviewUi(passedItem()));
    expect(screen.queryByTestId("bulk-review-revalidate")).not.toBeInTheDocument();

    // Semantic candidate edit lands on the server: needs-validation, approval
    // cleared, contentRevision bumped by the backend edit contract.
    rerender(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          { status: "needs-validation", validation: null, validatedRevision: null },
        ),
      ),
    );

    expect(screen.getByTestId("bulk-review-continue")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-revalidate")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("bulk-review-revalidate"));
    await waitFor(() => {
      expect(handlers.onRevalidate).toHaveBeenCalledTimes(1);
    });
    expect(handlers.onRevalidate).toHaveBeenCalledWith("item-1", 3);
    // Validator-only: revalidation never triggers generation.
    expect(handlers.onGenerate).not.toHaveBeenCalled();

    // The validator-only run re-approves the same generation (generationCount
    // stays 1 in the fixture; validatedRevision returns to the revision).
    rerender(
      variantReviewUi(
        passedItem({ contentRevision: 3 }, { validatedRevision: 3 }),
      ),
    );

    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    expect(screen.queryByTestId("bulk-review-revalidate")).not.toBeInTheDocument();
  });

  it("surfaces a failed revalidation and keeps the action available", async () => {
    handlers.onRevalidate.mockRejectedValue(new Error("validator offline"));
    render(
      variantReviewUi(
        passedItem(
          {},
          { status: "needs-validation", validation: null, validatedRevision: null },
        ),
      ),
    );

    fireEvent.click(screen.getByTestId("bulk-review-revalidate"));
    await waitFor(() => {
      expect(
        screen.getByTestId("bulk-review-revalidate-error"),
      ).toHaveTextContent("validator offline");
    });
    expect(screen.getByTestId("bulk-review-revalidate")).toBeEnabled();
  });

  it("keeps a passed item submittable across a tags-only candidate save", async () => {
    handlers.onUpdateDraft.mockResolvedValue({ contentRevision: 2 });
    render(variantReviewUi(passedItem()));

    const tagField = screen.getByTestId("bulk-review-tags-field");
    fireEvent.change(tagField, { target: { value: "calculus" } });
    fireEvent.keyDown(tagField, { key: "Enter", code: "Enter" });

    // Tags-only save: no invalidation, no revision bump, still current PASS.
    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    await waitFor(() => {
      expect(handlers.onUpdateDraft).toHaveBeenCalledWith(
        "item-1",
        expect.objectContaining({ tags: ["math", "calculus"] }),
        expect.objectContaining({ target: "candidate", expectedRevision: 2 }),
      );
    });
    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    expect(screen.queryByTestId("bulk-review-revalidate")).not.toBeInTheDocument();
  });

  it("warns that source edits invalidate the candidate and gates Continue after the reset lands", () => {
    const { rerender } = render(variantReviewUi(passedItem()));

    // Candidate is the default target: no warning.
    expect(
      screen.queryByTestId("bulk-review-source-invalidation-warning"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    expect(
      screen.getByTestId("bulk-review-source-invalidation-warning"),
    ).toHaveTextContent("invalidates the current variant");

    // Source semantic edit lands: candidate discarded, back to not-requested.
    rerender(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          {
            status: "not-requested",
            original: null,
            candidate: null,
            generationCount: 0,
            validation: null,
            validatedRevision: null,
          },
        ),
      ),
    );

    expect(screen.getByTestId("bulk-review-continue")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-variation-status")).toHaveTextContent(
      "Variant: not generated",
    );
    expect(
      screen.queryByTestId("bulk-review-source-invalidation-warning"),
    ).not.toBeInTheDocument();
  });

  it("admits an attested candidate through the pass gate and continues", () => {
    // Attested READY: validatedRevision is None but the attestation covers
    // the current revision — same admission right as a validator-covered PASS.
    const attested = passedItem(
      {},
      {
        validatedRevision: null,
        attestation: { revision: 2, at: "2026-10-01T00:00:00Z" },
      },
    );
    expect(variantPassGateReason(attested)).toBeNull();

    render(variantReviewUi(attested));
    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    expect(screen.getByTestId("bulk-review-attestation")).toHaveTextContent(
      /user-attested at revision 2/i,
    );
  });

  it("blocks a ready candidate whose attestation covers an older revision", () => {
    const staleAttestation = passedItem(
      { contentRevision: 3 },
      {
        validatedRevision: null,
        attestation: { revision: 2, at: "2026-10-01T00:00:00Z" },
      },
    );
    expect(variantPassGateReason(staleAttestation)).toBe(
      "Variant needs revalidation",
    );
  });

  it("shows the stale banner and both exits after a semantic candidate edit", async () => {
    const { rerender } = render(variantReviewUi(passedItem()));
    expect(
      screen.queryByTestId("bulk-review-stale-validation"),
    ).not.toBeInTheDocument();

    // Semantic edit landed: needs-validation, stale PASS report kept visible.
    rerender(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          {
            status: "needs-validation",
            validatedRevision: null,
            attestation: null,
          },
        ),
      ),
    );

    expect(screen.getByTestId("bulk-review-stale-validation")).toHaveTextContent(
      "previous version",
    );
    expect(screen.getByTestId("bulk-review-attest")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("bulk-review-attest"));
    await waitFor(() => {
      expect(handlers.onAttest).toHaveBeenCalledTimes(1);
    });
    expect(handlers.onAttest).toHaveBeenCalledWith("item-1", 3);
    // Attest is never a validator call.
    expect(handlers.onRevalidate).not.toHaveBeenCalled();

    // Attestation landed: READY again with the attestation banner.
    rerender(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          {
            validatedRevision: null,
            attestation: { revision: 3, at: "2026-10-01T00:00:00Z" },
          },
        ),
      ),
    );
    expect(screen.getByTestId("bulk-review-continue")).toBeEnabled();
    expect(screen.queryByTestId("bulk-review-attest")).not.toBeInTheDocument();
  });

  it("offers only Revalidate when no stored pass report exists", () => {
    render(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          {
            status: "needs-validation",
            validation: null,
            validatedRevision: null,
            attestation: null,
          },
        ),
      ),
    );

    expect(
      screen.queryByTestId("bulk-review-stale-validation"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("bulk-review-attest")).not.toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-revalidate")).toBeInTheDocument();
  });

  it("surfaces a failed attestation and keeps the action available", async () => {
    handlers.onAttest.mockRejectedValue(new Error("attest conflict"));
    render(
      variantReviewUi(
        passedItem(
          { contentRevision: 3 },
          {
            status: "needs-validation",
            validatedRevision: null,
            attestation: null,
          },
        ),
      ),
    );

    fireEvent.click(screen.getByTestId("bulk-review-attest"));
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-attest-error")).toHaveTextContent(
        "attest conflict",
      );
    });
    expect(screen.getByTestId("bulk-review-attest")).toBeEnabled();
  });
});
