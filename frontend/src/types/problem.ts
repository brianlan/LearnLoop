import type { CorrectAnswer } from "./exam";

export interface ProblemDetail {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  graphDsl?: string;
  imageUrl?: string;
  correctAnswer?: CorrectAnswer;
  isDeleted: boolean;
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
  createdAt: string;
  updatedAt: string;
}

export interface ProblemsResponse {
  items: ProblemListItem[];
  total: number;
  page: number;
  pageSize: number;
}
