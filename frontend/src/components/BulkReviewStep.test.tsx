import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
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
