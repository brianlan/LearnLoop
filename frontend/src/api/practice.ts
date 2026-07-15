import { api } from "./client";
import type {
  PracticeAttemptResult,
  PracticeHistoryResponse,
  PracticeNextResponse,
  PracticeStatsResponse,
} from "@/types/practice";

export const PRACTICE_HISTORY_KEY = ["practice-history"] as const;
export const PRACTICE_STATS_KEY = ["practice-stats"] as const;

export async function getPracticeHistory(): Promise<PracticeHistoryResponse> {
  return api.get<PracticeHistoryResponse>("/practice/history");
}

export async function getPracticeStats(): Promise<PracticeStatsResponse> {
  return api.get<PracticeStatsResponse>("/practice/stats");
}

export async function startPractice(): Promise<PracticeNextResponse> {
  return api.post<PracticeNextResponse>("/practice/next", {});
}

export async function submitPracticeAttempt(
  problemId: string,
  submittedAnswer: string,
): Promise<PracticeAttemptResult> {
  return api.post<PracticeAttemptResult>("/practice/attempts", {
    problemId,
    submittedAnswer,
  });
}

export async function nextPracticeProblem(): Promise<PracticeNextResponse> {
  return api.post<PracticeNextResponse>("/practice/next", {});
}
