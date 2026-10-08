import { api } from "./client";
import type { ProblemVariantSession } from "@/types/problemVariants";

export type { ProblemVariantMode } from "@/types/problemVariants";
import type { ProblemVariantMode } from "@/types/problemVariants";
import type { BulkItemVariation } from "@/types/bulkIngestion";

export interface ProblemVariantSessionResponse {
  session: ProblemVariantSession | null;
}

export async function createProblemVariant(
  problemId: string,
  mode: ProblemVariantMode,
): Promise<ProblemVariantSessionResponse> {
  return api.post<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants`,
    { mode },
  );
}

export async function getActiveProblemVariantSession(
  problemId: string,
): Promise<ProblemVariantSessionResponse> {
  return api.get<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/active`,
  );
}

export async function editProblemVariantCandidate(
  problemId: string,
  sessionId: string,
  request: {
    expectedRevision: number;
    text?: string;
    problemType?: string;
    graphDsl?: string | null;
    correctAnswer?: string;
    tags?: string[];
  },
): Promise<ProblemVariantSessionResponse> {
  return api.patch<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/${sessionId}/candidate`,
    request,
  );
}

export async function generateProblemVariant(
  problemId: string,
  sessionId: string,
  expectedRevision: number,
): Promise<ProblemVariantSessionResponse> {
  return api.post<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/${sessionId}/generate`,
    { expectedRevision },
  );
}

export async function revalidateProblemVariant(
  problemId: string,
  sessionId: string,
  expectedRevision: number,
): Promise<ProblemVariantSessionResponse> {
  return api.post<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/${sessionId}/revalidate`,
    { expectedRevision },
  );
}

export async function attestProblemVariant(
  problemId: string,
  sessionId: string,
  expectedRevision: number,
): Promise<ProblemVariantSessionResponse> {
  return api.post<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/${sessionId}/attest`,
    { expectedRevision },
  );
}

export async function submitProblemVariant(
  problemId: string,
  sessionId: string,
): Promise<{ problemId: string; alreadySubmitted: boolean }> {
  return api.post(
    `/problems/${problemId}/variants/${sessionId}/submit`,
    undefined,
  );
}

export async function discardProblemVariant(
  problemId: string,
  sessionId: string,
): Promise<ProblemVariantSessionResponse> {
  return api.post<ProblemVariantSessionResponse>(
    `/problems/${problemId}/variants/${sessionId}/discard`,
    undefined,
  );
}

// The serialized session's variation carries the same shape as a batch
// item's variation; only generationCount/validatedRevision typing differs
// by nullability across transports.
export type { BulkItemVariation };
