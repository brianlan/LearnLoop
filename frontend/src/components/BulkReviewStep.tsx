import { useCallback, useEffect, useMemo, useState } from "react";
import type { BulkBatch, BulkItem } from "@/types/bulkIngestion";
import {
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
import { VariantReviewPanel } from "./VariantReviewPanel";

const POLL_INTERVAL_MS = 2500;
const ACTION_REQUIRED_BORDER = "2px solid var(--color-error, #dc2626)";

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
    conflictedItemIds,
    hasPendingSaves,
    hasSaveFailures,
    hasConflicts,
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
  const actionState = actionStateFor(selectedItem, isEditable, isLoading);
  const isFieldDisabled = !isEditable || isLoading;
  const variation = selectedItem.variation;
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
  if (hasConflicts) {
    continueDisabledReasons.push("Draft changed elsewhere; copy your edits and reload");
  }
  const canContinue = continueDisabledReasons.length === 0;

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
                  {conflictedItemIds.has(item.itemId) && (
                    <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
                      {" "}
                      (conflict)
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <VariantReviewPanel
            status={selectedItem.status}
            failureMessage={selectedItem.extraction.failureMessage}
            variation={variation}
            draft={currentDraft}
            requiredFieldGaps={selectedRequiredFieldGaps}
            imageSource={previewUrl}
            target={activeTarget}
            onTargetChange={setEditTarget}
            isFieldDisabled={isFieldDisabled}
            showGenerate={isEditable && batch.ingestionMode !== "original"}
            actionState={actionState}
            onUpdateDraft={(changes) => updateDraft(selectedItem, activeTarget, changes)}
            onGenerate={() => handleGenerate(selectedItem)}
            onRevalidate={() => handleRevalidate(selectedItem)}
            onAttest={() => handleAttest(selectedItem)}
            reviewTagSuggestions={reviewTagSuggestions}
            recentTags={recentTags}
            onTagsChange={(nextTags, prevTags) =>
              handleTagsChange(selectedItem, activeTarget, nextTags, prevTags)
            }
            extraActions={
              <>
                {selectedItem.status === "failed" && (
                  <button
                    type="button"
                    data-testid="bulk-review-retry"
                    onClick={() => onRetry(selectedItem.itemId)}
                    disabled={actionState.isActionWorking}
                  >
                    Retry extraction
                  </button>
                )}
                {isDeleted ? (
                  <button
                    type="button"
                    data-testid="bulk-review-undo"
                    onClick={() => onUndoDelete(selectedItem.itemId)}
                    disabled={actionState.isActionWorking}
                  >
                    Undo delete
                  </button>
                ) : (
                  <button
                    type="button"
                    data-testid="bulk-review-delete"
                    onClick={() => onDelete(selectedItem.itemId)}
                    disabled={actionState.isActionWorking}
                  >
                    Delete
                  </button>
                )}
              </>
            }
          />
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
