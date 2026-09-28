import type { CorrectAnswer } from "./exam";

export interface ModelIdentity {
  provider: string;
  model: string;
}

// Immutable content snapshots stored on the admitted variant; mirrors the
// backend VariationContentPayload.
export interface VariationContent {
  text: string;
  problemType: string;
  subject: string;
  graphDsl?: string | null;
  correctAnswer?: CorrectAnswer;
}

export interface VariationOriginal extends VariationContent {
  // Owned audit-image URL (owner-only route); never the normal problem image.
  auditImageUrl?: string | null;
}

export interface VariationValidation {
  verdict: string;
  helperModel?: ModelIdentity | null;
}

// Read-only provenance of an admitted variant problem; mirrors the backend
// ProblemVariationPayload. Absent/null means an ordinary problem.
export interface ProblemVariation {
  mode: string;
  original: VariationOriginal;
  acceptedVariant: VariationContent;
  generator: ModelIdentity;
  generationCount: number;
  validation: VariationValidation;
}

export interface ProblemDetail {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  graphDsl?: string;
  imageUrl?: string;
  correctAnswer?: CorrectAnswer;
  variation?: ProblemVariation | null;
  isDeleted: boolean;
  isDisabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ProblemResponse {
  problem: ProblemDetail;
}

export interface ProblemListItem {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  imageUrl?: string;
  tracking: {
    exposureCount: number;
    correctCount: number;
    failedCount: number;
    lastTestedAt?: string;
    lastAttemptCorrect?: boolean;
  };
  isDeleted: boolean;
  isDisabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PracticeWeight {
  lastWrong: number;
  failure: number;
  recency: number;
  total: number;
}

export interface ProblemsResponse {
  items: ProblemListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AttemptHistoryItem {
  id: string;
  testedAt: string;
  result: string | null;
  source: "practice" | "exam" | "created";
}

export interface AttemptHistoryResponse {
  items: AttemptHistoryItem[];
  total: number;
  hasMore: boolean;
}
