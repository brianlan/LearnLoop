import type React from "react";
import type {
  BulkDraft,
  BulkItemVariation,
} from "@/types/bulkIngestion";
import { TagInput } from "./TagInput";
import { GraphSandbox } from "./GraphSandbox";
import { LatexText } from "./LatexText";
import {
  canAttestVariant,
  evidenceChecks,
  evidenceView,
  failureKindLabel,
  getRequiredFieldGaps,
  statusLabel,
  variationStatusLabel,
} from "./BulkReviewStep.helpers";
import type {
  EditTarget,
  useBulkReviewEditing,
} from "./BulkReviewStep.editing";

const ACTION_REQUIRED_BORDER = "2px solid var(--color-error, #dc2626)";

const PROBLEM_TYPES = [
  { value: "single-choice", label: "Single choice" },
  { value: "multi-choice", label: "Multiple choice" },
  { value: "fill-in-the-blank", label: "Fill in the blank" },
  { value: "short-answer", label: "Short answer" },
];

const SUBJECTS = [
  { value: "math", label: "Math" },
  { value: "english", label: "English" },
];

// Exact per-item action state produced by useBulkReviewEditing's
// actionStateFor; the hook stays in the shell, the panel renders its result.
type BulkReviewEditingApi = ReturnType<typeof useBulkReviewEditing>;
export type VariantReviewActionState = ReturnType<
  BulkReviewEditingApi["actionStateFor"]
>;

// Read-only structured failure evidence for a failed variant run (#613
// report payload). No accept/override/fallback controls exist here.
function VariationFailureEvidence({
  variation,
}: {
  variation: BulkItemVariation;
}) {
  const validation = evidenceView(variation.validation);
  const failures = validation?.failures ?? [];
  const reports = validation?.reports ?? [];
  return (
    <div
      data-testid="bulk-review-evidence"
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "6px",
        padding: "12px",
        marginBottom: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        fontSize: "0.9em",
      }}
    >
      <div style={{ fontWeight: 600 }}>Generation evidence (read-only)</div>
      {variation.status === "ready" && variation.attestation && (
        <div
          data-testid="bulk-review-attestation"
          style={{ color: "var(--color-warning, #b45309)" }}
        >
          User-attested at revision {variation.attestation.revision} — kept by
          the teacher, not covered by a validator run.
          {failures.length > 0 &&
            " Waved failures are listed below; the teacher accepted them as-is."}
        </div>
      )}
      <div data-testid="bulk-review-evidence-types">
        Source type: {variation.original?.problemType ?? "unknown"} · Candidate
        type: {variation.candidate?.problemType ?? "unknown"}
      </div>
      <div data-testid="bulk-review-evidence-answers">
        Expected answer (source): {variation.original?.correctAnswer ?? "—"} ·
        Expected answer (candidate): {variation.candidate?.correctAnswer ?? "—"}
      </div>
      {failures.map((failure, index) => (
        <div key={index} data-testid="bulk-review-evidence-failure">
          <strong data-testid="bulk-review-evidence-failure-kind">
            {failureKindLabel(failure.kind)}
          </strong>
          : {failure.evidence}
        </div>
      ))}
      {reports.map((report, index) => (
        <div key={index} data-testid="bulk-review-evidence-report">
          <div data-testid="bulk-review-evidence-solved">
            Solved (original): {report.originalSolvedAnswer ?? "—"} · Solved
            (variant): {report.variantSolvedAnswer ?? "—"}
          </div>
          <div data-testid="bulk-review-evidence-helper-original">
            Helper {report.validatorModel?.provider ?? "?"} /{" "}
            {report.validatorModel?.model ?? "?"} (original):{" "}
            {report.answerComparisonOriginal?.result ?? "no judgement"}
            {report.answerComparisonOriginal?.evidence
              ? ` — ${report.answerComparisonOriginal.evidence}`
              : ""}
          </div>
          <div data-testid="bulk-review-evidence-helper-variant">
            Helper {report.validatorModel?.provider ?? "?"} /{" "}
            {report.validatorModel?.model ?? "?"} (variant):{" "}
            {report.answerComparisonVariant?.result ?? "no judgement"}
            {report.answerComparisonVariant?.evidence
              ? ` — ${report.answerComparisonVariant.evidence}`
              : ""}
          </div>
          <ul data-testid="bulk-review-evidence-checks" style={{ margin: 0 }}>
            {evidenceChecks(report).map((check) => (
              <li key={check.category}>
                {check.category}: {check.evidence}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * Batch-agnostic single-item variant review surface (#684): status/evidence
 * display, source/candidate editors and the variant action buttons. A move
 * (not a rewrite) of the per-item review JSX from BulkReviewStep; the
 * editing hook, selection and list-derived state stay in the shell.
 */
export interface VariantReviewPanelProps {
  status: string;
  failureMessage?: string | null;
  variation: BulkItemVariation | null;
  draft: BulkDraft;
  requiredFieldGaps: ReturnType<typeof getRequiredFieldGaps>;
  /** Already-resolved image URL for the item preview (crop or source image). */
  imageSource: string;
  target: EditTarget;
  onTargetChange: (target: EditTarget) => void;
  isFieldDisabled: boolean;
  /** Problem-level flow: the stored source cannot be edited (#684). */
  sourceReadOnly?: boolean;
  /** Generate visibility is shell policy (e.g. ingestion mode gating). */
  showGenerate: boolean;
  actionState: VariantReviewActionState;
  onUpdateDraft: (changes: Partial<BulkDraft>) => void;
  onGenerate: () => void;
  onRevalidate: () => void;
  onAttest: () => void;
  reviewTagSuggestions?: string[];
  recentTags?: string[];
  onTagsChange: (nextTags: string[], previousTags: string[]) => void;
  /** Optional action slot (submit, extraction retry/delete, ...). */
  extraActions?: React.ReactNode;
}

export function VariantReviewPanel({
  status,
  failureMessage,
  variation,
  draft,
  requiredFieldGaps,
  imageSource,
  target,
  onTargetChange,
  isFieldDisabled,
  sourceReadOnly = false,
  showGenerate,
  actionState,
  onUpdateDraft,
  onGenerate,
  onRevalidate,
  onAttest,
  reviewTagSuggestions = [],
  recentTags = [],
  onTagsChange,
  extraActions,
}: VariantReviewPanelProps) {
  const {
    isActionWorking,
    hasSaveFailed,
    hasConflict,
    sourceLocked,
    generateDisabledReason,
    generateHint,
    generateError,
    revalidating,
    revalidateError,
    attesting,
    attestError,
    revalidateDisabledReason,
    attestDisabledReason,
  } = actionState;
  const isSemanticFieldDisabled =
    isFieldDisabled || (target === "source" && (sourceLocked || sourceReadOnly));
  const variationNeedsValidation = variation?.status === "needs-validation";
  const stalePassReport =
    variationNeedsValidation && variation?.validation?.verdict === "pass";
  const failOverrideEligible =
    canAttestVariant({ variation }) && variation?.validation?.verdict === "fail";

  const currentTagSet = new Set(draft.tags ?? []);
  const visibleRecentTags = recentTags.filter((tag) => !currentTagSet.has(tag));

  const handleRecentTagClick = (tag: string) => {
    if (isFieldDisabled) return;
    const currentTags = draft.tags ?? [];
    if (currentTags.includes(tag)) return;
    onTagsChange([...currentTags, tag], currentTags);
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "12px",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <span data-testid="bulk-review-status">
            {statusLabel(status)}
          </span>
          {variation && (
            <span
              data-testid="bulk-review-variation-status"
              style={{ fontSize: "0.85em" }}
            >
              {variationStatusLabel(variation.status)}
            </span>
          )}
        <div
          data-testid="bulk-review-status-messages"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "4px",
            minHeight: "1.4em",
          }}
        >
          {hasConflict ? (
            <span
              data-testid="bulk-review-save-status"
              style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
            >
              Draft changed elsewhere. Copy your edits, then reload to resolve.
            </span>
          ) : hasSaveFailed && (
            <span
              data-testid="bulk-review-save-status"
              style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
            >
              Save failed, retrying...
            </span>
          )}
          {generateHint && (
            <span
              data-testid="bulk-review-generate-hint"
              style={{ fontSize: "0.85em", opacity: 0.8 }}
            >
              {generateHint}
            </span>
          )}
          {generateError && (
            <span
              data-testid="bulk-review-generate-error"
              style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
            >
              Generate failed: {generateError}
            </span>
          )}
          {revalidateError && (
            <span
              data-testid="bulk-review-revalidate-error"
              style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
            >
              Revalidate failed: {revalidateError}
            </span>
          )}
          {attestError && (
            <span
              data-testid="bulk-review-attest-error"
              style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
            >
              Attest failed: {attestError}
            </span>
          )}
        </div>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          {showGenerate && (
            <button
              type="button"
              data-testid="bulk-review-generate"
              onClick={onGenerate}
              disabled={generateDisabledReason !== ""}
              title={generateHint || undefined}
            >
              {variation?.status === "failed"
                ? "Generate Again"
                : "Generate variant"}
            </button>
          )}
          {variation?.status === "needs-validation" && (
            <button
              type="button"
              data-testid="bulk-review-revalidate"
              onClick={onRevalidate}
              disabled={revalidateDisabledReason !== "" || revalidating}
              title={revalidateDisabledReason || undefined}
            >
              {revalidating ? "Revalidating..." : "Revalidate"}
            </button>
          )}
          {stalePassReport && (
            <button
              type="button"
              data-testid="bulk-review-attest"
              onClick={onAttest}
              disabled={attestDisabledReason !== "" || attesting}
              title={
                attestDisabledReason ||
                "Keep the existing validation without revalidating"
              }
            >
              {attesting ? "Keeping..." : "Keep validation"}
            </button>
          )}
          {extraActions}
        </div>
      </div>

      {failureMessage && (
        <div
          data-testid="bulk-review-failure"
          style={{ color: "var(--color-error, #dc2626)", marginBottom: "12px" }}
        >
          {failureMessage}
        </div>
      )}

      {imageSource && (
        <img
          src={imageSource}
          alt="Crop preview"
          data-testid="bulk-review-preview"
          style={{
            maxWidth: "100%",
            maxHeight: "200px",
            marginBottom: "12px",
            border: "1px solid var(--color-border)",
          }}
        />
      )}

      {variation?.candidate && (
        <div
          data-testid="bulk-review-edit-target"
          role="group"
          aria-label="Editing target"
          style={{ display: "flex", gap: "8px", marginBottom: "12px" }}
        >
          <button
            type="button"
            data-testid="bulk-review-edit-candidate"
            aria-pressed={target === "candidate"}
            onClick={() => onTargetChange("candidate")}
            disabled={isFieldDisabled}
          >
            Edit candidate
          </button>
          <button
            type="button"
            data-testid="bulk-review-edit-source"
            aria-pressed={target === "source"}
            onClick={() => onTargetChange("source")}
            disabled={isFieldDisabled}
          >
            Edit source
          </button>
        </div>
      )}

      {target === "source" && variation?.original && (
        <div
          data-testid="bulk-review-source-invalidation-warning"
          style={{
            color: "var(--color-warning, #b45309)",
            fontSize: "0.9em",
            marginBottom: "12px",
          }}
        >
          Saving source changes invalidates the current variant and
          requires a new generation.
        </div>
      )}

      {stalePassReport && (
        <div
          data-testid="bulk-review-stale-validation"
          style={{
            color: "var(--color-warning, #b45309)",
            fontSize: "0.9em",
            marginBottom: "12px",
          }}
        >
          Validation covers a previous version of this candidate.
          Revalidate, or keep it if you accept the current version as-is.
        </div>
      )}
      {/* #671: an execution-only failure keeps its stored fail report
          visible alongside the Revalidate action. */}
      {(variation?.status === "failed" ||
        failOverrideEligible ||
        stalePassReport ||
        (variation?.status === "ready" && variation?.attestation) ||
        (variation?.status === "needs-validation" &&
          variation?.validation?.verdict === "fail")) && (
        <VariationFailureEvidence variation={variation} />
      )}

      {/* #658: fail-override attestation sits below the evidence panel
          so the failing checks the teacher is waving stay visible. */}
      {failOverrideEligible && variation && (
        <div style={{ marginBottom: "12px" }}>
          <button
            type="button"
            data-testid="bulk-review-attest-fail"
            onClick={onAttest}
            disabled={attestDisabledReason !== "" || attesting}
            title={
              attestDisabledReason ||
              "Accept the flagged failures and approve this variant"
            }
          >
            {attesting ? "Approving..." : "Override failures — approve anyway"}
          </button>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <label>
          Text
          <textarea
            data-testid="bulk-review-text"
            value={draft.text ?? ""}
            onChange={(event) => onUpdateDraft({ text: event.target.value })}
            disabled={isSemanticFieldDisabled}
            rows={4}
            style={{
              width: "100%",
              border: requiredFieldGaps.text
                ? ACTION_REQUIRED_BORDER
                : undefined,
            }}
          />
        </label>

        <div>
          <div
            style={{
              fontSize: "0.85em",
              fontWeight: 600,
              marginBottom: "6px",
            }}
          >
            Text preview
          </div>
          <div
            data-testid="bulk-review-text-preview"
            style={{
              border: "1px solid var(--color-border)",
              borderRadius: "6px",
              padding: "12px",
              minHeight: "64px",
              backgroundColor: "var(--color-surface-muted)",
            }}
          >
            <LatexText
              text={draft.text ?? ""}
              style={{ whiteSpace: "pre-wrap" }}
            />
          </div>
        </div>

        <div style={{ display: "flex", gap: "12px" }}>
          <label style={{ flex: 1 }}>
            Problem type
            <select
              data-testid="bulk-review-type"
              value={draft.problemType ?? "short-answer"}
              onChange={(event) =>
                onUpdateDraft({ problemType: event.target.value })
              }
              disabled={isSemanticFieldDisabled}
              style={{
                width: "100%",
                border: requiredFieldGaps.problemType
                  ? ACTION_REQUIRED_BORDER
                  : undefined,
              }}
            >
              {PROBLEM_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ flex: 1 }}>
            Subject
            <select
              data-testid="bulk-review-subject"
              value={draft.subject ?? "math"}
              onChange={(event) => onUpdateDraft({ subject: event.target.value })}
              disabled={isSemanticFieldDisabled || target === "candidate"}
              title={
                target === "candidate"
                  ? "Candidates share the source subject"
                  : undefined
              }
              style={{ width: "100%" }}
            >
              {SUBJECTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label>
          Correct answer
          <input
            type="text"
            data-testid="bulk-review-answer"
            value={draft.correctAnswer ?? ""}
            onChange={(event) =>
              onUpdateDraft({ correctAnswer: event.target.value })
            }
            disabled={isSemanticFieldDisabled}
            style={{
              width: "100%",
              border: requiredFieldGaps.correctAnswer
                ? ACTION_REQUIRED_BORDER
                : undefined,
            }}
          />
        </label>

        <label>
          Graph DSL
          <textarea
            data-testid="bulk-review-graphdsl"
            value={draft.graphDsl ?? ""}
            onChange={(event) => onUpdateDraft({ graphDsl: event.target.value })}
            disabled={isSemanticFieldDisabled}
            rows={10}
            style={{
              width: "100%",
              minHeight: "180px",
              resize: "vertical",
              fontFamily: "monospace",
              fontSize: "0.9em",
              lineHeight: 1.4,
            }}
          />
        </label>

        {draft.graphDsl?.trim() && (
          <div>
            <div
              style={{
                fontSize: "0.85em",
                fontWeight: 600,
                marginBottom: "6px",
              }}
            >
              Graph preview
            </div>
            <GraphSandbox dsl={draft.graphDsl} height={300} />
          </div>
        )}

        {visibleRecentTags.length > 0 && (
          <div
            data-testid="bulk-review-recent-tags"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "6px",
              alignItems: "center",
              marginBottom: "8px",
            }}
          >
            <span
              style={{
                fontSize: "0.85em",
                fontWeight: 600,
                color: "var(--color-text-muted)",
              }}
            >
              Recent tags:
            </span>
            {visibleRecentTags.map((tag) => (
              <button
                key={tag}
                type="button"
                data-testid={`bulk-review-recent-tag-${tag}`}
                disabled={isFieldDisabled}
                onClick={() => handleRecentTagClick(tag)}
                style={{
                  padding: "2px 8px",
                  border: "1px solid var(--color-border)",
                  borderRadius: "4px",
                  backgroundColor: "var(--color-surface)",
                  color: "var(--color-text)",
                  fontSize: "0.8em",
                  cursor: isFieldDisabled ? "not-allowed" : "pointer",
                }}
              >
                {tag}
              </button>
            ))}
          </div>
        )}

        <TagInput
          tags={draft.tags ?? []}
          onChange={(tags) => onTagsChange(tags, draft.tags ?? [])}
          suggestions={reviewTagSuggestions}
          placeholder="Add a tag..."
          disabled={isFieldDisabled}
          label="Tags"
          testId="bulk-review-tags"
        />
      </div>
    </div>
  );
}
