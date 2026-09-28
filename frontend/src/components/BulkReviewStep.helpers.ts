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

// Source draft and variant candidate are separate editing targets with
// separate autosave buffers and save endpoints.
export type EditTarget = "source" | "candidate";

// Server-side identity of the content a buffer is based on. `revision` is the
// item contentRevision sent as expectedRevision; `generation` identifies the
// candidate produced by one generation run.
export interface TargetStamp {
  revision: number;
  generation: number;
  updatedAt: number;
}

export function bufferKey(itemId: string, target: EditTarget): string {
  return `${itemId}::${target}`;
}

export function targetDraft(item: BulkItem, target: EditTarget): BulkDraft {
  if (target === "candidate" && item.variation?.candidate) {
    const candidate = item.variation.candidate;
    return {
      text: candidate.text ?? "",
      problemType: candidate.problemType ?? "short-answer",
      graphDsl: candidate.graphDsl ?? "",
      correctAnswer: candidate.correctAnswer ?? "",
      // Tags are shared metadata living in the item draft; the candidate
      // payload never carries them (seeding from the candidate would send an
      // empty tag list and clear the shared tags on every candidate save).
      tags: item.draft.tags ?? [],
      // Display-only: a candidate has no subject of its own and candidate
      // saves never send this field.
      subject: item.variation.original?.subject ?? item.draft.subject ?? "math",
    };
  }
  return defaultDraft(item);
}

export function targetStamp(item: BulkItem, target: EditTarget): TargetStamp {
  return {
    revision: item.contentRevision,
    generation:
      target === "candidate" ? item.variation?.generationCount ?? 0 : 0,
    updatedAt: Date.parse(item.updatedAt) || 0,
  };
}

// An older snapshot (stale save or poll response) never replaces what this
// buffer has already seen.
// ponytail: two writes sharing one millisecond fall through to content
// comparison below; exact ordering needs a server sequence number.
export function isStaleStamp(
  incoming: TargetStamp,
  current: TargetStamp | undefined,
): boolean {
  if (!current) return false;
  return (
    incoming.updatedAt < current.updatedAt ||
    incoming.revision < current.revision ||
    incoming.generation < current.generation
  );
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
