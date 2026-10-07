import type {
  BulkDraft,
  BulkItem,
  BulkVariationValidation,
  VariationStatus,
} from "@/types/bulkIngestion";

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

export function variationStatusLabel(status: VariationStatus | string): string {
  switch (status) {
    case "not-requested":
      return "Variant: not generated";
    case "queued":
      return "Variant: queued";
    case "generating":
      return "Variant: generating...";
    case "validating":
      return "Variant: validating...";
    case "ready":
      return "Variant: ready";
    case "failed":
      return "Variant: failed";
    case "needs-validation":
      return "Variant: needs validation";
    default:
      return `Variant: ${status}`;
  }
}

// Variant work still running in the background worker.
export function isVariantBusy(status: VariationStatus): boolean {
  return (
    status === "queued" || status === "generating" || status === "validating"
  );
}

export function hasActiveVariantWork(item: BulkItem): boolean {
  return Boolean(item.variation && isVariantBusy(item.variation.status));
}

// Current PASS, mirroring the backend variant-submit admission check
// (variation ready + verdict pass + [validatedRevision OR attestation]
// matching the current contentRevision): only this state may enter final
// review or submission. Returns null when the item is passed, otherwise a
// short reason why Continue/submit is blocked.
export function variantPassGateReason(item: BulkItem): string | null {
  const variation = item.variation;
  if (!variation || !variation.original) return "Variant not generated";
  switch (variation.status) {
    case "ready":
      if (
        variation.validation?.verdict !== "pass" &&
        variation.attestation?.revision !== item.contentRevision
      ) {
        // #658: an attested FAIL verdict is admitted — the attest fence
        // guaranteed a check-/answer-kind-only failure set on the current
        // revision.
        return "Variant validation failed";
      }
      if (
        variation.validatedRevision !== item.contentRevision &&
        variation.attestation?.revision !== item.contentRevision
      ) {
        return "Variant needs revalidation";
      }
      return null;
    case "needs-validation":
      return "Variant needs validation";
    case "failed":
      return "Variant failed — generate again";
    case "queued":
      return "Variant generation is queued";
    case "generating":
      return "Variant generation is running";
    case "validating":
      return "Variant validation is running";
    default:
      return `Variant status: ${variation.status}`;
  }
}

// Failure evidence as stored by the #613 validation contract:
// {verdict, failures: [{kind, evidence}], reports: [ValidatorReport]}.
// The backend presents these dicts verbatim.
export interface EvidenceCheck {
  category: string;
  evidence: string;
}

export interface EvidenceReport {
  validatorModel?: { provider?: string; model?: string };
  originalSolvedAnswer?: string | null;
  variantSolvedAnswer?: string | null;
  checks?: Record<string, EvidenceCheck>;
  answerComparisonOriginal?: { result?: string; evidence?: string };
  answerComparisonVariant?: { result?: string; evidence?: string };
}

export interface EvidenceFailure {
  kind?: string;
  evidence?: string;
}

export interface EvidenceView {
  verdict?: string | null;
  failures?: EvidenceFailure[];
  reports?: EvidenceReport[];
}

export function evidenceView(
  validation: BulkVariationValidation | null | undefined,
): EvidenceView | null {
  return (validation as EvidenceView | null | undefined) ?? null;
}

export function evidenceChecks(report: EvidenceReport): EvidenceCheck[] {
  return Object.values(report.checks ?? {});
}

// Backend failure kinds (#658): "check" is a validator judgment failure
// and "answer" an answer-comparison failure — both the teacher may attest
// away (#665); "content" a generated-content mismatch — a verified fact,
// never overridable. "invalid-candidate" (checkpointed candidate failed
// schema validation) is content/schema; "provider"/"invalid-response" and
// every "vlm-*" code persisted by the worker (vlm-invalid-response,
// vlm-timeout, vlm-network-error, vlm-provider-error,
// vlm-provider-rejected) come from the model side. Unknown kinds are shown
// verbatim, never misclassified.
export function failureKindLabel(kind: string | undefined): string {
  if (kind === "check") {
    return "Validator judgment failure";
  }
  if (kind === "answer") {
    return "Answer correctness failure";
  }
  if (kind === "content" || kind === "invalid-candidate") {
    return "Content failure";
  }
  if (kind === "provider" || kind === "invalid-response" || kind?.startsWith("vlm-")) {
    return "Model execution failure";
  }
  return kind ? `${kind} failure` : "Failure";
}

// Whether the teacher may attest this item into READY, mirroring the
// backend is_attestable predicate (#658): the stale-PASS path
// (needs-validation only) or a FAIL whose failures are all check- or
// answer-kind (from needs-validation or failed, #665). Kind-less legacy
// entries and worker raw kinds fail closed.
export function canAttestVariant(item: BulkItem): boolean {
  const variation = item.variation;
  if (!variation) return false;
  const verdict = variation.validation?.verdict;
  if (verdict === "pass") {
    return variation.status === "needs-validation";
  }
  if (verdict !== "fail") return false;
  if (variation.status !== "needs-validation" && variation.status !== "failed") {
    return false;
  }
  const validation = variation.validation as EvidenceView | null | undefined;
  const failures = validation?.failures ?? [];
  return (
    failures.length > 0 &&
    failures.every(
      (failure) => failure.kind === "check" || failure.kind === "answer",
    )
  );
}
