import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { BulkIngestionWizard } from "./BulkIngestionWizard";
import { ApiError } from "@/api/client";
import type { BatchResponse, BulkBatch } from "@/types/bulkIngestion";

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

describe("BulkIngestionWizard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    const notFound = new ApiError("No active batch found", 404, "NOT_FOUND");
    mocks.getActiveBatch.mockRejectedValue(notFound);
    mocks.getBatch.mockRejectedValue(notFound);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("shows loading state then upload step when no active batch exists", async () => {
    mocks.getActiveBatch.mockRejectedValue(
      new ApiError("No active batch found", 404, "NOT_FOUND"),
    );

    render(<BulkIngestionWizard />);
    expect(screen.getByTestId("bulk-wizard-loading")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-upload-step")).toBeInTheDocument();
    });
    expect(screen.getByTestId("bulk-wizard-create-batch")).toBeInTheDocument();
  });

  it("shows error state when loading the batch fails", async () => {
    mocks.getActiveBatch.mockRejectedValue(new Error("Network error"));

    render(<BulkIngestionWizard />);

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("shows expired empty state for an expired batch", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({ status: "expired" }),
    });

    render(<BulkIngestionWizard />);

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-expired")).toBeInTheDocument();
    });
    expect(screen.getByText(/has expired/i)).toBeInTheDocument();
  });

  it("shows detect step when active batch has uncommitted images", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "uploaded",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-detect-step")).toBeInTheDocument();
    });
  });

  it("creates a batch and switches to upload input", async () => {
    mocks.getActiveBatch.mockRejectedValue(
      new ApiError("No active batch found", 404, "NOT_FOUND"),
    );
    mocks.createBatch.mockResolvedValue({ batch: makeBatch({ id: "batch-new" }) });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-create-batch")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-wizard-create-batch"));

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-upload-input")).toBeInTheDocument();
    });
    expect(mocks.createBatch).toHaveBeenCalledTimes(1);
  });

  it("advances to review step when batch has committed images and queued items", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "queued",
            order: 0,
            draft: {},
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.uploadBatchImages.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
    });
  });

  it("starts extraction once when entering the review step and not again on poll refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "queued",
            order: 0,
            draft: {},
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.startBatchExtraction.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "queued",
            order: 0,
            draft: {},
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.getBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "extracting",
            order: 0,
            draft: {},
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
    });
    expect(mocks.startBatchExtraction).toHaveBeenCalledTimes(1);
    expect(mocks.startBatchExtraction).toHaveBeenCalledWith("batch-1");

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });

    await waitFor(() => {
      expect(mocks.getBatch).toHaveBeenCalledWith("batch-1");
    });
    expect(mocks.startBatchExtraction).toHaveBeenCalledTimes(1);

    vi.useRealTimers();
  });

  it("does not persist batch state in localStorage", async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    mocks.getActiveBatch.mockRejectedValue(
      new ApiError("No active batch found", 404, "NOT_FOUND"),
    );

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-upload-step")).toBeInTheDocument();
    });

    expect(getItemSpy).not.toHaveBeenCalledWith(expect.stringContaining("batch"));
    expect(setItemSpy).not.toHaveBeenCalledWith(expect.stringContaining("batch"), expect.anything());
  });

  it("uploads images and advances to the detect step", async () => {
    mocks.getActiveBatch.mockRejectedValue(
      new ApiError("No active batch found", 404, "NOT_FOUND"),
    );
    mocks.createBatch.mockResolvedValue({ batch: makeBatch({ id: "batch-new" }) });
    mocks.uploadBatchImages.mockResolvedValue({
      batch: makeBatch({
        id: "batch-new",
        images: [
          {
            imageId: "img-1",
            status: "uploaded",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k", width: 100, height: 100 },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-create-batch")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-wizard-create-batch"));
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-upload-input")).toBeInTheDocument();
    });

    const file = new File(["image"], "test.png", { type: "image/png" });
    fireEvent.change(screen.getByTestId("bulk-wizard-upload-input"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-detect-step")).toBeInTheDocument();
    });
    expect(mocks.uploadBatchImages).toHaveBeenCalledWith("batch-new", [file]);
  });

  it("runs detection for an uploaded image", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "uploaded",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k", width: 100, height: 100 },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.detectImageBoxes.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "ready",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k", width: 100, height: 100 },
            boxes: [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-detect-image-img-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-detect-run-img-1"));

    await waitFor(() => {
      expect(mocks.detectImageBoxes).toHaveBeenCalledWith("batch-1", "img-1");
    });
  });

  it("commits an image after reviewing boxes", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "ready",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k", width: 100, height: 100 },
            boxes: [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.commitImage.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k", width: 100, height: 100 },
            boxes: [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "queued",
            order: 0,
            draft: {},
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-detect-commit-img-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-detect-commit-img-1"));

    await waitFor(() => {
      expect(mocks.commitImage).toHaveBeenCalledWith("batch-1", "img-1");
    });
  });

  it("surfaces upload validation failures", async () => {
    mocks.getActiveBatch.mockRejectedValue(
      new ApiError("No active batch found", 404, "NOT_FOUND"),
    );
    mocks.createBatch.mockResolvedValue({ batch: makeBatch({ id: "batch-new" }) });
    mocks.uploadBatchImages.mockRejectedValue(new Error("Unsupported image type"));

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-create-batch")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-wizard-create-batch"));
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-upload-input")).toBeInTheDocument();
    });

    const file = new File(["image"], "test.bmp", { type: "image/bmp" });
    fireEvent.change(screen.getByTestId("bulk-wizard-upload-input"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("bulk-upload-error")).toHaveTextContent(
        "Unsupported image type",
      );
    });
    expect(mocks.uploadBatchImages).toHaveBeenCalledWith("batch-new", [file]);
  });

  it("renders detection failure state with retry and manual box entry", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "detect-failed",
            order: 0,
            sourceImage: {
              bucket: "b",
              objectKey: "k",
              width: 100,
              height: 100,
              mediaUrl: "http://example.com/img.png",
            },
            boxes: [],
            detection: { failureMessage: "Vision model error" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-detect-failure-img-1")).toHaveTextContent(
        "Vision model error",
      );
    });
    expect(screen.getByTestId("box-editor")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-detect-run-img-1")).toBeInTheDocument();
  });

  it("renders a ready image with no boxes for manual box entry", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "ready",
            order: 0,
            sourceImage: {
              bucket: "b",
              objectKey: "k",
              width: 100,
              height: 100,
              mediaUrl: "http://example.com/img.png",
            },
            boxes: [],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("box-editor")).toBeInTheDocument();
    });
    expect(screen.getByTestId("bulk-detect-status-img-1")).toHaveTextContent(
      "Review boxes",
    );
  });

  it("persists subject override through the save API", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "ready",
            order: 0,
            sourceImage: {
              bucket: "b",
              objectKey: "k",
              width: 100,
              height: 100,
              mediaUrl: "http://example.com/img.png",
            },
            subject: "math",
            boxes: [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.saveImageBoxes.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "ready",
            order: 0,
            sourceImage: {
              bucket: "b",
              objectKey: "k",
              width: 100,
              height: 100,
              mediaUrl: "http://example.com/img.png",
            },
            subject: "english",
            boxes: [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
            detection: { model: "model" },
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-detect-subject-img-1")).toHaveValue("math");
    });

    fireEvent.change(screen.getByTestId("bulk-detect-subject-img-1"), {
      target: { value: "english" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("bulk-detect-save-img-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-detect-save-img-1"));

    await waitFor(() => {
      expect(mocks.saveImageBoxes).toHaveBeenCalledWith(
        "batch-1",
        "img-1",
        [{ boxId: "box-1", x: 10, y: 10, width: 20, height: 20 }],
        "english",
      );
    });
  });

  it("advances to submit step when all items are ready", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "ready",
            order: 0,
            draft: {
              text: "What is 2+2?",
              problemType: "short-answer",
              correctAnswer: "4",
            },
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-submit-step")).toBeInTheDocument();
    });
    expect(screen.getByTestId("bulk-submit-button")).not.toBeDisabled();
  });

  it("submits the batch and refreshes to show results", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "ready",
            order: 0,
            draft: {
              text: "What is 2+2?",
              problemType: "short-answer",
              correctAnswer: "4",
            },
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.submitBatch.mockResolvedValue({
      submitSummary: {
        batchId: "batch-1",
        status: "completed",
        items: [
          {
            itemId: "item-1",
            status: "submitted",
            submittedProblemId: "problem-1",
            failureCode: null,
            failureMessage: null,
          },
        ],
      },
    });
    mocks.getBatch.mockResolvedValue({
      batch: makeBatch({
        status: "completed",
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "submitted",
            order: 0,
            draft: {
              text: "What is 2+2?",
              problemType: "short-answer",
              correctAnswer: "4",
            },
            extraction: {},
            retryCount: 0,
            submit: { submittedProblemId: "problem-1" },
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-submit-step")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-submit-button"));

    await waitFor(() => {
      expect(mocks.submitBatch).toHaveBeenCalledWith("batch-1");
    });
    await waitFor(() => {
      expect(mocks.getBatch).toHaveBeenCalledWith("batch-1");
    });
  });

  it("stays on submit step when submission has partial failures", async () => {
    mocks.getActiveBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "ready",
            order: 0,
            draft: {
              text: "What is 2+2?",
              problemType: "short-answer",
              correctAnswer: "4",
            },
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
          {
            itemId: "item-2",
            imageId: "img-1",
            batchId: "batch-1",
            status: "ready",
            order: 1,
            draft: {
              text: "What is 3+3?",
              problemType: "short-answer",
              correctAnswer: "6",
            },
            extraction: {},
            retryCount: 0,
            submit: {},
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });
    mocks.submitBatch.mockResolvedValue({
      submitSummary: {
        batchId: "batch-1",
        status: "active",
        items: [
          {
            itemId: "item-1",
            status: "submitted",
            submittedProblemId: "problem-1",
            failureCode: null,
            failureMessage: null,
          },
          {
            itemId: "item-2",
            status: "submit-failed",
            submittedProblemId: null,
            failureCode: "VALIDATION_ERROR",
            failureMessage: "Invalid draft",
          },
        ],
      },
    });
    mocks.getBatch.mockResolvedValue({
      batch: makeBatch({
        images: [
          {
            imageId: "img-1",
            status: "committed",
            order: 0,
            sourceImage: { bucket: "b", objectKey: "k" },
            boxes: [],
            detection: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
        items: [
          {
            itemId: "item-1",
            imageId: "img-1",
            batchId: "batch-1",
            status: "submitted",
            order: 0,
            draft: {
              text: "What is 2+2?",
              problemType: "short-answer",
              correctAnswer: "4",
            },
            extraction: {},
            retryCount: 0,
            submit: { submittedProblemId: "problem-1" },
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
          {
            itemId: "item-2",
            imageId: "img-1",
            batchId: "batch-1",
            status: "submit-failed",
            order: 1,
            draft: {
              text: "What is 3+3?",
              problemType: "short-answer",
              correctAnswer: "6",
            },
            extraction: {},
            retryCount: 0,
            submit: { failureMessage: "Invalid draft" },
            origin: {},
            createdAt: "2026-07-03T00:00:00Z",
            updatedAt: "2026-07-03T00:00:00Z",
          },
        ],
      }),
    });

    render(<BulkIngestionWizard />);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-wizard-submit-step")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("bulk-submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("bulk-submit-summary")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("bulk-submit-retry-item-2"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("bulk-submit-delete-item-2"),
    ).toBeInTheDocument();
  });
});
