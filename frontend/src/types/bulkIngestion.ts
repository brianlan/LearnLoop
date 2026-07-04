export type BatchState = "active" | "completed" | "expired" | "deleted";

export type ImageState =
  | "uploaded"
  | "detecting"
  | "detect-failed"
  | "ready"
  | "committed"
  | "deleted";

export type ItemState =
  | "queued"
  | "extracting"
  | "ready"
  | "failed"
  | "submit-failed"
  | "deleted"
  | "submitted";

export interface BulkSourceImage {
  bucket: string;
  objectKey: string;
  contentType?: string | null;
  sizeBytes?: number | null;
  sha256?: string | null;
  width?: number | null;
  height?: number | null;
  uploadedAt?: string | null;
  mediaUrl?: string | null;
}

export interface BulkImageBox {
  boxId: string;
  x: number;
  y: number;
  width: number;
  height: number;
  page?: number;
  [key: string]: unknown;
}

export interface BulkDetection {
  model?: string | null;
  rawProviderResponse?: unknown;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export interface BulkImage {
  imageId: string;
  status: ImageState;
  order: number;
  sourceImage: BulkSourceImage;
  subject?: string | null;
  boxes: BulkImageBox[];
  detection: BulkDetection;
  committedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface BulkDraft {
  text?: string | null;
  problemType?: string | null;
  graphDsl?: string | null;
  correctAnswer?: string | null;
  tags?: string[];
  subject?: string | null;
}

export interface BulkExtraction {
  rawText?: string | null;
  rawProblemType?: string | null;
  rawGraphDsl?: string | null;
  rawCorrectAnswer?: string | null;
  rawTags?: string[];
  failureCode?: string | null;
  failureMessage?: string | null;
  [key: string]: unknown;
}

export interface BulkItemSubmit {
  status?: string;
  submittedProblemId?: string | null;
  submittedAt?: string | null;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export interface BulkCrop {
  bucket?: string;
  objectKey?: string;
  contentType?: string | null;
  mediaUrl?: string | null;
  [key: string]: unknown;
}

export interface BulkItem {
  itemId: string;
  imageId: string;
  batchId: string;
  status: ItemState;
  order: number;
  draft: BulkDraft;
  extraction: BulkExtraction;
  retryCount: number;
  submit: BulkItemSubmit;
  origin: Record<string, unknown>;
  crop?: BulkCrop | null;
  leaseUntil?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface BulkBatch {
  id: string;
  userId: string;
  status: BatchState;
  images: BulkImage[];
  items: BulkItem[];
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
}

export interface BatchResponse {
  batch: BulkBatch;
}

export interface BulkSubmitItemResult {
  itemId: string;
  status: string;
  submittedProblemId?: string | null;
  failureCode?: string | null;
  failureMessage?: string | null;
}

export interface BulkSubmitSummary {
  batchId: string;
  status: string;
  items: BulkSubmitItemResult[];
}

export interface SubmitSummaryResponse {
  submitSummary: BulkSubmitSummary;
}

export type BulkWizardStep =
  | "upload"
  | "detect"
  | "review"
  | "submit"
  | "complete";
