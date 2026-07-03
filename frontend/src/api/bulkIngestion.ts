import { api } from "./client";
import type { BatchResponse, BulkImageBox } from "@/types/bulkIngestion";

export async function createBatch(): Promise<BatchResponse> {
  return api.post<BatchResponse>("/ingestion-batches", undefined);
}

export async function getActiveBatch(): Promise<BatchResponse> {
  return api.get<BatchResponse>("/ingestion-batches/active");
}

export async function getBatch(batchId: string): Promise<BatchResponse> {
  return api.get<BatchResponse>(`/ingestion-batches/${batchId}`);
}

export async function uploadBatchImages(
  batchId: string,
  images: File[],
): Promise<BatchResponse> {
  const formData = new FormData();
  for (const image of images) {
    formData.append("images", image);
  }
  return api.postFormData<BatchResponse>(
    `/ingestion-batches/${batchId}/images`,
    formData,
  );
}

export async function detectImageBoxes(
  batchId: string,
  imageId: string,
): Promise<BatchResponse> {
  return api.post<BatchResponse>(
    `/ingestion-batches/${batchId}/images/${imageId}/detect`,
    undefined,
  );
}

export async function saveImageBoxes(
  batchId: string,
  imageId: string,
  boxes: BulkImageBox[],
  subject?: string | null,
): Promise<BatchResponse> {
  return api.patch<BatchResponse>(
    `/ingestion-batches/${batchId}/images/${imageId}`,
    { boxes, subject },
  );
}

export async function commitImage(
  batchId: string,
  imageId: string,
): Promise<BatchResponse> {
  return api.post<BatchResponse>(
    `/ingestion-batches/${batchId}/images/${imageId}/commit`,
    undefined,
  );
}

export async function deleteImage(
  batchId: string,
  imageId: string,
): Promise<BatchResponse> {
  return api.delete<BatchResponse>(
    `/ingestion-batches/${batchId}/images/${imageId}`,
  );
}
