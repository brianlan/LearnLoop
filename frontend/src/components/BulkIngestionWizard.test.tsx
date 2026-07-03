import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { BulkIngestionWizard } from "./BulkIngestionWizard";
import { ApiError } from "@/api/client";
import type { BatchResponse, BulkBatch } from "@/types/bulkIngestion";

const mocks = vi.hoisted(() => ({
  getActiveBatch: vi.fn<() => Promise<BatchResponse>>(),
  getBatch: vi.fn<() => Promise<BatchResponse>>(),
  createBatch: vi.fn<() => Promise<BatchResponse>>(),
  uploadBatchImages: vi.fn<() => Promise<BatchResponse>>(),
}));

vi.mock("@/api/bulkIngestion", () => ({
  getActiveBatch: mocks.getActiveBatch,
  getBatch: mocks.getBatch,
  createBatch: mocks.createBatch,
  uploadBatchImages: mocks.uploadBatchImages,
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

  it("uploads images and advances the step", async () => {
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
});
