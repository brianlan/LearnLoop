import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  commitImage,
  createBatch,
  deleteImage,
  detectImageBoxes,
  getActiveBatch,
  getBatch,
  saveImageBoxes,
  uploadBatchImages,
} from "./bulkIngestion";
import type { BatchResponse } from "@/types/bulkIngestion";

function makeBatchResponse(id = "batch-1"): BatchResponse {
  return {
    batch: {
      id,
      userId: "user-1",
      status: "active",
      images: [],
      items: [],
      createdAt: "2026-07-03T00:00:00Z",
      updatedAt: "2026-07-03T00:00:00Z",
      expiresAt: "2026-07-04T00:00:00Z",
    },
  };
}

describe("bulk ingestion API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("createBatch", () => {
    it("creates a batch with a POST to /ingestion-batches", async () => {
      const response = makeBatchResponse("batch-new");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await createBatch();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch).toHaveBeenCalledWith("/api/v1/ingestion-batches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: undefined,
      });
      expect(result).toEqual(response);
    });
  });

  describe("getActiveBatch", () => {
    it("fetches the active batch", async () => {
      const response = makeBatchResponse();
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await getActiveBatch();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch).toHaveBeenCalledWith("/api/v1/ingestion-batches/active", {
        credentials: "include",
      });
      expect(result).toEqual(response);
    });
  });

  describe("getBatch", () => {
    it("fetches a batch by id", async () => {
      const response = makeBatchResponse("batch-2");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await getBatch("batch-2");

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/ingestion-batches/batch-2",
        { credentials: "include" },
      );
      expect(result).toEqual(response);
    });
  });

  describe("uploadBatchImages", () => {
    it("uploads images as multipart/form-data", async () => {
      const response = makeBatchResponse("batch-1");
      let capturedBody: FormData | undefined;
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const files = [
        new File(["image1"], "a.png", { type: "image/png" }),
        new File(["image2"], "b.jpg", { type: "image/jpeg" }),
      ];

      await uploadBatchImages("batch-1", files);

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const callArgs = mockFetch.mock.calls[0] as [string, RequestInit];
      expect(callArgs[0]).toBe("/api/v1/ingestion-batches/batch-1/images");
      expect(callArgs[1].method).toBe("POST");
      expect(callArgs[1].credentials).toBe("include");
      capturedBody = callArgs[1].body as FormData;
      expect(capturedBody.getAll("images")).toHaveLength(2);
    });
  });

  describe("detectImageBoxes", () => {
    it("posts to the detect endpoint", async () => {
      const response = makeBatchResponse("batch-1");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await detectImageBoxes("batch-1", "img-1");

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/ingestion-batches/batch-1/images/img-1/detect",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: undefined,
        },
      );
      expect(result).toEqual(response);
    });
  });

  describe("saveImageBoxes", () => {
    it("patches boxes and subject", async () => {
      const response = makeBatchResponse("batch-1");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const boxes = [{ boxId: "box-1", x: 0, y: 0, width: 10, height: 10 }];
      const result = await saveImageBoxes("batch-1", "img-1", boxes, "math");

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/ingestion-batches/batch-1/images/img-1",
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ boxes, subject: "math" }),
        },
      );
      expect(result).toEqual(response);
    });
  });

  describe("commitImage", () => {
    it("posts to the commit endpoint", async () => {
      const response = makeBatchResponse("batch-1");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await commitImage("batch-1", "img-1");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/ingestion-batches/batch-1/images/img-1/commit",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: undefined,
        },
      );
      expect(result).toEqual(response);
    });
  });

  describe("deleteImage", () => {
    it("deletes the image", async () => {
      const response = makeBatchResponse("batch-1");
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(response),
      });
      vi.stubGlobal("fetch", mockFetch);

      const result = await deleteImage("batch-1", "img-1");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/ingestion-batches/batch-1/images/img-1",
        {
          method: "DELETE",
          credentials: "include",
        },
      );
      expect(result).toEqual(response);
    });
  });
});
