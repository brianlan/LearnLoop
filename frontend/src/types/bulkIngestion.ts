export type BatchState = "active" | "completed" | "expired" | "deleted";

// Batch-level ingestion mode, immutable after creation; mirrors the backend
// IngestionMode enum. Missing on legacy batches means original.
export type IngestionMode = "original" | "data-only" | "data-and-wording";

// Per-item variant lifecycle: not-requested → queued → generating →
// validating → ready | failed; ready → needs-validation; failed → queued
// only via manual Generate Again.
export type VariationStatus =
  | "not-requested"
  | "queued"
  | "generating"
  | "validating"
  | "ready"
  | "failed"
  | "needs-validation";

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

// The confirmed source draft sent with Generate; mirrors the backend
// VariationOriginalPayload.
export interface VariationOriginalPayload {
  text: string;
  problemType: string;
  correctAnswer: string;
  graphDsl?: string | null;
  subject?: string | null;
}

export interface GenerateVariationRequest {
  expectedRevision: number;
  original: VariationOriginalPayload;
}

export interface EditVariationCandidateRequest {
  expectedRevision: number;
  text?: string;
  problemType?: string;
  graphDsl?: string | null;
  correctAnswer?: string;
  tags?: string[];
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
  // Per-item semantic revision: bumped by semantic source/candidate changes
  // and new generation; tags and worker progress never bump it.
  contentRevision: number;
  variation: BulkItemVariation | null;
  createdAt: string;
  updatedAt: string;
}

// Variant content stored/edited on the candidate. Tags are shared metadata
// and live in the item draft (`BulkDraft.tags`); the backend candidate
// deliberately never carries them.
export interface BulkVariationCandidate {
  text?: string | null;
  problemType?: string | null;
  graphDsl?: string | null;
  correctAnswer?: string | null;
}

// Validator evidence record; the backend presents it verbatim (alias-shaped
// dict with model identities, checks, evidence and answer comparisons) and
// it never contains provider secrets. Typed loosely until the evidence UI
// slice owns its exact shape.
export interface BulkVariationValidation {
  verdict?: string | null;
  [key: string]: unknown;
}

// Explicit user attestation that keeps a stale PASS (#648): recorded when a
// teacher restores READY from needs-validation instead of revalidating.
export interface BulkVariationAttestation {
  revision: number;
  at: string;
}

// Client-facing per-item variation view: status, progress and evidence only;
// the backend never exposes claimToken/leaseUntil fencing state.
export interface BulkItemVariation {
  status: VariationStatus;
  generationCount: number;
  original: BulkVariationOriginal | null;
  candidate: BulkVariationCandidate | null;
  validation: BulkVariationValidation | null;
  validatedRevision: number | null;
  attestation: BulkVariationAttestation | null;
  queuedAt: string | null;
}

export interface BulkVariationOriginal {
  text: string;
  problemType: string;
  graphDsl?: string | null;
  correctAnswer: string;
  subject?: string | null;
}

export interface BulkBatch {
  id: string;
  userId: string;
  status: BatchState;
  ingestionMode: IngestionMode;
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
