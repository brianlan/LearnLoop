import type { BulkDraft, BulkItem } from "@/types/bulkIngestion";

const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 4000;

export function retryDelayMs(failureCount: number): number {
  return Math.min(BASE_RETRY_MS * 2 ** failureCount, MAX_RETRY_MS);
}

export function defaultDraft(item: BulkItem): BulkDraft {
  return {
    text: item.draft.text ?? "",
    problemType: item.draft.problemType ?? "short-answer",
    graphDsl: item.draft.graphDsl ?? "",
    correctAnswer: item.draft.correctAnswer ?? "",
    tags: item.draft.tags ?? [],
    subject: item.draft.subject ?? "math",
  };
}

export function serializeDraft(draft: BulkDraft): string {
  return JSON.stringify(draft);
}

export function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "extracting":
      return "Extracting...";
    case "ready":
      return "Ready";
    case "failed":
      return "Extraction failed";
    case "submit-failed":
      return "Submit failed";
    case "deleted":
      return "Deleted";
    case "submitted":
      return "Submitted";
    default:
      return status;
  }
}

export function getRequiredFieldGaps(draft: BulkDraft) {
  return {
    text: !draft.text || draft.text.trim() === "",
    problemType: !draft.problemType,
    correctAnswer: !draft.correctAnswer || draft.correctAnswer.trim() === "",
  };
}
