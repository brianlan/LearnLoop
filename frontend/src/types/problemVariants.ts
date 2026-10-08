// Problem-variant session types (issue #685). The session's `variation`
// subtree mirrors the batch item variation exactly (same lifecycle
// statuses, evidence and serialized view), so the review panel and helpers
// are reused unchanged.

import type {
  BulkItemVariation,
  VariationStatus,
} from "@/types/bulkIngestion";

export type ProblemVariantMode = "data-only" | "transfer-variant";

export interface ProblemVariantSession {
  sessionId: string;
  problemId: string;
  mode: ProblemVariantMode;
  contentRevision: number;
  tags: string[];
  variation: BulkItemVariation | null;
  submit: {
    submittedProblemId: string;
    success: boolean;
  } | null;
  discardedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export type { VariationStatus };
