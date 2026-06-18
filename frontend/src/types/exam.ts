export type ProblemType =
  | "single-choice"
  | "multi-choice"
  | "fill-in-the-blank"
  | "short-answer";

export type ExamState = "in-progress" | "submitted" | "discarded";

export type GradingStatus =
  | "ungraded"
  | "correct"
  | "incorrect"
  | "pending-review";

export interface CorrectAnswer {
  display: string;
  normalizedText: string;
  normalizedSet: string[];
  format: string;
}

export interface SourceImage {
  bucket: string;
  objectKey: string;
  contentType?: string;
  sizeBytes?: number;
  sha256?: string;
  uploadedAt?: string;
}

export interface ExamProblem {
  text: string;
  problemType: ProblemType;
  graphDsl?: string;
  correctAnswer?: CorrectAnswer;
  sourceImage?: SourceImage;
  imageUrl?: string;
}

export interface ExamAnswer {
  raw?: string;
  savedAt?: string;
}

export interface ExamGrading {
  status: GradingStatus;
  method?: string;
  isCorrect?: boolean;
  score?: number;
  feedback?: string;
  providerModel?: string;
  rawProviderResponse?: Record<string, unknown>;
  gradedAt?: string;
  retryCount: number;
  selfReportedCorrect?: boolean;
}

export interface ExamItem {
  itemId: string;
  order: number;
  problemId: string;
  problem: ExamProblem;
  answer: ExamAnswer;
  grading: ExamGrading;
}

export interface SelectionPolicy {
  cooldownDays: number;
  lastWrongWeight: number;
  failureRateWeight: number;
  recencyWeight: number;
  minProblemAgeDays: number;
}

export interface ExamConfigSnapshot {
  maxProblemCount: number;
  selectionPolicy: SelectionPolicy;
  generatedAt: string;
}

export interface ExamSummary {
  totalProblems: number;
  answeredProblems: number;
  gradedProblems: number;
  pendingProblems: number;
  correctProblems: number;
  failedProblems: number;
  score: number | null;
}

export interface Exam {
  id: string;
  state: ExamState;
  configSnapshot: ExamConfigSnapshot;
  items: ExamItem[];
  summary: ExamSummary;
  createdAt: string;
  startedAt?: string;
  submittedAt?: string;
  discardedAt?: string;
  updatedAt: string;
}

export interface ExamHistoryItem {
  id: string;
  state: ExamState;
  createdAt: string;
  submittedAt?: string;
  discardedAt?: string;
  summary: ExamSummary;
}

export interface CreateExamRequest {
  maxProblemCount: number;
}

export interface CreateExamResponse {
  exam: Exam;
}

export interface ExamResponse {
  exam: Exam;
}

export interface SaveAnswerRequest {
  answer?: string | null;
}

export interface SaveAnswerResponse {
  item: ExamItem;
}

export interface SelfReportRequest {
  isCorrect: boolean;
}

export interface SelfReportResponse {
  item: ExamItem;
  summary: ExamSummary;
}

export interface ExamHistoryResponse {
  items: ExamHistoryItem[];
  page: number;
  pageSize: number;
  total: number;
}
