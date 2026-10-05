import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  BulkBatch,
  BulkDraft,
  BulkItem,
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
  hasActiveVariantWork,
  statusLabel,
  variantPassGateReason,
  variationStatusLabel,
} from "./BulkReviewStep.helpers";
import {
  useBulkReviewEditing,
  type EditTarget,
  type ReviewEditingCallbacks,
} from "./BulkReviewStep.editing";

const POLL_INTERVAL_MS = 2500;
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

// Read-only structured failure evidence for a failed variant run (#613
// report payload). No accept/override/fallback controls exist here.
function VariationFailureEvidence({
  variation,
}: {
  variation: NonNullable<BulkItem["variation"]>;
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
            " Waved checks are listed below; the teacher accepted them as-is."}
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

export interface BulkReviewStepProps extends ReviewEditingCallbacks {
  batch: BulkBatch;
  isLoading: boolean;
  onRefresh: (batchId: string) => void | Promise<void>;
  onRetry: (itemId: string) => void | Promise<void>;
  onDelete: (itemId: string) => void | Promise<void>;
  onUndoDelete: (itemId: string) => void | Promise<void>;
  onContinue: () => void;
  tagSuggestions?: string[];
}

export function BulkReviewStep({
  batch,
  isLoading,
  onRefresh,
  onUpdateDraft,
  onGenerate,
  onRevalidate,
  onAttest,
  onRetry,
  onDelete,
  onUndoDelete,
  onContinue,
  tagSuggestions = [],
}: BulkReviewStepProps) {
  const items = useMemo(
    () => [...batch.items].sort((a, b) => a.order - b.order),
    [batch.items],
  );
  const [selectedItemId, setSelectedItemId] = useState<string>(() => {
    const firstActionable = items.find((item) => item.status !== "deleted");
    return firstActionable?.itemId ?? items[0]?.itemId ?? "";
  });
  const [editTarget, setEditTarget] = useState<EditTarget>("candidate");
  const [recentTags, setRecentTags] = useState<string[]>([]);

  const selectedItem = useMemo(
    () => items.find((item) => item.itemId === selectedItemId) || items[0],
    [items, selectedItemId],
  );
  const activeTarget: EditTarget = selectedItem?.variation?.candidate
    ? editTarget
    : "source";
  const {
    getDraft,
    updateDraft,
    handleGenerate,
    handleRevalidate,
    handleAttest,
    actionStateFor,
    failedItemIds,
    hasPendingSaves,
    hasSaveFailures,
  } = useBulkReviewEditing(items, {
    onUpdateDraft,
    onGenerate,
    onRevalidate,
    onAttest,
  });

  const reviewTagSuggestions = useMemo(() => {
    const seen = new Set<string>();
    const merged: string[] = [];

    const addTag = (tag: string) => {
      const trimmed = tag.trim();
      if (!trimmed || seen.has(trimmed)) return;
      seen.add(trimmed);
      merged.push(trimmed);
    };

    for (const tag of tagSuggestions) {
      addTag(tag);
    }
    for (const item of items) {
      for (const target of ["source", "candidate"] as const) {
        if (target === "candidate" && !item.variation?.candidate) continue;
        for (const tag of getDraft(item, target).tags ?? []) {
          addTag(tag);
        }
      }
    }

    return merged;
  }, [getDraft, items, tagSuggestions]);

  const handleTagsChange = useCallback(
    (
      item: BulkItem,
      target: EditTarget,
      nextTags: string[],
      prevTags: string[],
    ) => {
      const prevSet = new Set(prevTags);
      const added = nextTags.filter((tag) => !prevSet.has(tag));
      if (added.length > 0) {
        setRecentTags((prev) => {
          const next = [...prev];
          const seen = new Set(prev);
          for (const tag of added) {
            if (seen.has(tag)) {
              const index = next.indexOf(tag);
              if (index >= 0) next.splice(index, 1);
            } else {
              seen.add(tag);
            }
            next.unshift(tag);
          }
          return next.slice(0, 5);
        });
      }
      updateDraft(item, target, { tags: nextTags });
    },
    [updateDraft],
  );

  useEffect(() => {
    // Extraction and variant work both keep the batch being observed.
    const hasActiveWork = items.some(
      (item) =>
        item.status === "queued" ||
        item.status === "extracting" ||
        hasActiveVariantWork(item),
    );
    if (!hasActiveWork || batch.status !== "active") return;

    const id = window.setInterval(() => {
      onRefresh(batch.id);
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(id);
  }, [batch.id, batch.status, items, onRefresh]);

  const selectedIndex = items.findIndex(
    (item) => item.itemId === selectedItem?.itemId,
  );

  const navigate = useCallback(
    (direction: -1 | 1) => {
      const nextIndex = selectedIndex + direction;
      if (nextIndex >= 0 && nextIndex < items.length) {
        setSelectedItemId(items[nextIndex].itemId);
      }
    },
    [items, selectedIndex],
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey) return;
      if (event.key === "PageDown") {
        navigate(1);
        event.preventDefault();
      } else if (event.key === "PageUp") {
        navigate(-1);
        event.preventDefault();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  if (!selectedItem) {
    return (
      <div data-testid="bulk-wizard-review-step">
        <h2>Review extracted items</h2>
        <p>No items to review.</p>
      </div>
    );
  }

  const sourceImage = batch.images.find(
    (image) => image.imageId === selectedItem.imageId,
  );
  const previewUrl = selectedItem.crop?.mediaUrl || sourceImage?.sourceImage?.mediaUrl || "";
  const isDeleted = selectedItem.status === "deleted";
  const isEditable =
    !isDeleted &&
    selectedItem.status !== "queued" &&
    selectedItem.status !== "extracting" &&
    selectedItem.status !== "submitted";
  const currentDraft = getDraft(selectedItem, activeTarget);
  const {
    isActionWorking,
    hasSaveFailed,
    generateDisabledReason,
    generateHint,
    generateError,
    revalidating,
    revalidateError,
    attesting,
    attestError,
    revalidateDisabledReason,
    attestDisabledReason,
  } = actionStateFor(selectedItem, isEditable, isLoading);
  const isFieldDisabled = !isEditable || isLoading;
  const variation = selectedItem.variation;
  const variationNeedsValidation = variation?.status === "needs-validation";
  const stalePassReport =
    variationNeedsValidation && variation?.validation?.verdict === "pass";
  const failOverrideEligible =
    canAttestVariant(selectedItem) && variation?.validation?.verdict === "fail";
  const activeItems = items.filter((item) => item.status !== "deleted");
  const itemValidation = activeItems.map((item) => {
    const draft = getDraft(item, "source");
    const requiredFieldGaps = getRequiredFieldGaps(draft);
    // Submitted items are done; they must not block the remaining flow.
    if (item.status === "submitted") {
      return { itemId: item.itemId, reasons: [] as string[], requiredFieldGaps };
    }
    const reasons: string[] = [];
    if (item.status === "queued" || item.status === "extracting") {
      reasons.push(`Item ${item.order + 1}: Extraction is still running`);
    } else if (item.status === "failed") {
      reasons.push(`Item ${item.order + 1}: Extraction failed`);
    } else if (item.status !== "ready" && item.status !== "submit-failed") {
      reasons.push(`Item ${item.order + 1}: Item is not ready`);
    }
    if (!draft.text || draft.text.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Question text is required`);
    }
    if (!draft.problemType) {
      reasons.push(`Item ${item.order + 1}: Problem type is required`);
    }
    if (!draft.correctAnswer || draft.correctAnswer.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Correct answer is required`);
    }
    if (batch.ingestionMode !== "original") {
      // Only current-PASS candidates gate Continue.
      const variantReason = variantPassGateReason(item);
      if (variantReason) {
        reasons.push(`Item ${item.order + 1}: ${variantReason}`);
      }
    }
    return { itemId: item.itemId, reasons, requiredFieldGaps };
  });
  const itemValidationById = new Map(
    itemValidation.map((validation) => [validation.itemId, validation]),
  );
  const selectedValidation = itemValidationById.get(selectedItem.itemId);
  const selectedRequiredFieldGaps =
    selectedValidation?.requiredFieldGaps ?? getRequiredFieldGaps(currentDraft);
  const continueDisabledReasons = itemValidation.flatMap(
    (validation) => validation.reasons,
  );
  if (activeItems.length === 0) {
    continueDisabledReasons.push("No items to submit");
  }
  if (hasPendingSaves) {
    continueDisabledReasons.push("Draft changes are still saving");
  }
  if (hasSaveFailures) {
    continueDisabledReasons.push("Draft save failed, retrying");
  }
  const canContinue = continueDisabledReasons.length === 0;

  const currentTagSet = new Set(currentDraft.tags ?? []);
  const visibleRecentTags = recentTags.filter((tag) => !currentTagSet.has(tag));

  const handleRecentTagClick = (tag: string) => {
    if (isFieldDisabled) return;
    const currentTags = currentDraft.tags ?? [];
    if (currentTags.includes(tag)) return;
    handleTagsChange(selectedItem, activeTarget, [...currentTags, tag], currentTags);
  };

  return (
    <div data-testid="bulk-wizard-review-step">
      <h2>Review extracted items</h2>

      <div
        style={{
          display: "flex",
          gap: "16px",
          marginBottom: "16px",
        }}
      >
        <button
          type="button"
          data-testid="bulk-review-prev"
          onClick={() => navigate(-1)}
          disabled={selectedIndex <= 0}
        >
          Previous
        </button>
        <span data-testid="bulk-review-position">
          {selectedIndex + 1} / {items.length}
        </span>
        <button
          type="button"
          data-testid="bulk-review-next"
          onClick={() => navigate(1)}
          disabled={selectedIndex >= items.length - 1}
        >
          Next
        </button>
      </div>

      <div
        data-testid="bulk-review-layout"
        style={{
          display: "grid",
          gridTemplateColumns: "120px 1fr",
          gap: "16px",
        }}
      >
        <div>
          <h3>Items</h3>
          <ul data-testid="bulk-review-queue" style={{ padding: 0, listStyle: "none" }}>
            {items.map((item) => (
              <li key={item.itemId}>
                <button
                  type="button"
                  data-testid={`bulk-review-item-${item.itemId}`}
                  data-action-required={
                    (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                      ? "true"
                      : "false"
                  }
                  onClick={() => setSelectedItemId(item.itemId)}
                  disabled={item.itemId === selectedItem.itemId}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    border:
                      (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                        ? ACTION_REQUIRED_BORDER
                        : "2px solid transparent",
                    borderRadius: "6px",
                    background:
                      item.itemId === selectedItem.itemId
                        ? "var(--color-primary)"
                        : "transparent",
                    color:
                      item.itemId === selectedItem.itemId ? "white" : "inherit",
                  }}
                >
                  {item.order + 1}. {statusLabel(item.status)}
                  {item.variation && (
                    <span
                      data-testid={`bulk-review-item-variation-${item.itemId}`}
                      style={{ fontSize: "0.85em", opacity: 0.8 }}
                    >
                      {" "}
                      {variationStatusLabel(item.variation.status)}
                    </span>
                  )}
                  {failedItemIds.has(item.itemId) && (
                    <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
                      {" "}
                      (save failed)
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

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
                {statusLabel(selectedItem.status)}
              </span>
              {variation && (
                <span
                  data-testid="bulk-review-variation-status"
                  style={{ fontSize: "0.85em" }}
                >
                  {variationStatusLabel(variation.status)}
                </span>
              )}
              {hasSaveFailed && (
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
            <div style={{ display: "flex", gap: "8px" }}>
              {isEditable && batch.ingestionMode !== "original" && (
                <button
                  type="button"
                  data-testid="bulk-review-generate"
                  onClick={() => handleGenerate(selectedItem)}
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
                  onClick={() => handleRevalidate(selectedItem)}
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
                  onClick={() => handleAttest(selectedItem)}
                  disabled={attestDisabledReason !== "" || attesting}
                  title={
                    attestDisabledReason ||
                    "Keep the existing validation without revalidating"
                  }
                >
                  {attesting ? "Keeping..." : "Keep validation"}
                </button>
              )}
              {selectedItem.status === "failed" && (
                <button
                  type="button"
                  data-testid="bulk-review-retry"
                  onClick={() => onRetry(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Retry extraction
                </button>
              )}
              {isDeleted ? (
                <button
                  type="button"
                  data-testid="bulk-review-undo"
                  onClick={() => onUndoDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Undo delete
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="bulk-review-delete"
                  onClick={() => onDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Delete
                </button>
              )}
            </div>
          </div>

          {selectedItem.extraction.failureMessage && (
            <div
              data-testid="bulk-review-failure"
              style={{ color: "var(--color-error, #dc2626)", marginBottom: "12px" }}
            >
              {selectedItem.extraction.failureMessage}
            </div>
          )}

          {previewUrl && (
            <img
              src={previewUrl}
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

          {selectedItem.variation?.candidate && (
            <div
              data-testid="bulk-review-edit-target"
              role="group"
              aria-label="Editing target"
              style={{ display: "flex", gap: "8px", marginBottom: "12px" }}
            >
              <button
                type="button"
                data-testid="bulk-review-edit-candidate"
                aria-pressed={activeTarget === "candidate"}
                onClick={() => setEditTarget("candidate")}
                disabled={isFieldDisabled}
              >
                Edit candidate
              </button>
              <button
                type="button"
                data-testid="bulk-review-edit-source"
                aria-pressed={activeTarget === "source"}
                onClick={() => setEditTarget("source")}
                disabled={isFieldDisabled}
              >
                Edit source
              </button>
            </div>
          )}

          {activeTarget === "source" && variation?.original && (
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
          {(variation?.status === "failed" ||
            failOverrideEligible ||
            stalePassReport ||
            (variation?.status === "ready" && variation?.attestation)) && (
            <VariationFailureEvidence variation={variation} />
          )}

          {/* #658: fail-override attestation sits below the evidence panel
              so the failing checks the teacher is waving stay visible. */}
          {failOverrideEligible && variation && (
            <div style={{ marginBottom: "12px" }}>
              <button
                type="button"
                data-testid="bulk-review-attest-fail"
                onClick={() => handleAttest(selectedItem)}
                disabled={attestDisabledReason !== "" || attesting}
                title={
                  attestDisabledReason ||
                  "Accept the failed judgment checks and approve this variant"
                }
              >
                {attesting
                  ? "Approving..."
                  : "Override failed checks — approve anyway"}
              </button>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <label>
              Text
              <textarea
                data-testid="bulk-review-text"
                value={currentDraft.text ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem, activeTarget, { text: event.target.value })
                }
                disabled={isFieldDisabled}
                rows={4}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.text
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
                  text={currentDraft.text ?? ""}
                  style={{ whiteSpace: "pre-wrap" }}
                />
              </div>
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <label style={{ flex: 1 }}>
                Problem type
                <select
                  data-testid="bulk-review-type"
                  value={currentDraft.problemType ?? "short-answer"}
                  onChange={(event) =>
                    updateDraft(selectedItem, activeTarget, {
                      problemType: event.target.value,
                    })
                  }
                  disabled={isFieldDisabled}
                  style={{
                    width: "100%",
                    border: selectedRequiredFieldGaps.problemType
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
                  value={currentDraft.subject ?? "math"}
                  onChange={(event) =>
                    updateDraft(selectedItem, activeTarget, {
                      subject: event.target.value,
                    })
                  }
                  disabled={isFieldDisabled || activeTarget === "candidate"}
                  title={
                    activeTarget === "candidate"
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
                value={currentDraft.correctAnswer ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem, activeTarget, {
                    correctAnswer: event.target.value,
                  })
                }
                disabled={isFieldDisabled}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.correctAnswer
                    ? ACTION_REQUIRED_BORDER
                    : undefined,
                }}
              />
            </label>

            <label>
              Graph DSL
              <textarea
                data-testid="bulk-review-graphdsl"
                value={currentDraft.graphDsl ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem, activeTarget, {
                    graphDsl: event.target.value,
                  })
                }
                disabled={isFieldDisabled}
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

            {currentDraft.graphDsl?.trim() && (
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
                <GraphSandbox dsl={currentDraft.graphDsl} height={300} />
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
              tags={currentDraft.tags ?? []}
              onChange={(tags) =>
                handleTagsChange(selectedItem, activeTarget, tags, currentDraft.tags ?? [])
              }
              suggestions={reviewTagSuggestions}
              placeholder="Add a tag..."
              disabled={isFieldDisabled}
              label="Tags"
              testId="bulk-review-tags"
            />
          </div>
        </div>
      </div>

      <div style={{ marginTop: "16px", textAlign: "right" }}>
        <button
          type="button"
          data-testid="bulk-review-continue"
          onClick={onContinue}
          disabled={!canContinue || isLoading}
        >
          Continue to submit
        </button>
      </div>
    </div>
  );
}
