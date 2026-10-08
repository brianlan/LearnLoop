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

export interface VariationValidation {
  verdict: string;
  helperModel?: ModelIdentity | null;
}

// Read-only provenance of an admitted variant problem; mirrors the backend
// ProblemVariationPayload. Source-derived evidence (original snapshot, audit
// image, validation reports) is withheld at the backend serialization seam;
// absent/null means an ordinary problem.
export interface ProblemVariation {
  mode: string;
  acceptedVariant: VariationContent;
  generator: ModelIdentity;
  generationCount: number;
  validation: VariationValidation;
  // Problem-variant provenance link (#685); batch-ingested variants are null.
  sourceProblemId?: string | null;
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
