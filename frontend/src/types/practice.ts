export interface PracticeProblem {
  id: string;
  text: string;
  type: string;
  imageUrl?: string;
  graphDsl?: string;
}

export interface PracticeAttemptResult {
  gradingStatus: string;
  gradingMethod: string;
  feedback?: string;
}

export interface PracticeAttemptDetail {
  submittedAnswer: string;
  gradingStatus: string;
  gradingMethod: string;
  createdAt: string;
  feedback?: string;
}

export interface PracticeHistorySummary {
  totalAttempts: number;
  correctCount: number;
  wrongCount: number;
  lastPracticedAt?: string;
  lastResult?: string;
}

export interface PracticeHistoryItem {
  problemId: string;
  problemText: string;
  problemType: string;
  imageUrl?: string;
  summary: PracticeHistorySummary;
  attempts: PracticeAttemptDetail[];
}

export type PracticeNextStatus = "ok" | "no_eligible" | "no_problems";

export interface PracticeNextResponse {
  status: PracticeNextStatus;
  problem?: PracticeProblem;
}

export interface PracticeHistoryResponse {
  items: PracticeHistoryItem[];
}
