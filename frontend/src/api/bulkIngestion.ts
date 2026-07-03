import { api } from "./client";
import type { BatchResponse } from "@/types/bulkIngestion";

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
