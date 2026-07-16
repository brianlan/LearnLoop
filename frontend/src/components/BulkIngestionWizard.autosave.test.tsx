import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { BulkIngestionWizard } from "./BulkIngestionWizard";
import type { BatchResponse, BulkBatch, BulkImage, BulkItem } from "@/types/bulkIngestion";

const mocks = vi.hoisted(() => ({
  getActiveBatch: vi.fn<() => Promise<BatchResponse>>(),
  getBatch: vi.fn<() => Promise<BatchResponse>>(),
  createBatch: vi.fn<() => Promise<BatchResponse>>(),
  uploadBatchImages: vi.fn<() => Promise<BatchResponse>>(),
  detectImageBoxes: vi.fn<() => Promise<BatchResponse>>(),
  saveImageBoxes: vi.fn<() => Promise<BatchResponse>>(),
  commitImage: vi.fn<() => Promise<BatchResponse>>(),
  deleteImage: vi.fn<() => Promise<BatchResponse>>(),
  startBatchExtraction: vi.fn<() => Promise<BatchResponse>>(),
  submitBatch: vi.fn<() => Promise<{ submitSummary: { batchId: string; status: string; items: unknown[] } }>>(),
  retryItem: vi.fn<() => Promise<BatchResponse>>(),
  deleteBatchItem: vi.fn<() => Promise<BatchResponse>>(),
  undoDeleteBatchItem: vi.fn<() => Promise<BatchResponse>>(),
  updateItemDraft: vi.fn<() => Promise<BatchResponse>>(),
}));

vi.mock("@/api/bulkIngestion", () => ({
  getActiveBatch: mocks.getActiveBatch,
  getBatch: mocks.getBatch,
  createBatch: mocks.createBatch,
  uploadBatchImages: mocks.uploadBatchImages,
  detectImageBoxes: mocks.detectImageBoxes,
  saveImageBoxes: mocks.saveImageBoxes,
  commitImage: mocks.commitImage,
  deleteImage: mocks.deleteImage,
  startBatchExtraction: mocks.startBatchExtraction,
  submitBatch: mocks.submitBatch,
  retryItem: mocks.retryItem,
  deleteBatchItem: mocks.deleteBatchItem,
  undoDeleteBatchItem: mocks.undoDeleteBatchItem,
  updateItemDraft: mocks.updateItemDraft,
}));

function makeImage(overrides: Partial<BulkImage> = {}): BulkImage {
  return {
    imageId: "img-1",
    status: "committed",
    order: 0,
    sourceImage: { bucket: "b", objectKey: "k" },
    boxes: [],
    detection: {},
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    ...overrides,
  };
}

function makeItem(overrides: Partial<BulkItem> = {}): BulkItem {
  return {
    itemId: "item-1",
    imageId: "img-1",
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
    crop: { mediaUrl: "http://example.com/crop-item-1.png" },
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
    images: [makeImage()],
    items: [makeItem()],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
    ...overrides,
  };
}

const reviewBatchResponse: BatchResponse = { batch: makeBatch() };

async function renderAtReviewStep() {
  mocks.getActiveBatch.mockRejectedValue(
    new Error("No active batch found"),
  );
  mocks.getBatch.mockResolvedValue(reviewBatchResponse);

  render(<BulkIngestionWizard initialBatchId="batch-1" />);

  await waitFor(() => {
    expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
  });
}

function getContinueButton() {
  return screen.getByTestId("bulk-review-continue") as HTMLButtonElement;
}

describe("BulkIngestionWizard integrated autosave characterization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gates Continue while a draft save is pending and re-enables it after success", async () => {
    let resolveSave: (value: BatchResponse) => void = () => undefined;
    mocks.updateItemDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );

    await renderAtReviewStep();
    const continueButton = getContinueButton();
    expect(continueButton).not.toBeDisabled();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "42" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);
    expect(continueButton).toBeDisabled();
    expect(answerInput).not.toBeDisabled();
    expect(screen.queryByTestId("bulk-wizard-error")).not.toBeInTheDocument();

    await act(async () => {
      resolveSave({ batch: makeBatch({ items: [makeItem({ draft: { ...makeItem().draft, correctAnswer: "42" } })] }) });
    });

    await waitFor(() => {
      expect(continueButton).not.toBeDisabled();
    });
  });

  it("swallows a rejected updateItemDraft and surfaces a wizard-level error instead of a review retry", async () => {
    mocks.updateItemDraft.mockRejectedValue(new Error("network error"));

    await renderAtReviewStep();
    const continueButton = getContinueButton();
    expect(continueButton).not.toBeDisabled();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "99" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-error")).toHaveTextContent("network error");
    });

    // A wizard-level error replaces the entire review UI. The review step never sees the
    // rejection, so there is no item-level save-failed indicator, no retry, and no Continue.
    expect(screen.queryByTestId("bulk-wizard-review-step")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bulk-review-continue")).not.toBeInTheDocument();
    expect(screen.queryByText(/save failed/i)).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);
  });

  it("keeps the wizard-level error view after a later successful save", async () => {
    mocks.updateItemDraft
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce({
        batch: makeBatch({
          items: [makeItem({ draft: { ...makeItem().draft, correctAnswer: "7" } })],
        }),
      });

    await renderAtReviewStep();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "7" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-error")).toHaveTextContent("network error");
    });

    // The wizard does not clear its error banner after a subsequent successful save,
    // so the review UI remains replaced even though the underlying API call succeeded.
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-error")).toHaveTextContent("network error");
    });
    expect(screen.queryByTestId("bulk-wizard-review-step")).not.toBeInTheDocument();
  });
});
